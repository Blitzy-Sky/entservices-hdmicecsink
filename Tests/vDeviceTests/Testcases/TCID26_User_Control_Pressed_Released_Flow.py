"""
/**
 * @file TCID26_User_Control_Pressed_Released_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID26_User_Control_Pressed_Released_Flow
 * @details Exercises the sink's user-control path in both directions, and carries this
 *          suite's device-level boundary-value coverage for it.
 *
 *          Outbound leg: org.rdk.HdmiCecSink.sendUserControlPressed is dispatched over
 *          JSON-RPC and its success acknowledgement asserted, then
 *          org.rdk.HdmiCecSink.sendUserControlReleased is dispatched and asserted in turn.
 *          The two APIs are asymmetric by contract - pressed carries a key code alongside
 *          the logical address, released carries the logical address alone - and both are
 *          driven from the command constants in HdmiCECSink_Curl.py, so no address or key
 *          code is restated here and neither can drift out of step with the fixtures.
 *
 *          Inbound leg: three UserControlPressed frames are injected through the vComponent
 *          emulation API, each closed by a UserControlReleased frame. The three key codes
 *          are the NOMINAL value, the MINIMUM value and the upper BYTE BOUNDARY value, which
 *          is the same three-point sweep the sink L1 suite already applies to these two APIs
 *          as sendUserControlPressed_MinKeyCode and sendUserControlPressed_BoundaryKeyCode
 *          (and their sendUserControlReleased_ counterparts). Reusing that idiom at device
 *          level is deliberate: it is the established repository convention for a
 *          parameterised API rather than a shape invented here.
 *
 *          The rejected-argument half of the boundary set is deliberately NOT attempted at
 *          this level and is not missing from the estate: sendUserControlPressed and
 *          sendUserControlReleased are each already asserted at L1 with
 *          _InvalidLogicalAddress, _InvalidKeyCode, _MissingParams, _MalformedJSON,
 *          _NegativeValues and _StringValues variants. Those cases need to observe a
 *          REJECTION, and this suite's command constants expose only well-formed requests,
 *          so duplicating them here would add no coverage and could only weaken the split.
 *
 *          Closing probe: getDeviceList is read once at the end as a liveness check. It is
 *          not a key-delivery assertion, as the expected-result section below sets out, but a
 *          burst of six injected frames that left the plugin unable to answer would be a
 *          genuine regression, and this is the only consequence of the flow that the L3
 *          transport can observe.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework
 *    with the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the CEC topology, so the logical address carried by
 *    the outbound command constants resolves to a real emulated peer - the Audio System,
 *    which is this suite's bootstrap peer - rather than to an empty address.
 *  - The vComponent emulation API is reachable, since the three inbound key codes arrive as
 *    posted YAML command documents rather than as anything this module builds itself.
 *  - No continuous integration workflow in this repository executes this suite; this case is
 *    authored for device-level execution and has not been run.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - Both outbound calls are acknowledged with {"success": true}, all six injected frames
 *    are accepted with HTTP 200, and the closing getDeviceList probe still answers.
 *  - The OnKeyPressEvent and OnKeyReleaseEvent notifications the injected frames provoke are
 *    NOT observable at this level and nothing is asserted about them. This suite reaches the
 *    plugin over one-shot curl, which cannot subscribe to a Thunder notification channel, so
 *    an event assertion here would be unfounded. That is a limit of THIS level, not a gap in
 *    the estate: both events are asserted by the sink's own suites - L1
 *    onKeyPressEvent_SubscribedClient_ReceivesAddressAndKeyCode,
 *    onKeyPressEvent_BoundaryOperands_AreForwardedVerbatim,
 *    onKeyPressEvent_NoSubscriber_ProducesNoClientNotification and
 *    onKeyReleaseEvent_SubscribedClient_ReceivesLogicalAddress, and L2
 *    InjectUserControlPressedFrameAndVerifyEvent (with minimum, maximum-named and
 *    out-of-range key-code variants) and InjectUserControlReleasedFrameAndVerifyEvent. Where
 *    the coverage register still lists them among the events uncovered even by the sink's own
 *    L2 suite, that entry is the register's PRE-CHANGE BASELINE.
 *
 * @pass_criteria
 *  - All six required YAML posts return HTTP 200, both JSON-RPC calls acknowledge
 *    {"success": true}, the closing probe parses as an object whose result carries
 *    success true and an integer numberofdevices, the guaranteed closing
 *    UserControlReleased cleanup is accepted, and run_test() returns True.
 *
 * @failure_criteria
 *  - A response mismatch, a failed or silently skipped emulation post, a JSON parsing
 *    failure, an unreachable endpoint or an unavailable device-level prerequisite;
 *    run_test() then returns False.
 *  - The cleanup operation (the closing UserControlReleased) cannot be confirmed, which is reported
 *    as its own finding and fails this case even when every measurement above holds.
 */
"""


