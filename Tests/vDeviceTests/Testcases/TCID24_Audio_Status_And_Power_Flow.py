"""
/**
 * @file TCID24_Audio_Status_And_Power_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID24_Audio_Status_And_Power_Flow
 * @details Drives TWO COMPLETE REQUEST/RESPONSE EXCHANGES against the emulated audio system,
 *          and injects a third frame that is the mute corner case of the first - two
 *          solicitations and three replies, where the read-only cases earlier in this suite
 *          have neither. Seven steps, in this order:
 *            1. before-probe - org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus, read for
 *               context and logged beside the after-probe rather than asserted;
 *            2. EXCHANGE 1, OUTBOUND - org.rdk.HdmiCecSink.sendGetAudioStatusMessage, which
 *               reaches HdmiCecSinkImplementation::SendGetAudioStatusMessage
 *               (HdmiCecSinkImplementation.cpp:1690) and directs <Give Audio Status> at CEC
 *               logical address 5 through sendGiveAudioStatusMsg (:1263, sendTo
 *               LogicalAddress::AUDIO_SYSTEM at :1274);
 *            3. EXCHANGE 1, INBOUND (positive) - Device_Report_Audio_Status.yaml injects the
 *               audio system's <Report Audio Status> answer carrying volume 50 with the mute
 *               bit CLEAR, reaching HdmiCecSinkProcessor::process(const ReportAudioStatus &,
 *               const Header &) at :556 and then Process_ReportAudioStatus_msg at :1171;
 *            4. EXCHANGE 1, INBOUND (corner) - Device_Report_Audio_Status_Muted.yaml injects
 *               the SAME volume with bit 7 SET, which is the mute-decoding branch of the same
 *               handler. Posting both is what makes this module positive-plus-corner rather
 *               than happy-path only;
 *            5. EXCHANGE 2, OUTBOUND - org.rdk.HdmiCecSink.requestAudioDevicePowerStatus,
 *               which reaches RequestAudioDevicePowerStatus (:2208), directs
 *               <Give Device Power Status> at logical address 5 (:2233) and - critically -
 *               sets m_audioDevicePowerStatusRequested at :2234;
 *            6. EXCHANGE 2, INBOUND - Device_Report_Power_Status.yaml injects the audio
 *               system's <Report Power Status> answer, reaching
 *               process(const ReportPowerStatus &, const Header &) at :408;
 *            7. after-probe - the same connected-status read, logged beside the first.
 *
 *          THE ORDER OF EACH SOLICITATION AND ITS REPLY IS FUNCTIONAL, NOT COSMETIC, and for
 *          exchange 2 the code proves it: the AudioSystem-specific branch at :425 is guarded by
 *          `(header.from == LogicalAddress::AUDIO_SYSTEM) &&
 *          m_audioDevicePowerStatusRequested`, and that flag is set in exactly one place -
 *          inside RequestAudioDevicePowerStatus at :2234. Inject the reply first and the flag is
 *          still false, so reportAudioDevicePowerStatusInfo (:1277) is never reached and the
 *          exchange degenerates into an unsolicited report.
 *
 *          ALL THREE FIXTURES ARE DIRECTED (initiator 0x5, destination 0x0), AND NONE MAY BE
 *          SWAPPED FOR A BROADCAST FORM: both handlers return early on a broadcast destination
 *          (:559 for ReportAudioStatus, :411 for ReportPowerStatus), so a broadcast frame is
 *          discarded before any decoding happens. The power-status fixture must also be
 *          Device_Report_Power_Status.yaml, whose payload initiates from 0x5; the sibling
 *          Process_Report_Power_Status.yaml exists but initiates from 0x4, which satisfies the
 *          generic device-update path and misses the AudioSystem branch this module exists to
 *          drive.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework with
 *    the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the emulated topology, including the VAUDIO
 *    AudioSystem peer at CEC logical address 5, and has left HDMI-CEC ENABLED. Both
 *    conditions are load-bearing for exchange 2: RequestAudioDevicePowerStatus returns
 *    Core::ERROR_GENERAL when CEC is disabled, when no logical address has been allocated, or
 *    when the connection is absent (HdmiCecSinkImplementation.cpp:2210-2231), and the reply
 *    can only be attributed to an audio system that exists at address 5.
 *  - The vComponent HTTP API is reachable, so all three reply payloads can be injected.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - Both solicitations are acknowledged, and all three replies are injected and accepted by
 *    the emulator.
 *  - THE DECODED VOLUME AND MUTE STATE ARE NOT ASSERTED, because they are not observable from
 *    this transport: SendGetAudioStatusMessage publishes only a success flag
 *    (IHdmiCecSink.h:261), no getter on the interface returns the received audio status, and
 *    the reportAudioStatusEvent notification that does carry the pair
 *    (IHdmiCecSink.h:127-130) is fanned out to registered COM-RPC/JSON-RPC subscribers at
 *    HdmiCecSinkImplementation.cpp:1192, not to a curl request/response.
 *  - THE PEER'S POWER STATUS IS LIKEWISE NOT ASSERTED, for the same structural reason:
 *    RequestAudioDevicePowerStatus publishes only a success flag (IHdmiCecSink.h:348), and the
 *    received value leaves the plugin solely through the reportAudioDevicePowerStatus
 *    notification (IHdmiCecSink.h:160-162, fanned out at :1277).
 *
 * @pass_criteria
 *  - All three required YAML posts return HTTP 200, sendGetAudioStatusMessage and
 *    requestAudioDevicePowerStatus each acknowledge result.success as True, the after-probe
 *    parses with result.success True and a boolean result.connected, and run_test() returns
 *    True.
 *
 * @failure_criteria
 *  - A request is not dispatched, a response is the no-response sentinel, any required
 *    vComponent post does not return HTTP 200, either solicitation does not acknowledge
 *    success, the after-probe reports success other than True or a non-boolean connected, a
 *    JSON parsing error occurs, or run_test() returns False.
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
    the documented failure. Four call sites share this - the two solicitation acknowledgements
    and the two connected-status probes - which is why it is factored out rather than inlined.
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

    # SHARED STATE, AND THE ONE RESIDUAL THIS MODULE LEAVES BEHIND.
    #
    # ORDERING CHOSEN: unmuted first, MUTED LAST - deliberately, because muted is the corner
    # case and putting it after the positive case means a failure in the corner injection cannot
    # be mistaken for a failure in the positive one. The consequence is stated rather than
    # glossed: the last audio status the middleware recorded for the audio system is the MUTED
    # one, and this module does not restore the unmuted state. No restore is fabricated for two
    # reasons - the interface publishes NO inverse API (nothing on IHdmiCecSink clears a received
    # audio status, so a "restore" could only re-inject the unmuted frame and prove nothing), and
    # the residual alters NO user-visible plugin setting exposed by the interface (the status is
    # fanned out as a notification at HdmiCecSinkImplementation.cpp:1192 and persisted into
    # nothing a later case reads; the next case in SuitManager.py,
    # TCID25_Standby_Coordination_Flow, exercises standby rather than audio mute). Exchange 2 is
    # unaffected either way - its branch is gated on m_audioDevicePowerStatusRequested, not on
    # any audio status.

    # Before-probe: context for the two exchanges, logged rather than asserted.
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

    # EXCHANGE 1, ACT 1 - OUTBOUND SOLICITATION. The sink asks the audio system for its current
    # volume and mute state. This must precede the two injections below; see @details.
    audio_response = send_curl_command(HdmiCecSinkApis.send_get_audio_status_message)
    if not audio_response:
        log_error("✖ sendGetAudioStatusMessage command not sent")
        return False
    if audio_response.startswith("< No response"):
        log_error("✖ sendGetAudioStatusMessage returned no response from WPEFramework")
        return False
    log_success("✔ curl command sent")
    log_warning(f"Response: {audio_response}")
    time.sleep(1)

    # EXCHANGE 1, ACT 2 - INBOUND REPLY, POSITIVE. payload ["0x50","0x7A","0x32"]: opcode 0x7A
    # (<Report Audio Status>) with ONE operand byte whose bit 7 is the audio mute flag and bits
    # 0-6 the volume - so 0x32 is volume 50 with mute CLEAR. Do not widen or narrow the operand.
    ok_status = _post_hdmicec("Device_Report_Audio_Status.yaml")
    time.sleep(1)

    # EXCHANGE 1, ACT 3 - INBOUND REPLY, MUTE CORNER. payload ["0x50","0x7A","0xB2"]: the frame
    # above with the mute bit raised, 0x32 | 0x80 == 0xB2, so the SAME volume 50 arrives muted.
    # One operand bit is the entire difference, and it is the whole reason this second fixture
    # exists rather than being a duplicate - it is the only route from this transport into the
    # mute-decoding branch of the same handler. All three posts are required below, so a fixture
    # renamed out from under this module surfaces as a failure here rather than silently
    # disabling a step (a missing name makes send_vcomponent_command return
    # (0, "YAML file not found: ...")).
    ok_muted = _post_hdmicec("Device_Report_Audio_Status_Muted.yaml")
    time.sleep(1)

    # EXCHANGE 2, ACT 1 - OUTBOUND SOLICITATION. The sink asks the audio system for its power
    # state. Besides emitting <Give Device Power Status> this call sets
    # m_audioDevicePowerStatusRequested (HdmiCecSinkImplementation.cpp:2234), which is the flag
    # the inbound handler's AudioSystem branch is gated on - so this step is not merely first in
    # narrative order, it is the step that arms the next one.
    power_response = send_curl_command(HdmiCecSinkApis.request_audio_device_power_status)
    if not power_response:
        log_error("✖ requestAudioDevicePowerStatus command not sent")
        return False
    if power_response.startswith("< No response"):
        log_error("✖ requestAudioDevicePowerStatus returned no response from WPEFramework")
        return False
    log_success("✔ curl command sent")
    log_warning(f"Response: {power_response}")
    time.sleep(1)

    # EXCHANGE 2, ACT 2 - INBOUND REPLY. payload ["0x50","0x90","0x00"]: opcode 0x90
    # (<Report Power Status>) with operand 0x00 (on). THE INITIATOR IS THE POINT: only 0x5
    # satisfies `header.from == LogicalAddress::AUDIO_SYSTEM` at :425 and reaches
    # reportAudioDevicePowerStatusInfo. The sibling Process_Report_Power_Status.yaml initiates
    # from 0x4 and takes the generic device-update path, so it is deliberately not used here.
    ok_power = _post_hdmicec("Device_Report_Power_Status.yaml")
    time.sleep(1)

    if not (ok_status and ok_muted and ok_power):
        log_error("✖ required vComponent emulation posts failed")
        return False

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
        # Exchange 1's acknowledgement. SendGetAudioStatusMessage publishes exactly one field -
        # success (IHdmiCecSink.h:261) - and the implementation sets it unconditionally
        # (HdmiCecSinkImplementation.cpp:1693), so requiring True is a measured claim rather
        # than an optimistic one.
        #
        # UNOBSERVABLE 1 - VOLUME AND MUTE STATE. Nothing is asserted about the 0x32/0xB2
        # operands that were just injected, and that is not an omission. No getter on
        # IHdmiCecSink returns a received audio status; the decoded pair leaves the plugin only
        # through the reportAudioStatusEvent notification (IHdmiCecSink.h:127-130), fanned out to
        # registered COM-RPC/JSON-RPC subscribers at HdmiCecSinkImplementation.cpp:1192. A curl
        # request/response cannot subscribe, so the two injections are verified by the emulator's
        # HTTP 200 acceptance and by the handler being reachable at all - not by their contents.
        if _result_object(audio_response).get("success") is not True:
            log_error("✖ sendGetAudioStatusMessage did not acknowledge success")
            return False

        # Exchange 2's acknowledgement, on the same footing: RequestAudioDevicePowerStatus
        # publishes only success (IHdmiCecSink.h:348).
        #
        # UNOBSERVABLE 2 - THE PEER'S POWER STATUS. The 0x00 operand is likewise not asserted.
        # The received value leaves the plugin solely through the reportAudioDevicePowerStatus
        # notification (IHdmiCecSink.h:160-162), fanned out at
        # HdmiCecSinkImplementation.cpp:1277, and no getter exposes it. Asserting a power value
        # here would mean asserting something this transport never receives.
        if _result_object(power_response).get("success") is not True:
            log_error("✖ requestAudioDevicePowerStatus did not acknowledge success")
            return False

        before_result = _result_object(before)
        after_result = _result_object(after)
        connected_before = before_result.get("connected")
        connected_after = after_result.get("connected")
        log_info(
            "Observed audio device connected state: "
            f"before={connected_before} after={connected_after}"
        )

        # UNOBSERVABLE 3 - TYPE-ONLY ASSERTION ON `connected`. DO NOT STRENGTHEN THIS INTO A
        # VALUE CHECK. Neither True nor False is a claim this testcase can honestly make, and
        # neither exchange above is what sets the flag anyway: it mirrors
        # HdmiCecSinkImplementation::hdmiCecAudioDeviceConnected, raised only when a peer is
        # discovered at logical address 5 (:2459). The sink's own L2 suite asserts the
        # counter-intuitive value for exactly this reason - EXPECT_FALSE(connected) at
        # ../../L2Tests/tests/HdmiCecSink_L2Test.cpp:1827 over COM-RPC and
        # EXPECT_FALSE(result["connected"].Boolean()) at :2539 over JSON-RPC - because no audio
        # system is ever discovered in that in-process host. This suite has never been executed,
        # so pinning the value would fail in one valid environment or the other. `success` is
        # different: the implementation sets it unconditionally, so requiring True is measured.
        if after_result.get("success") is True and isinstance(connected_after, bool):
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID24_Audio_Status_And_Power_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {after}")
    except json.JSONDecodeError:
        log_error("Invalid JSON response")

    log_error("TCID24_Audio_Status_And_Power_Flow Failed ❌")
    return False
