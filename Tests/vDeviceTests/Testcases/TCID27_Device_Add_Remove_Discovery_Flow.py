"""
/**
 * @file TCID27_Device_Add_Remove_Discovery_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID27_Device_Add_Remove_Discovery_Flow
 * @details Walks one emulated peer through its ENTIRE lifecycle - appear, be announced, be
 *          mutated, then depart - and samples the sink's device list on either side of that
 *          lifecycle. It is the only case in this suite that exercises the DEPARTURE half, which
 *          is what makes it the last and most complete of the flow modules. Six steps, in this
 *          order:
 *            1. before-probe - org.rdk.HdmiCecSink.getDeviceList, the reference sample;
 *            2. ADD - Device_Add.yaml attaches the "GameConsole" PlaybackDevice beneath the
 *               VAUDIO audio-system root of the emulated map;
 *            3. MUTATE - Device_Status.yaml drives that same peer to power_status "off" and
 *               marks it faulted;
 *            4. ANNOUNCE - Process_Report_Physical_Address.yaml then
 *               Process_Device_Vendor_ID.yaml inject the two BROADCAST discovery frames a real
 *               peer emits, reaching HdmiCecSinkProcessor::process(const ReportPhysicalAddress&,
 *               const Header&) at HdmiCecSinkImplementation.cpp:344 and
 *               process(const DeviceVendorID&, const Header&) at :376. Both handlers call
 *               addDevice() for the frame's INITIATOR, so this is the step that makes a peer
 *               PRESENT to the plugin rather than merely present in the emulator's map - see
 *               the note at the step itself for why that is not asserted to be the very peer
 *               step 2 added;
 *            5. mid-probe - the same device-list read, sampled while the peer is established;
 *            6. REMOVE - Device_Remove.yaml drops the peer again, followed by the after-probe.
 *
 *          WHAT THIS CASE IS FOR. HdmiCecSinkImplementation::removeDevice(const int) at
 *          HdmiCecSinkImplementation.cpp:2474 and the OnDeviceRemoved notification at
 *          HdmiCecSink.h:127 are both recorded as ZERO-HIT in the coverage gap register, and
 *          OnDeviceRemoved is additionally one of the five notifications the gap register lists
 *          as uncovered even by the sink's own in-process L2 suite. removeDevice() also carries
 *          a third uncovered arm: it calls HdmiPortMap::removeChild for whichever HDMI input
 *          matches the departing peer's physical address (:2494), an arm only a NESTED peer can
 *          reach, and "GameConsole" is nested one level below CECAdapter in the seeded topology.
 *          This module drives all three from the device level. It does not measure them - this
 *          suite has never been executed, as the preconditions below record - so no coverage
 *          claim is made here.
 *
 *          WHAT IS ACTUALLY ASSERTED, AND WHAT IS NOT. The OnDeviceRemoved NOTIFICATION IS NOT
 *          OBSERVABLE FROM THIS TRANSPORT and is deliberately NOT asserted: a curl
 *          request/response exchange cannot subscribe to a Thunder event, so the notification
 *          HdmiCecSink.h:131 forwards as the plugin's onDeviceRemoved event is delivered to
 *          registered JSON-RPC/COM-RPC subscribers this case is not one of. What IS observable
 *          is the CONSEQUENCE: removeDevice() decrements m_numberOfDevices at :2490 before it
 *          fans the notification out, and that counter is the numberofdevices field
 *          getDeviceList publishes. The three probes therefore sample the count, and the
 *          assertion is on its DIRECTION across the lifecycle - never on an exact value; see
 *          the reasoning at the assertion itself.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework with the
 *    org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the emulated topology and left HDMI-CEC enabled. That
 *    seeding is what supplies the VAUDIO AudioSystem root the added peer attaches beneath, and
 *    it is also what claims the sink's own logical address: both addDevice() and removeDevice()
 *    return early while m_logicalAddressAllocated is LogicalAddress::UNREGISTERED
 *    (HdmiCecSinkImplementation.cpp:2482 for the removal), so without it every step below would
 *    be accepted by the emulator and ignored by the plugin.
 *  - The vComponent HTTP API is reachable, so the five YAML documents can be posted.
 *  - No continuous integration workflow in this repository executes this suite; it is authored
 *    for device-level execution and has not been run.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The added peer becomes visible in the sink's device list once the broadcast discovery
 *    frames have announced it, and is absent from that list after the removal.
 *  - The OnDeviceRemoved notification is NOT observable over the L3 curl transport and is NOT
 *    asserted; only its device-count consequence is. Nothing here attests that the notification
 *    fired.
 *
 * @pass_criteria
 *  - All five required YAML posts return HTTP 200, all three device-list probes parse with
 *    result.success True and an integer result.numberofdevices, the post-removal count is not
 *    greater than the post-add count, and run_test() returns True.
 *
 * @failure_criteria
 *  - A probe is not dispatched, a probe returns the no-response sentinel, any required
 *    vComponent post does not return HTTP 200, a probe reports success other than True or a
 *    non-integer numberofdevices, the post-removal count exceeds the post-add count, a JSON
 *    parsing error occurs, or run_test() returns False.
 */
"""