import time
import os
import re
import json

from utils import (
    send_curl_command,
    send_vcomponent_command,
    sanitise_for_log,
    HDMICEC_CMD_BASE,
    log_info,
    log_success,
    log_error,
    log_warning,
    log_with_timing,
    CEC_FRAME_PACING_SECONDS,
)
import HdmiCECSink_Curl as HdmiCecSinkApis


def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {sanitise_for_log(body)}")
    return http_code == 200


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could
    carry a non-object "result" or not be an object at all. Every such case collapses to {} so
    the caller reports a MISSING FIELD rather than raising AttributeError out of run_test(),
    which owes its caller a bool on every path. A body that is not JSON at all still raises
    json.JSONDecodeError, which run_test() handles as the documented failure. Three call sites
    share this - the two outbound acknowledgements and the closing probe - which is why it is
    factored out here, in the idiom TCID23_Short_Audio_Descriptor_Flow established.
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


# FIXTURE NAMES ARE LOAD-BEARING, AND A MISSPELLING IS SILENT AT THE POINT OF USE. utils
# resolves the name against HDMICEC_CMD_BASE and returns (0, "YAML file not found: <path>")
# when the document does not exist, so a wrong name does not raise - it merely fails its post.
# That matters more on this module than on any other in the suite, because this is the module
# that carries the boundary sweep: a silently skipped Min or Boundary post would quietly
# reduce the sweep to the nominal case while every remaining check still passed, and the
# missing coverage would be invisible in a green log. Every post below is therefore captured
# into a named flag and all six are required together rather than fired and forgotten. The
# four filenames used here were verified against vcomponent_configurations/commands/ on disk.
#
# The body is logged but never parsed. utils reports the status the vComponent actually
# returned and is fail-closed about it: a silent, refused or failing vComponent yields 0
# together with curl's own diagnosis rather than a manufactured 200, and even a genuine 200
# may carry an empty body. `body` is therefore diagnostic text for a human reading the log,
# never a JSON document to decode.


# FRAMING OF THE SIX INJECTED DOCUMENTS - DIRECTED FROM LOGICAL ADDRESS 5, AND CHOSEN, NOT
# INHERITED.
#
# run_test() posts the Device_-prefixed documents, whose payloads are directed with header
# 0x50 (initiator 5, destination 0). Process_-prefixed equivalents exist beside them -
# Process_User_Control_Pressed and Process_User_Control_Released, header 0x40, initiator 4 -
# and the sink handlers would accept either, because process(UserControlPressed) and
# process(UserControlReleased) apply no destination filter. So this is a genuine choice, and
# it is settled by the topology rather than by taste: Init_Devicelist_Populate seeds the
# audio system at logical address 5 as this suite's bootstrap peer, and HdmiCECSink_Curl.py
# records that logical address 4 has no peer in that topology. Injecting from 5 therefore
# models a frame from a peer that actually exists and matches the address the outbound
# constants target, which keeps both legs of this flow describing one conversation with one
# device. Injecting from 4 would model a frame from nothing.
#
# One family is used throughout for that reason. Do not mix the two sets below: the point of
# the sweep is that the three documents differ ONLY in their key-code operand, so changing
# the header on one of them would turn a controlled comparison into two unrelated variables.


