"""
/**
 * @file TCID20_ARC_Initiation_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID20_ARC_Initiation_Flow
 * @details Drives ONE Audio Return Channel INITIATION end to end and then probes the two arms
 *          of the handler's admission gate with frames engineered to fail exactly one arm
 *          each. Six steps, in this order:
 *            1. before-probe - org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus, read for
 *               context and logged beside the after-probe rather than asserted;
 *            2. ENABLE - org.rdk.HdmiCecSink.setupARCRouting with {"enabled": true}, the sink's
 *               own request to bring ARC up (SetupARCRouting, IHdmiCecSink.h:328);
 *            3. POSITIVE INJECTION - Device_Initiate_Arc.yaml delivers the audio system's
 *               <Initiate ARC> to HdmiCecSinkProcessor::process(const InitiateArc&, const
 *               Header&) at HdmiCecSinkImplementation.cpp:508;
 *            4. NEGATIVE ARM A - Device_Initiate_Arc_Broadcast.yaml, same initiator, broadcast
 *               destination;
 *            5. NEGATIVE ARM B - Device_Initiate_Arc_Invalid_Initiator.yaml, directed
 *               destination, wrong initiator;
 *            6. after-probe - the same connected-status read, logged beside the first.
 *
 *          THE ADMISSION GATE IS WHY THE THREE FIXTURES DIFFER BY ONE BYTE. The handler opens
 *          with a single two-arm rejection at HdmiCecSinkImplementation.cpp:510 -
 *          `if((!(header.from.toInt() == 0x5)) || (header.to.toInt() ==
 *          LogicalAddress::BROADCAST)) return;` - so an <Initiate ARC> is admitted only when it
 *          comes FROM logical address 5, the audio system, AND is DIRECTED rather than
 *          broadcast. The header byte alone decides it:
 *            * 0x50 (positive)  initiator 5, destination 0 - both arms satisfied;
 *            * 0x5F (arm A)     initiator 5, destination 0xF broadcast - fails the destination
 *                               arm while holding the initiator constant;
 *            * 0x40 (arm B)     initiator 4, a playback device, destination 0 - fails the
 *                               initiator arm while holding the destination constant.
 *          Changing a header byte therefore changes which arm is under test; do not substitute
 *          fixtures between the steps.
 *
 *          WHAT THE TWO NEGATIVE INJECTIONS DO AND DO NOT PROVE. Both are expected to be
 *          ACCEPTED by the emulator - HTTP 200 means the frame reached the CEC bus - and then
 *          DISCARDED by the handler at the gate. Those two outcomes are not in tension, and
 *          this module never conflates them: a 200 on a negative fixture is evidence of
 *          injection, never of an ARC initiation. The rejection itself is unobservable from
 *          here, because the gate is a bare `return` that publishes no counter, no state and no
 *          notification, so the two posts are LOGGED AND NOT ASSERTED. They are carried anyway
 *          because injecting the frame is what exercises the gate on a real device; claiming
 *          the outcome would be the part this transport cannot support.
 *
 *          ARC IS DELIBERATELY LEFT ENABLED WHEN THIS CASE PASSES. This module is the PRODUCER
 *          half of an ordered pair: TCID21_ARC_Termination_Flow runs immediately after it
 *          (SuitManager.py registers the two at consecutive positions) and is the restorer that
 *          takes ARC back down. Tearing ARC down here would leave TCID21 nothing to terminate,
 *          so the absence of a restore step below is deliberate and is marked at the point
 *          where one would otherwise sit. The pair, run in order, returns the device to the
 *          state it was found in; this case on its own does not, and is not meant to.
 *
 *          GAP CLOSED. COVERAGE_GAPS.md ranks the missing sink vDeviceTests suite 22nd at
 *          priority P1 (#gap-plugin-sink-vdevicetests) precisely because the sink's ARC and
 *          audio-routing use cases - the ones that define the sink - had no end-to-end safety
 *          net. In the §4b API table `SetupARCRouting` is recorded as covered by the sink's own
 *          L2 suite (SetupARCRouting_COMRPC and SetupARCRouting_JSONRPC in
 *          ../../L2Tests/tests/HdmiCecSink_L2Test.cpp) with NO E2E leg. This module supplies
 *          the INITIATION half of that leg; TCID21_ARC_Termination_Flow supplies the other.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework with
 *    the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the emulated topology, including the VAUDIO
 *    AudioSystem peer at CEC logical address 5, and has left HDMI-CEC enabled. Without that
 *    peer the admission gate at HdmiCecSinkImplementation.cpp:510 can never be satisfied, since
 *    an <Initiate ARC> from any other address is discarded unread. The peer is supplied BY THE
 *    EMULATED TOPOLOGY: the device under test is never reconfigured to act as its own audio
 *    system, which is the role-inversion construct this suite excludes by design.
 *  - The vComponent HTTP API is reachable, so the three ARC frames can be injected.
 *  - TCID21_ARC_Termination_Flow is expected to run after this case and restore the ARC state
 *    this case leaves enabled.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - setupARCRouting acknowledges the enable request, and the directed <Initiate ARC> frame is
 *    injected and accepted by the emulator.
 *  - The two gate-arm frames are injected and are expected to be DISCARDED by the handler. That
 *    expectation is stated and logged, not asserted: see @details.
 *  - THE ARC HANDSHAKE ITSELF IS NOT ASSERTED, because it is not observable from this
 *    transport. A successful initiation publishes arcInitiationEvent (ArcInitiationEvent,
 *    IHdmiCecSink.h:71), a Thunder notification delivered to registered COM-RPC/JSON-RPC
 *    subscribers rather than to a one-shot curl request/response, and no getter on the
 *    interface reports the ARC routing state.
 *  - The ARC state is left ENABLED for TCID21_ARC_Termination_Flow.
 *
 * @pass_criteria
 *  - The required <Initiate ARC> YAML post returns HTTP 200, setupARCRouting acknowledges
 *    result.success as True, the after-probe parses with result.success True and a boolean
 *    result.connected, and run_test() returns True.
 *
 * @failure_criteria
 *  - A request is not dispatched, a response is the no-response sentinel, the required
 *    vComponent post does not return HTTP 200, the enable call does not acknowledge success,
 *    the after-probe reports success other than True or a non-boolean connected, a JSON parsing
 *    error occurs, or run_test() returns False.
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


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could
    carry a non-object "result" or not be an object at all. Every such case collapses to {} so
    the caller reports a MISSING FIELD rather than raising AttributeError out of run_test(). A
    body that is not JSON at all still raises json.JSONDecodeError, which run_test() handles as
    the documented failure. Three call sites share this, which is why it is factored out.
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

    # Before-probe: context for the exchange, logged rather than asserted.
    before = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    if not before:
        log_error("✖ initial getAudioDeviceConnectedStatus command not sent")
        return False
    # The transport failure guard that actually fires in this suite. utils.send_curl_command
    # returns the "< No response from WPEFramework >" sentinel - a TRUTHY string - for every
    # failure mode, so the falsy check above cannot catch one on its own. The prefix form is the
    # detection contract utils.py documents for callers.
    if before.startswith("< No response"):
        log_error("✖ initial getAudioDeviceConnectedStatus returned no response")
        return False
    log_warning(f"Initial audio connection: {before}")

    # ACT 1 - ENABLE. setupARCRouting({"enabled": true}) is the sink's own request to bring ARC
    # up, and it must precede the injections below: the handler's Process_InitiateArc() path
    # (HdmiCecSinkImplementation.cpp:3315) is what an admitted frame reaches, and enabling first
    # is the order a real audio system and television negotiate in.
    curl_response = send_curl_command(HdmiCecSinkApis.setup_arc_routing_true)
    if not curl_response:
        log_error("✖ setupArcRouting command not sent")
        return False
    if curl_response.startswith("< No response"):
        log_error("✖ setupArcRouting returned no response from WPEFramework")
        return False
    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")
    time.sleep(1)

    # ACT 2 - POSITIVE INJECTION, THE ONE POST THIS CASE REQUIRES. Device_Initiate_Arc.yaml
    # carries payload ["0x50","0xC0"]: header 0x50 is initiator 5 to destination 0, opcode 0xC0
    # is <Initiate ARC>. That header is the only one of the three fixtures that clears both arms
    # of the gate at HdmiCecSinkImplementation.cpp:510, so this is the frame that represents an
    # actual initiation and its post is required rather than advisory. Naming a file that does
    # not exist would make send_vcomponent_command return (0, "YAML file not found: ..."), which
    # would otherwise read as a passing ARC case that never injected anything - hence the check.
    ok_positive = _post_hdmicec("Device_Initiate_Arc.yaml")
    time.sleep(1)

    if not ok_positive:
        log_error("✖ required vComponent ARC initiation post failed")
        return False

    # ACT 3 - THE TWO GATE ARMS, INJECTED AND NOT ASSERTED.
    #
    # Each fixture below differs from the positive one in its HEADER BYTE ONLY, so each isolates
    # a single arm of the rejection at HdmiCecSinkImplementation.cpp:510 with the other arm held
    # constant. Both are expected to be accepted by the emulator and then discarded by the
    # handler, and NEITHER OUTCOME IS ASSERTED HERE - deliberately, and this is the honest
    # reporting line of this module. The gate is a bare `return`: it increments no counter,
    # changes no state and raises no notification, so a rejection leaves nothing this transport
    # can read. Requiring HTTP 200 would assert the emulator's behaviour and call it the
    # handler's; requiring a non-200 would assert the opposite of what should happen. The posts
    # are carried because injecting the frames is what exercises the gate on a real device, and
    # their results are logged so a reader can see what the emulator did with them.
    #
    # A 200 logged below is therefore evidence that the frame was INJECTED. It is not, and must
    # not be read as, evidence of an ARC initiation.
    ok_broadcast = _post_hdmicec("Device_Initiate_Arc_Broadcast.yaml")
    time.sleep(1)
    log_info(
        "  gate arm A (header 0x5F, broadcast destination) injected; expected to be discarded "
        f"by the handler - post accepted: {ok_broadcast}"
    )

    ok_invalid_initiator = _post_hdmicec("Device_Initiate_Arc_Invalid_Initiator.yaml")
    time.sleep(1)
    log_info(
        "  gate arm B (header 0x40, initiator is not the audio system) injected; expected to be "
        f"discarded by the handler - post accepted: {ok_invalid_initiator}"
    )

    # After-probe: the same read as the before-probe, so the pair can be compared in the log.
    after = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    if not after:
        log_error("✖ final getAudioDeviceConnectedStatus command not sent")
        return False
    if after.startswith("< No response"):
        log_error("✖ final getAudioDeviceConnectedStatus returned no response")
        return False
    log_warning(f"Final audio connection: {after}")

    # SHARED STATE LEFT BEHIND, ON PURPOSE. A teardown would sit exactly here - the disabling
    # counterpart of Act 1, setupARCRouting with {"enabled": false}, which HdmiCECSink_Curl.py
    # publishes as the `false` half of its setup_arc_routing pair - and it is absent by design
    # rather than by omission. TCID21_ARC_Termination_Flow is the paired consumer that terminates
    # what this case initiates, and SuitManager.py runs it immediately after this one, so
    # restoring here would leave that case nothing to terminate and would silently convert it
    # into a no-op. This module is consequently NOT self-restoring; the ORDERED PAIR is. Do not
    # add a teardown here without moving the termination coverage somewhere it can still be
    # reached.

    try:
        # Act 1's acknowledgement. SetupARCRouting publishes exactly one field - success
        # (IHdmiCecSink.h:328) - so this is the only claim the enable call itself supports.
        # Nothing is asserted about the resulting ARC handshake; see @expected_result.
        if _result_object(curl_response).get("success") is not True:
            log_error("✖ setupArcRouting did not acknowledge success")
            return False

        before_result = _result_object(before)
        after_result = _result_object(after)
        connected_before = before_result.get("connected")
        connected_after = after_result.get("connected")
        log_info(
            "Observed audio device connected state: "
            f"before={connected_before} after={connected_after}"
        )

        # TYPE-ONLY ASSERTION ON `connected` - DO NOT STRENGTHEN THIS INTO A VALUE CHECK.
        # Neither True nor False is a claim this testcase can honestly make. The flag mirrors
        # HdmiCecSinkImplementation::hdmiCecAudioDeviceConnected, which is set when a peer is
        # discovered at logical address 5 rather than by an ARC initiation, so an ARC flow is not
        # what moves it. The sink's own L2 suite asserts the counter-intuitive value for exactly
        # that reason - EXPECT_FALSE(connected) at ../../L2Tests/tests/HdmiCecSink_L2Test.cpp:1827
        # over COM-RPC and EXPECT_FALSE(result["connected"].Boolean()) at :2539 over JSON-RPC -
        # because no audio system is ever discovered in that in-process host. This suite has
        # never been executed, so pinning the value would fail in one valid environment or the
        # other. `success` is different: the implementation sets it unconditionally, so requiring
        # True is measured.
        if after_result.get("success") is True and isinstance(connected_after, bool):
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID20_ARC_Initiation_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {after}")
    except json.JSONDecodeError:
        log_error("Invalid JSON response")

    log_error("TCID20_ARC_Initiation_Flow Failed ❌")
    return False
