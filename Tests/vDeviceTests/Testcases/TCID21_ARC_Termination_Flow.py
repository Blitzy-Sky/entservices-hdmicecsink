"""
/**
 * @file TCID21_ARC_Termination_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID21_ARC_Termination_Flow
 * @details Drives ONE Audio Return Channel TERMINATION end to end and probes the one arm of the
 *          handler's admission gate for which this suite ships a fixture. Five steps, in this
 *          order:
 *            1. before-probe - org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus, read for
 *               context and logged beside the after-probe rather than asserted;
 *            2. POSITIVE INJECTION - Device_Terminate_Arc.yaml delivers the audio system's
 *               <Terminate ARC> to HdmiCecSinkProcessor::process(const TerminateArc&, const
 *               Header&) at HdmiCecSinkImplementation.cpp:535;
 *            3. NEGATIVE ARM A - Device_Terminate_Arc_Broadcast.yaml, same initiator, broadcast
 *               destination;
 *            4. DISABLE / RESTORE - org.rdk.HdmiCecSink.setupARCRouting with
 *               {"enabled": false}, the sink's own request to take ARC back down
 *               (SetupARCRouting, HdmiCecSinkImplementation.cpp:1600);
 *            5. after-probe - the same connected-status read, logged beside the first.
 *
 *          THE ADMISSION GATE IS WHY THE TWO FIXTURES DIFFER BY ONE BYTE. The handler opens with
 *          the same two-arm rejection its initiation counterpart uses, at
 *          HdmiCecSinkImplementation.cpp:537 - `if((!(header.from.toInt() == 0x5)) ||
 *          (header.to.toInt() == LogicalAddress::BROADCAST)) return;` - so a <Terminate ARC> is
 *          admitted only when it comes FROM logical address 5, the audio system, AND is DIRECTED
 *          rather than broadcast. The header byte alone decides it:
 *            * 0x50 (positive)  initiator 5, destination 0 - both arms satisfied;
 *            * 0x5F (arm A)     initiator 5, destination 0xF broadcast - fails the destination
 *                               arm while holding the initiator constant.
 *          In both fixtures the opcode byte is 0xC5, <Terminate ARC>. Changing a header byte
 *          changes which arm is under test; do not substitute fixtures between the steps.
 *
 *          ONLY ONE NEGATIVE ARM IS EXERCISED, AND THAT IS NOT AN OVERSIGHT. The gate's other
 *          arm - a directed frame from an initiator that is not the audio system - needs a
 *          fixture carrying header 0x40, and this suite ships one on the INITIATE ARC side but
 *          none on the TERMINATE ARC side, so there is no valid document to post for it. THIS
 *          MODULE POSTS THE TWO TERMINATE DOCUMENTS NAMED ABOVE AND NOTHING ELSE - no
 *          initiation fixture, which is TCID20_ARC_Initiation_Flow's to post, and no invented
 *          filename, because a name that does not resolve makes send_vcomponent_command return
 *          (0, "YAML file not found: ...") and would read as a passing ARC case that injected
 *          nothing. The arm is therefore left unexercised and recorded here rather than faked,
 *          and TCID20_ARC_Initiation_Flow already covers it on the initiation path, where the
 *          gate expression is identical and the fixture for it does exist.
 *
 *          WHAT THE NEGATIVE INJECTION DOES AND DOES NOT PROVE. It is expected to be ACCEPTED by
 *          the emulator - HTTP 200 means the frame reached the CEC bus - and then DISCARDED by
 *          the handler at the gate. Those two outcomes are not in tension, and this module never
 *          conflates them: a 200 on the negative fixture is evidence of injection, never of an
 *          ARC termination. The rejection itself is unobservable from here, because the gate is a
 *          bare `return` that publishes no counter, no state and no notification, so the post is
 *          LOGGED AND NOT ASSERTED. It is carried anyway because injecting the frame is what
 *          exercises the gate on a real device; claiming the outcome would be the part this
 *          transport cannot support.
 *
 *          THIS CASE IS THE RESTORER OF AN ORDERED PAIR. TCID20_ARC_Initiation_Flow is the
 *          PRODUCER half: it deliberately leaves ARC ENABLED and documents, at the point where a
 *          teardown would otherwise sit, that this module is the consumer that takes ARC back
 *          down. SuitManager.py registers the two at consecutive positions (20 then 21) for that
 *          reason. Step 4 above is therefore not decoration - it is the step that returns the
 *          device to the state the pair was entered in, which is why it is asserted rather than
 *          merely logged. Run in order the pair is state-neutral; TCID20 alone is not, and this
 *          case alone assumes ARC was brought up before it.
 *
 *          GAP CLOSED. COVERAGE_GAPS.md ranks the missing sink vDeviceTests suite 22nd at
 *          priority P1 (#gap-plugin-sink-vdevicetests) precisely because the sink's ARC and
 *          audio-routing use cases - the ones that define the sink - had no end-to-end safety
 *          net. In the §4b API table `SetupARCRouting` is recorded as covered by the sink's own
 *          L2 suite (SetupARCRouting_COMRPC and SetupARCRouting_JSONRPC in
 *          ../../L2Tests/tests/HdmiCecSink_L2Test.cpp) with NO E2E leg. TCID20 supplies the
 *          INITIATION half of that leg; this module supplies the TERMINATION half.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework with
 *    the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the emulated topology, including the VAUDIO
 *    AudioSystem peer at CEC logical address 5, and has left HDMI-CEC enabled. Without that
 *    peer the admission gate at HdmiCecSinkImplementation.cpp:537 can never be satisfied, since
 *    a <Terminate ARC> from any other address is discarded unread. The peer is supplied BY THE
 *    EMULATED TOPOLOGY: the device under test is never reconfigured to act as its own audio
 *    system, which is the role-inversion construct this suite excludes by design.
 *  - TCID20_ARC_Initiation_Flow is expected to have run immediately before this case and to have
 *    left ARC enabled; this module is the half of that pair which restores it.
 *  - The vComponent HTTP API is reachable, so the two ARC frames can be injected.
 *  - AUTHORED, NOT EXECUTED in this repository: no CI workflow runs this suite, and nothing
 *    described here has been observed against a live device or emulator.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The directed <Terminate ARC> frame is injected and accepted by the emulator, and
 *    setupARCRouting acknowledges the disable request.
 *  - The gate-arm frame is injected and is expected to be DISCARDED by the handler. That
 *    expectation is stated and logged, not asserted: see @details.
 *  - THE ARC TERMINATION HANDSHAKE ITSELF IS NOT ASSERTED, because it is not observable from
 *    this transport. An admitted frame reaches Process_TerminateArc()
 *    (HdmiCecSinkImplementation.cpp:3359), which moves m_currentArcRoutingState to
 *    ARC_STATE_ARC_TERMINATED and fans out arcTerminationEvent
 *    (ArcTerminationEvent("success"), HdmiCecSinkImplementation.cpp:3381) - a Thunder
 *    notification delivered to registered COM-RPC/JSON-RPC subscribers rather than to a one-shot
 *    curl request/response - and no getter on the interface reports the ARC routing state.
 *  - The ARC state is left DISABLED, restoring what TCID20_ARC_Initiation_Flow enabled.
 *
 * @pass_criteria
 *  - The required <Terminate ARC> YAML post returns HTTP 200, setupARCRouting acknowledges
 *    result.success as True, the after-probe parses with result.success True and a boolean
 *    result.connected, and run_test() returns True.
 *
 * @failure_criteria
 *  - A request is not dispatched, a response is the no-response sentinel, the required
 *    vComponent post does not return HTTP 200, the disable call does not acknowledge success,
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

    # ACT 1 - POSITIVE INJECTION, THE ONE POST THIS CASE REQUIRES. Device_Terminate_Arc.yaml
    # carries payload ["0x50","0xC5"]: header 0x50 is initiator 5 to destination 0, opcode 0xC5
    # is <Terminate ARC>. That header is the only one of the two fixtures that clears both arms
    # of the gate at HdmiCecSinkImplementation.cpp:537, so this is the frame that represents an
    # actual termination and its post is required rather than advisory. Naming a file that does
    # not exist would make send_vcomponent_command return (0, "YAML file not found: ..."), which
    # would otherwise read as a passing ARC case that never injected anything - hence the check.
    #
    # The injection deliberately precedes the disable request in ACT 3: the frame is the AUDIO
    # SYSTEM asking the television to tear ARC down, which is the direction a real peer drives,
    # and the sink's own setupARCRouting call is then the local half of the same teardown.
    ok_positive = _post_hdmicec("Device_Terminate_Arc.yaml")
    time.sleep(1)

    if not ok_positive:
        log_error("✖ required vComponent ARC termination post failed")
        return False

    # ACT 2 - THE GATE ARM THIS SUITE HAS A FIXTURE FOR, INJECTED AND NOT ASSERTED.
    #
    # Device_Terminate_Arc_Broadcast.yaml differs from the positive fixture in its HEADER BYTE
    # ONLY - 0x5F instead of 0x50 - so it isolates the destination arm of the rejection at
    # HdmiCecSinkImplementation.cpp:537 with the initiator arm held constant. It is expected to
    # be accepted by the emulator and then discarded by the handler, and NEITHER OUTCOME IS
    # ASSERTED HERE - deliberately, and this is the honest reporting line of this module. The
    # gate is a bare `return`: it increments no counter, changes no state and raises no
    # notification, so a rejection leaves nothing this transport can read. Requiring HTTP 200
    # would assert the emulator's behaviour and call it the handler's; requiring a non-200 would
    # assert the opposite of what should happen. The post is carried because injecting the frame
    # is what exercises the gate on a real device, and its result is logged so a reader can see
    # what the emulator did with it.
    #
    # A 200 logged below is therefore evidence that the frame was INJECTED. It is not, and must
    # not be read as, evidence of an ARC termination.
    ok_broadcast = _post_hdmicec("Device_Terminate_Arc_Broadcast.yaml")
    time.sleep(1)
    log_info(
        "  gate arm A (header 0x5F, broadcast destination) injected; expected to be discarded "
        f"by the handler - post accepted: {ok_broadcast}"
    )

    # ACT 3 - DISABLE, AND THE RESTORE THAT MAKES THE PAIR STATE-NEUTRAL. setupARCRouting with
    # {"enabled": false} is the sink's own request to take ARC down, and it is the disabling
    # counterpart of TCID20_ARC_Initiation_Flow's ACT 1. TCID20 leaves ARC enabled on purpose and
    # names this module as the restorer, so this call is REQUIRED and its acknowledgement is
    # asserted below rather than merely logged: if it were dropped, the ordered pair would leak
    # an enabled ARC into every case that follows.
    curl_response = send_curl_command(HdmiCecSinkApis.setup_arc_routing_false)
    if not curl_response:
        log_error("✖ setupArcRouting disable command not sent")
        return False
    if curl_response.startswith("< No response"):
        log_error("✖ setupArcRouting disable returned no response from WPEFramework")
        return False
    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")
    time.sleep(1)

    # After-probe: the same read as the before-probe, so the pair can be compared in the log.
    after = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    if not after:
        log_error("✖ final getAudioDeviceConnectedStatus command not sent")
        return False
    if after.startswith("< No response"):
        log_error("✖ final getAudioDeviceConnectedStatus returned no response")
        return False
    log_warning(f"Final audio connection: {after}")

    try:
        # ACT 3's acknowledgement. SetupARCRouting publishes exactly one field - success
        # (IHdmiCecSink.h:328) - so this is the only claim the disable call itself supports.
        # Nothing is asserted about the resulting termination handshake; see @expected_result.
        if _result_object(curl_response).get("success") is not True:
            log_error("✖ setupArcRouting disable did not acknowledge success")
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
        # discovered at logical address 5 rather than by an ARC termination, so an ARC flow is not
        # what moves it. The sink's own L2 suite asserts the counter-intuitive value for exactly
        # that reason - EXPECT_FALSE(connected) at ../../L2Tests/tests/HdmiCecSink_L2Test.cpp:1827
        # over COM-RPC and EXPECT_FALSE(result["connected"].Boolean()) at :2539 over JSON-RPC -
        # because no audio system is ever discovered in that in-process host. This suite has
        # never been executed, so pinning the value would fail in one valid environment or the
        # other. `success` is different: the implementation sets it unconditionally, so requiring
        # True is measured.
        if after_result.get("success") is True and isinstance(connected_after, bool):
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID21_ARC_Termination_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {after}")
    except json.JSONDecodeError:
        log_error("Invalid JSON response")

    log_error("TCID21_ARC_Termination_Flow Failed ❌")
    return False