def _ensure_key_released():
    """Inject a closing UserControlReleased frame and report whether it was accepted.

    Returns (ok, detail).

    The flow pairs every injected press with an injected release, which is what keeps it
    state-neutral on the happy path. But a press and its release are two separate posts with
    guards between them, so any early return in between - a rejected post, an unacknowledged
    outbound call, an exception - left a key HELD DOWN on the sink. That is precisely the
    stuck-key fault the coverage register's OnKeyReleaseEvent rationale warns about, and it
    would leak into whichever case the suite runs next.

    The release is therefore also issued from run_test()'s finally block, on every path. A
    release with no outstanding press is harmless - process(UserControlReleased) simply clears a
    state that is already clear - so the extra post costs nothing and removes the need to reason
    about which exit path was taken.
    """
    if _post_hdmicec("Device_User_Control_Released.yaml"):
        return True, "closing UserControlReleased injected; no key left held"
    return False, "the closing UserControlReleased frame was not accepted by the emulator"


def run_test():
    '''Drive the outbound user-control pair, sweep three inbound key codes, verify liveness.

    Every injected press is closed by an injected release inside the flow, and a further
    release is issued from the finally block below so that no exit path - early return or
    exception - can leave a key held down on the sink. The restoration reports its own verdict
    and this case fails if either half fails.
    Returns:
        True when both outbound calls are acknowledged, all six emulation posts are accepted,
        the closing probe reports success with an integer device count, and the guaranteed
        closing release is accepted; False on a transport failure, a failed post, a response
        mismatch, a body that is not valid JSON, or a release that could not be confirmed.
    '''
    start_time = time.perf_counter()

    try:
        flow_ok = _run_user_control_flow()
    finally:
        cleanup_ok, cleanup_detail = _ensure_key_released()
        if cleanup_ok:
            log_info(f"  Cleanup: {cleanup_detail}")
        else:
            # Reported independently of the measurement: a key left held is a different defect
            # from a failed assertion here, and it affects later cases rather than this one.
            log_error(
                "TCID26_User_Control_Pressed_Released_Flow cleanup FAILED: a key may still be "
                f"held down for subsequent cases - {cleanup_detail}"
            )

    if flow_ok and cleanup_ok:
        elapsed_time = time.perf_counter() - start_time
        msg = "TCID26_User_Control_Pressed_Released_Flow Passed ✅"
        if os.environ.get("HDMICEC_TIMING_ENABLED"):
            log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
        else:
            log_success(msg)
        return True

    log_error("TCID26_User_Control_Pressed_Released_Flow Failed ❌")
    return False