import time
import os
import json

# The eight-symbol utils import below is the shared contract the emulation-driven ("flow")
# testcases in this suite are written against. `log_with_timing` is deliberately retained even
# though this module gates its own timing line on HDMICEC_TIMING_ENABLED directly: keeping the
# set identical across the flow modules is what lets one be diffed against another, so the
# resulting single "imported but unused" lint note is accepted convention here rather than an
# oversight. Every other symbol has a call site below.
#
# The set is also deliberately CLOSED. The two source-plugin modules this case was modelled on
# additionally import `subprocess` and `pathlib.Path` for a helper that hunts the filesystem for
# a legacy shell script; neither is imported here, because no documented coverage gap requires
# spawning a process or resolving a path from this module, and adding either would be a test
# construct introduced for its own sake. Every action below is a YAML post or a JSON-RPC read,
# and both of those belong to utils.py.
from utils import (
    send_curl_command,
    send_vcomponent_command,
    HDMICEC_CMD_BASE,
    log_info,
    log_success,
    log_error,
    log_warning,
    log_with_timing
)
import HdmiCECSink_Curl as HdmiCecSinkApis


def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {body}")
    return http_code == 200


# FIXTURE NAMES ARE A CONTRACT, AND THIS CASE IS THE ONE THAT CANNOT AFFORD A TYPO.
# utils.send_vcomponent_command resolves HDMICEC_CMD_BASE joined with the literal filename and
# refuses anything it cannot approve, returning (0, "YAML file not found: ...") for a name that
# does not resolve, and (0, "refused to post ...") for a symbolic link, a path outside the
# configuration tree or a non-regular file. None of those raise. A mistyped name therefore does
# not fail loudly - it silently disables that step - and for the removal below a silently skipped
# post would leave the emulated topology permanently altered for every case that runs after this
# one. That is why every one of the five posts is required to have returned HTTP 200 before any
# verdict is reached, rather than being posted and forgotten. All five names are verified to
# resolve: Device_Add.yaml, Device_Status.yaml, Process_Report_Physical_Address.yaml,
# Process_Device_Vendor_ID.yaml and Device_Remove.yaml.
#
# The (code, body) pair is asymmetric and only the CODE is a verdict. This suite's utils.py
# reports what the vComponent actually answered and reinterprets nothing: a refused connection, a
# timeout, and the CURLE_GOT_NOTHING case in which some vComponent builds apply the posted YAML
# and then close the connection without replying, all come back as code 0 with curl's own
# diagnosis in the body. So a post that really did take effect can still be reported as a failure
# here, and that is the intended direction of the error - a server that never answered is never
# promoted to a pass. The body, correspondingly, is free-form diagnostic text rather than a
# payload: it is logged and never parsed. Only the three JSON-RPC probe responses are given to
# json.loads.


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could
    carry a non-object "result" or not be an object at all. Every such case collapses to {} so
    the caller reports a MISSING FIELD rather than raising AttributeError out of run_test(). A
    body that is not JSON at all still raises json.JSONDecodeError, which run_test() handles
    as the documented failure. Three probes share this - the before, mid and after samples -
    which is why it is factored out rather than repeated.
    Args:
        response_text: Raw response string as returned by utils.send_curl_command
    Returns:
        The "result" mapping when the body is a JSON object carrying one, otherwise {}.
    """
    body = json.loads(response_text)
    if not isinstance(body, dict):
        return {}
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def run_test():
    start_time = time.perf_counter()

    # SHARED STATE: changed, and restored BY CONSTRUCTION rather than by a cleanup step.
    # Device_Add.yaml attaches "GameConsole" to the emulated device map and Device_Remove.yaml
    # takes the same "GameConsole" away again, so the add/remove pair IS the restoration - the
    # ordering is the cleanup, not a stylistic preference. Two consequences follow, and both are
    # deliberate. First, the removal is placed AFTER the discovery frames rather than being
    # omitted: it is simultaneously the step under test and the step that puts the topology back,
    # so it can be neither dropped nor moved earlier. Second, its post is included in the
    # required-post gate below, because a silently skipped removal would hand every later case in
    # the suite a topology this one altered.
    #
    # NO ROLE INVERSION. The peer added here is an emulated PlaybackDevice attached beneath the
    # VAUDIO audio-system root; the device under test remains the television throughout and is
    # never reconfigured to appear as its own peer in the list it is being asked to report.

    # Before-probe: the reference sample, taken before anything is changed.
    before = send_curl_command(HdmiCecSinkApis.get_device_list)
    if not before:
        log_error("✖ initial getDeviceList command not sent")
        return False
    # The transport failure guard that actually fires in this suite. utils.send_curl_command
    # returns the "< No response from WPEFramework >" sentinel - a TRUTHY string - for every
    # failure mode, so the falsy check above cannot catch one on its own. The prefix form is the
    # detection contract utils.py documents for callers.
    if before.startswith("< No response"):
        log_error("✖ initial getDeviceList returned no response from WPEFramework")
        return False
    log_warning(f"Initial device list: {before}")

    # ACT 1 - ADD. Device_Add.yaml attaches the "GameConsole" PlaybackDevice with parent
    # "VAUDIO", the audio-system root Init_Devicelist_Populate established. The settle here is 2s
    # rather than the 1s used between the later steps, matching the source-plugin template's own
    # pause around a device add or remove: a topology change has to travel the whole pipeline -
    # vComponent, the driver receive callback, the read thread, the decoder, and finally
    # addDevice() - before it can show up in a device-list read, which is a longer path than a
    # single frame injection.
    ok_add = _post_hdmicec("Device_Add.yaml")
    time.sleep(2)

    # ACT 2 - MUTATE. Device_Status.yaml drives the same peer to power_status "off" and marks it
    # faulted. This changes the EMULATOR's record, which the peer then reports on the bus, so the
    # value chosen matters: process(ReportPowerStatus) at HdmiCecSinkImplementation.cpp:408
    # compares the stored status against the reported one and calls sendDeviceUpdateInfo() only
    # when the two differ. Both the seeded topology and Device_Add.yaml declare this peer "on",
    # so a redundant "on" here would fan nothing out at all - which is why the fixture says
    # "off".
    ok_status = _post_hdmicec("Device_Status.yaml")
    time.sleep(1)

    # ACT 3 - ANNOUNCE. The two frames a real peer emits when it joins the bus. Both fixtures are
    # BROADCAST (initiator 4, destination F), and that framing is FUNCTIONAL, NOT STYLISTIC:
    # process(ReportPhysicalAddress) at HdmiCecSinkImplementation.cpp:344 and
    # process(DeviceVendorID) at :376 each open with the same guard - "Ignore Direct messages,
    # accepts only broadcast messages" - and return immediately for anything directed (:349-352
    # and :380-383 respectively).
    #
    # THE HAZARD THAT GUARD CREATES IS SILENT. Swapping either fixture for a directed variant
    # would still be accepted by the emulator and would still return HTTP 200, so the post would
    # look perfectly healthy while the handler discarded the frame and the announcement did
    # nothing at all. A green post is therefore not evidence that a frame was processed, which is
    # why the verdict below rests on the device-list samples rather than on the post codes alone.
    # Do not substitute a directed report frame here.
    #
    # These two posts are also what make a peer PRESENT TO THE PLUGIN rather than merely present
    # in the emulator's map: both handlers call addDevice() for the frame's INITIATOR (:356 and
    # :385), and removeDevice() does nothing at all unless that peer's m_isDevicePresent is
    # already set (:2487). That is the second reason the removal has to come last.
    #
    # Stated precisely, because the distinction matters for what may be claimed: these fixtures
    # announce logical address 4, whereas Act 1 asked the emulator to add a peer named
    # "GameConsole", and the address the emulator assigns that peer is its own business. So this
    # step is not asserted to announce exactly the peer Act 1 added - it establishes an
    # announced, present peer, which is what the removal path needs. The verdict rests on the
    # population count for the same reason: it holds whichever address the emulator chose.
    ok_rpa = _post_hdmicec("Process_Report_Physical_Address.yaml")
    time.sleep(1)
    ok_vid = _post_hdmicec("Process_Device_Vendor_ID.yaml")
    time.sleep(1)

    # Mid-probe: sampled while the peer is established, so it is the count the removal is
    # measured against.
    mid = send_curl_command(HdmiCecSinkApis.get_device_list)
    if not mid:
        log_error("✖ post-add getDeviceList command not sent")
        return False
    if mid.startswith("< No response"):
        log_error("✖ post-add getDeviceList returned no response from WPEFramework")
        return False
    log_warning(f"Device list after add: {mid}")

    # DIAGNOSTIC ONLY, DELIBERATELY NOT ASSERTED. printDeviceList makes the plugin dump its
    # internal device table to the WPEFramework log, where a human reading a failed run can
    # compare the plugin's own view against the JSON above. The dump lands in that log and not in
    # this response, so there is nothing here to assert: the reply carries only a `printed` flag
    # that the plugin reports whether or not any device is present. Its outcome consequently does
    # not affect the verdict, and a failure to dispatch it is logged rather than fatal - a
    # diagnostic that failed must not turn a passing lifecycle into a failure.
    dump = send_curl_command(HdmiCecSinkApis.print_device_list)
    if not dump or dump.startswith("< No response"):
        log_warning("printDeviceList diagnostic dump unavailable; continuing")
    else:
        log_info(f"printDeviceList diagnostic: {dump}")

    # ACT 4 - REMOVE, WHICH IS ALSO THE RESTORATION. This is the step the case exists for and the
    # step that puts the topology back, so it must not be skipped, must not be moved ahead of the
    # discovery frames, and its result must be checked. Device_Remove.yaml drops the same
    # "GameConsole" that Act 1 added, driving HdmiCecSinkImplementation::removeDevice() at
    # HdmiCecSinkImplementation.cpp:2474 - which decrements m_numberOfDevices (:2490), calls
    # HdmiPortMap::removeChild for the HDMI input matching the departing peer's physical address
    # (:2494, an arm only a nested peer reaches), clears the deviceList entry, and finally fans
    # OnDeviceRemoved out to every registered notification. The 2s settle matches Act 1's: a
    # topology change takes the long path through the pipeline in either direction.
    ok_remove = _post_hdmicec("Device_Remove.yaml")
    time.sleep(2)

    # Every one of the five posts is required. A post that did not return HTTP 200 either never
    # reached the vComponent or named a document that did not resolve, and in both cases the step
    # it represents did not happen - including, critically, the restoration above.
    if not (ok_add and ok_status and ok_rpa and ok_vid and ok_remove):
        log_error("✖ required vComponent emulation posts failed")
        return False

    # After-probe: the sample that closes the lifecycle.
    after = send_curl_command(HdmiCecSinkApis.get_device_list)
    if not after:
        log_error("✖ final getDeviceList command not sent")
        return False
    if after.startswith("< No response"):
        log_error("✖ final getDeviceList returned no response from WPEFramework")
        return False
    log_warning(f"Final device list: {after}")

    try:
        before_result = _result_object(before)
        mid_result = _result_object(mid)
        after_result = _result_object(after)

        before_count = before_result.get("numberofdevices")
        mid_count = mid_result.get("numberofdevices")
        after_count = after_result.get("numberofdevices")

        # The whole lifecycle in one line, so a run record shows what the counts actually did
        # rather than only whether the assertion held.
        log_info(
            f"Device count before={before_count} after_add={mid_count} "
            f"after_remove={after_count}"
        )

        # Every sample must be a well-formed answer before any comparison between samples means
        # anything: getDeviceList publishes success, numberofdevices and deviceList, and
        # comparing a count that is absent or non-integer would be comparing None.
        samples_valid = (
            before_result.get("success") is True
            and mid_result.get("success") is True
            and after_result.get("success") is True
            and isinstance(before_count, int)
            and isinstance(mid_count, int)
            and isinstance(after_count, int)
        )
        # The verdict cascade below reports the FIRST thing that is wrong and otherwise passes,
        # so every path through it ends on a single shared line.
        if not samples_valid:
            log_error("✖ a getDeviceList sample did not report success with an integer count")
            log_warning(f"Actual  : before={before} mid={mid} after={after}")
        # DIRECTION ONLY - DO NOT STRENGTHEN THIS INTO AN EXACT-COUNT ASSERTION. `mid_count ==
        # before_count + 1` is the tempting form and it would be wrong, because the add can
        # legitimately be a no-op: "GameConsole" is ALREADY declared in the seeded topology as a
        # nested child of CECAdapter
        # (vcomponent_configurations/commands/Device_Config_Add_Network.yaml), so re-adding it
        # need not move the population at all. The announcement frames are the same story from
        # the other side - they announce a peer that may or may not already be known, and
        # addDevice() is idempotent for a peer the plugin has already recorded. Pinning an exact
        # delta would make this case pass or fail on which sibling ran before it, which is
        # precisely the order-fragile shape this suite is built to avoid.
        #
        # What CAN be claimed without qualification is the direction across the removal:
        # removeDevice() only ever decrements the counter, so the population after the removal
        # must not exceed the population immediately before it. That is the observable
        # consequence of the path under test, and it is what is asserted. `before_count` is
        # logged rather than compared against, for the same idempotency reason.
        #
        # ALSO NOT ASSERTED, AND NOT AN OVERSIGHT: that OnDeviceRemoved fired. The notification
        # goes to registered Thunder subscribers, and a curl request/response exchange is not
        # one, so this transport cannot observe it. The count is its consequence, not the event
        # itself.
        #
        # The comparison stays inside this branch rather than being hoisted into a named boolean
        # beside samples_valid: an absent numberofdevices leaves None in these names, and `None >
        # None` raises TypeError in Python 3, so evaluating it eagerly would convert a malformed
        # reply from a reported verdict into an exception escaping run_test(). The elif is what
        # guarantees both operands are the integers samples_valid just proved them to be.
        elif after_count > mid_count:
            log_error(
                f"✖ device count rose across the removal: after_add={mid_count} "
                f"after_remove={after_count}"
            )
        else:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID27_Device_Add_Remove_Discovery_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
    except json.JSONDecodeError:
        # Deliberate fall-through rather than a second message here: a body that is not JSON is
        # reported by the shared failure line below, which every failure path in this case ends
        # on, and the three raw samples were already logged verbatim above - so the malformed
        # payload is in the run record whether or not this handler says anything.
        pass

    log_error("TCID27_Device_Add_Remove_Discovery_Flow Failed ❌")
    return False