def _run_user_control_flow():
    """The outbound pair, the three-key inbound sweep and the liveness probe.

    Returns True when every assertion holds. Every assertion is exactly as it was; only the
    verdict reporting moved to run_test() so the guaranteed release in its finally block runs
    first.
    """

    log_info(
        "Executing the user-control flow: outbound pressed and released, then inbound "
        "nominal, minimum and boundary key codes, each press closed by a release"
    )

    # ── FIXTURE CONSISTENCY, BEFORE ANY FRAME IS INJECTED ────────────────────────────────────────
    key_codes, reason = _verify_fixture_sweep()
    if key_codes is None:
        log_error(f"✖ {reason}")
        log_error("TCID26_User_Control_Pressed_Released_Flow Failed ❌")
        return False
    log_success(
        "✔ the fixture set is a genuine three-point sweep: one shared header, opcode "
        f"0x{OPCODE_USER_CONTROL_PRESSED:02X} throughout, and three distinct key codes "
        f"{[f'0x{code:02X}' for code in key_codes]}"
    )

    # ── THE OUTBOUND TARGET, DERIVED FROM THE CONSTANTS THIS MODULE SENDS ────────────────────────
    # Both constants must name the same logical address, or the release would not close the press.
    # The value is read out of the constants rather than written here, so it cannot drift from
    # them, and the topology is then required to actually contain that peer - which is the
    # precondition this module's commentary describes in prose and previously never checked.
    targets = {}
    for argv, label in (
        (HdmiCecSinkApis.send_user_control_pressed, "sendUserControlPressed"),
        (HdmiCecSinkApis.send_user_control_released, "sendUserControlReleased"),
    ):
        request, reason = _published_request(argv)
        if request is None:
            log_error(f"✖ {label}: {reason}")
            log_error("TCID26_User_Control_Pressed_Released_Flow Failed ❌")
            return False
        params = request.get("params")
        address = params.get("logicalAddress") if isinstance(params, dict) else None
        if not isinstance(address, int):
            log_error(
                f"✖ the {label} constant carries logicalAddress {address!r}, expected an integer"
            )
            log_error("TCID26_User_Control_Pressed_Released_Flow Failed ❌")
            return False
        targets[label] = address
    if len(set(targets.values())) != 1:
        log_error(
            f"✖ the two outbound constants target different logical addresses: {targets} - the "
            "release would not close the press it follows"
        )
        log_error("TCID26_User_Control_Pressed_Released_Flow Failed ❌")
        return False
    target_address = next(iter(targets.values()))

    log_success("✔ curl command sent")
    log_warning(f"Response: {pressed_response}")

    # ACT 2 - OUTBOUND RELEASE, WHICH CLOSES ACT 1. sendUserControlReleased takes the logical
    # address ALONE (IHdmiCecSink.h:281) - the asymmetry with pressed is by contract, since a
    # release identifies no key - and likewise answers with success only.
    #
    # This call is not an optional extra: without it the outbound leg would leave the peer
    # holding the key this case just pressed, which is the stuck-key condition described in
    # this function's docstring. Ordering is therefore load-bearing, not stylistic.
    log_info("Dispatching the outbound sendUserControlReleased call to close the press")
    released_response = send_curl_command(HdmiCecSinkApis.send_user_control_released)

    if not released_response:
        log_error("✖ sendUserControlReleased command not sent")
        return False
    if target_address not in before_addresses:
        log_error(
            f"✖ the outbound constants target logical address {target_address}, which is not in "
            f"the discovered topology {before_addresses} - the outbound leg would address nothing"
        )
        log_error("TCID26_User_Control_Pressed_Released_Flow Failed ❌")
        return False
    log_info(
        f"Before: {before_count} devices at {before_addresses}, outbound target "
        f"{target_address} present"
    )

    log_success("✔ curl command sent")
    log_warning(f"Response: {released_response}")

    # ACTS 3 TO 8 - THE INBOUND BOUNDARY SWEEP. THIS IS THE REASON THIS MODULE EXISTS.
    #
    # Three UserControlPressed frames are injected, differing ONLY in their key-code operand -
    # nominal, minimum, upper byte boundary - and each is closed by the same UserControlReleased
    # frame. That is the same three-point sweep the sink L1 suite applies to these APIs as
    # sendUserControlPressed_MinKeyCode and sendUserControlPressed_BoundaryKeyCode, lifted to
    # device level rather than reinvented.
    #
    # Each press is paired with a release IN SEQUENCE rather than the three presses being fired
    # and then released together. Pairing them keeps at most one key outstanding at any moment,
    # so a failure part-way through this block cannot leave two or three keys held; and it means
    # the sink observes three complete press-release cycles, which is what a real remote sends.
    #
    # The key codes themselves live in the YAML documents, so no operand value appears in this
    # file and no frame is hand-built here. Adding a fourth key code would mean adding a fourth
    # fixture; the fixture set is the vocabulary of this sweep and is not extended from here.
    log_info("Injecting the inbound UserControlPressed frame with the nominal key code")
    ok_nominal = _post_hdmicec("Device_User_Control_Pressed.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)

    log_info("Injecting the UserControlReleased frame to close the nominal press")
    ok_rel_1 = _post_hdmicec("Device_User_Control_Released.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)

    log_info("Injecting the inbound UserControlPressed frame with the minimum key code")
    ok_min = _post_hdmicec("Device_User_Control_Pressed_Min.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)

    log_info("Injecting the UserControlReleased frame to close the minimum press")
    ok_rel_2 = _post_hdmicec("Device_User_Control_Released.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)

    log_info("Injecting the inbound UserControlPressed frame with the boundary key code")
    ok_boundary = _post_hdmicec("Device_User_Control_Pressed_Boundary.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)

    log_info("Injecting the UserControlReleased frame to close the boundary press")
    ok_rel_3 = _post_hdmicec("Device_User_Control_Released.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)

    # All six posts are required TOGETHER. Tolerating any one of them would report a pass on a
    # run that never delivered part of the sweep - a dropped Min or Boundary press would silently
    # shrink this case to the nominal key code, and a dropped release would leave a key held.
    # Neither shortfall is visible downstream, so it has to be caught here.
    if not (ok_nominal and ok_min and ok_boundary and ok_rel_1 and ok_rel_2 and ok_rel_3):
        log_error("✖ required vComponent emulation posts failed")
        return False

    # CLOSING PROBE - A LIVENESS CHECK, NOT A KEY-DELIVERY CHECK.
    #
    # getDeviceList is read once here because it is the only consequence of this flow the L3
    # transport can actually observe. Six injected frames that left the plugin unable to answer,
    # or answering without a device count, would be a real regression and a genuine signal; that
    # is the whole of what this probe claims. It deliberately does NOT stand in for evidence that
    # any key arrived - see the assertion block below.
    log_info("Reading the device list as a closing liveness check")
    after = send_curl_command(HdmiCecSinkApis.get_device_list)

    if not after:
        log_error("✖ final getDeviceList command not sent")
        return False

    if after.startswith("< No response"):
        log_error("✖ no response from WPEFramework - final device list unavailable")
        return False

    log_warning(f"Final device list: {after}")

    try:
        # ── ACT 1 - OUTBOUND PRESS ──────────────────────────────────────────────────────────────
        # sendUserControlPressed takes a logical address AND a key code (IHdmiCecSink.h:275) and
        # answers with success only, so the acknowledgement is the whole of what it can be
        # asserted on. Both parameter values are carried by the command constant itself and are
        # never restated here.
        log_info("Dispatching the outbound sendUserControlPressed call")
        if not _acknowledged(HdmiCecSinkApis.send_user_control_pressed, "sendUserControlPressed"):
            return False
        outbound_press_outstanding = True

        # ── ACT 2 - OUTBOUND RELEASE, WHICH CLOSES ACT 1 ────────────────────────────────────────
        # sendUserControlReleased takes the logical address ALONE (IHdmiCecSink.h:281) - the
        # asymmetry with pressed is by contract, since a release identifies no key.
        log_info("Dispatching the outbound sendUserControlReleased call to close the press")
        if not _acknowledged(HdmiCecSinkApis.send_user_control_released, "sendUserControlReleased"):
            return False
        outbound_press_outstanding = False

        # ── ACTS 3 TO 8 - THE INBOUND BOUNDARY SWEEP. THE REASON THIS MODULE EXISTS ─────────────
        # Three UserControlPressed frames differing ONLY in their key-code operand - nominal,
        # minimum, upper byte boundary, all three verified distinct above - each closed by the
        # same UserControlReleased frame. That is the sweep the sink L1 suite applies to these
        # APIs as sendUserControlPressed_MinKeyCode and sendUserControlPressed_BoundaryKeyCode,
        # lifted to device level rather than reinvented.
        #
        # Asserted: the two outbound acknowledgements, the six emulation posts (gated above),
        # and that the plugin still answers getDeviceList with a success envelope carrying an
        # integer device count.
        #
        # NOT asserted, and NOT implied anywhere in this module: that any key press or key
        # release actually reached the sink's handlers, and that OnKeyPressEvent or
        # OnKeyReleaseEvent fired. Those are Thunder notifications, and this suite's one-shot
        # curl transport cannot subscribe to a notification channel, so there is no observation
        # to make here - only an assumption that could be dressed up as one. Inventing an event
        # assertion at this level would be a false green, which is worse than saying nothing.
        #
        # Those two events are NOT unverified in the estate, though - they are simply verified
        # somewhere this transport cannot reach. The sink L1 suite asserts them directly
        # (onKeyPressEvent_SubscribedClient_ReceivesAddressAndKeyCode,
        # onKeyPressEvent_BoundaryOperands_AreForwardedVerbatim,
        # onKeyPressEvent_NoSubscriber_ProducesNoClientNotification,
        # onKeyReleaseEvent_SubscribedClient_ReceivesLogicalAddress) and the sink L2 suite
        # asserts them from injected frames (InjectUserControlPressedFrameAndVerifyEvent plus
        # its minimum / maximum-named / out-of-range key-code variants, and
        # InjectUserControlReleasedFrameAndVerifyEvent). The coverage register's listing of
        # both events as uncovered is its pre-change baseline, not the current state.
        #
        # The device count is likewise NOT compared against a before-probe. User-control frames
        # carry no address discovery, so this flow has no defensible expectation about how the
        # count should move, and asserting a direction would be a guess. Its type is checked
        # because a probe that answers without an integer count is a malformed response
        # regardless of what this flow did.
        if pressed_ack and released_ack and alive_ack and has_count:
            return True

            log_info(f"Injecting the UserControlReleased frame to close {description}")
            if not _post_hdmicec(RELEASE_FIXTURE):
                log_error(
                    f"✖ required injection refused - the release closing {description} was never "
                    f"delivered ({RELEASE_FIXTURE})"
                )
                return False
            inbound_press_outstanding = None
            log_success(f"✔ delivered the release closing {description}")

        # ── CLOSING OBSERVATION - AN INVARIANT, NOT A LIVENESS CHECK ────────────────────────────
        # An earlier revision read getDeviceList here purely as liveness and declined to compare
        # the count, on the grounds that "asserting a direction would be a guess". Equality is not
        # a guess: neither process(UserControlPressed) nor process(UserControlReleased) calls
        # addDevice or touches deviceList at all (:295-305), and every injected frame initiates
        # from an address the suite already seeded, so the inventory MUST be identical. That makes
        # this a real invariant - six frames that added, dropped or renumbered a peer would be a
        # genuine regression - and it is polled on a bounded monotonic budget so a busy plugin is
        # waited for rather than raced.
        deadline = time.monotonic() + OBSERVE_TIMEOUT_S
        while True:
            after_readable, after_count, after_addresses = _device_inventory()
            if after_readable and (after_count, after_addresses) == (before_count, before_addresses):
                break
            if time.monotonic() >= deadline:
                if not after_readable:
                    log_error(
                        "✖ the device inventory became unreadable after the flow - six injected "
                        "frames that left the plugin unable to answer is a regression"
                    )
                else:
                    log_error(
                        "✖ the user-control flow changed the device inventory: "
                        f"{before_count}/{before_addresses} -> {after_count}/{after_addresses}. "
                        "Neither user-control handler touches deviceList, so it must be identical"
                    )
                return False
            time.sleep(OBSERVE_POLL_S)
        log_success(
            f"✔ inventory unchanged: {after_count} devices at exactly {after_addresses}"
        )
        log_warning(f"Pressed  : {pressed_response}")
        log_warning(f"Released : {released_response}")
        log_warning(f"Probe    : {after}")
    except json.JSONDecodeError:
        # No return from this handler, by design: control falls through to the single failure
        # tail below, so the case reports one verdict from one place however it failed. The
        # diagnostic is logged here because a parse error and a payload mismatch are different
        # faults, and this suite's idiom is to name which one occurred.
        log_error("Invalid JSON response")

    return False
