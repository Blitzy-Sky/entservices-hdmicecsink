"""
/**
 * @file TCID23_Short_Audio_Descriptor_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID23_Short_Audio_Descriptor_Flow
 * @details Exercises BOTH DIRECTIONS of one Short Audio Descriptor (SAD) exchange, which is
 *          what separates this module from the read-only cases earlier in the suite: the
 *          JSON-RPC call makes the sink ASK, and the injected fixture supplies the audio
 *          system's REPLY. Four steps, in this order:
 *            1. before-probe - org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus, read for
 *               context and logged beside the after-probe rather than asserted;
 *            2. OUTBOUND SOLICITATION - org.rdk.HdmiCecSink.requestShortAudioDescriptor, which
 *               encodes <Request Short Audio Descriptor> and directs it at CEC logical address
 *               5 (HdmiCecSinkImplementation.cpp:2204 sends to LogicalAddress::AUDIO_SYSTEM);
 *            3. INBOUND REPLY - Device_Report_Short_Audio_Descriptor.yaml injects the audio
 *               system's <Report Short Audio Descriptor> answer, reaching
 *               HdmiCecSinkProcessor::process(const ReportShortAudioDescriptor&, const Header&)
 *               at HdmiCecSinkImplementation.cpp:545;
 *            4. after-probe - the same connected-status read, logged beside the first.
 *
 *          THE ORDER OF STEPS 2 AND 3 IS FUNCTIONAL, NOT COSMETIC, and must not be swapped: a
 *          reply injected before its request is an unsolicited report, not half of an exchange.
 *          The emulator cannot answer the solicitation by itself either - the
 *          <Request Short Audio Descriptor> -> <Report Short Audio Descriptor> pair is recorded
 *          as ABSENT from the auto-response table in
 *          vcomponent_configurations/hdmicec/hdmicec_vcomponent_cec_responses.yaml:152-153 -
 *          which is why the answer is injected as a raw user_defined payload.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework with
 *    the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the emulated topology, including the VAUDIO
 *    AudioSystem peer at CEC logical address 5, and has left HDMI-CEC enabled.
 *  - The vComponent HTTP API is reachable, so the reply payload can be injected.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - requestShortAudioDescriptor acknowledges the solicitation, and the descriptor reply is
 *    injected and accepted by the emulator.
 *  - The DECODED DESCRIPTOR CONTENTS ARE NOT ASSERTED, because they are not observable from
 *    this transport: RequestShortAudioDescriptor publishes only a success flag
 *    (IHdmiCecSink.h:251), no getter on the interface returns the received descriptors, and the
 *    shortAudiodescriptorEvent notification that does carry them (IHdmiCecSink.h:149-152) is
 *    delivered to registered COM-RPC/JSON-RPC subscribers, not to a curl request/response.
 *
 * @pass_criteria
 *  - The required YAML post returns HTTP 200, requestShortAudioDescriptor acknowledges
 *    result.success as True, the after-probe parses with result.success True and a boolean
 *    result.connected, and run_test() returns True.
 *
 * @failure_criteria
 *  - A request is not dispatched, a response is the no-response sentinel, the required
 *    vComponent post does not return HTTP 200, the solicitation does not acknowledge success,
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

    # SHARED STATE: none. A descriptor exchange makes the sink record the audio system's
    # capability information; it changes no user-visible setting, and the interface publishes no
    # inverse API that could undo it. The absence of a restore step here is therefore deliberate
    # rather than forgotten, and no later testcase depends on this module having reverted
    # anything.

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

    # ACT 1 - OUTBOUND SOLICITATION. The sink asks the audio system for its descriptors. This
    # must precede the injection below; see @details.
    curl_response = send_curl_command(HdmiCecSinkApis.request_short_audio_descriptor)
    if not curl_response:
        log_error("✖ requestShortAudioDescriptor command not sent")
        return False
    if curl_response.startswith("< No response"):
        log_error("✖ requestShortAudioDescriptor returned no response from WPEFramework")
        return False
    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")
    time.sleep(1)

    # ACT 2 - INBOUND REPLY. Device_Report_Short_Audio_Descriptor.yaml carries payload
    # ["0x50","0xA3","0x01","0x00","0x00"]: directed from logical address 5 to 0, opcode 0xA3
    # (<Report Short Audio Descriptor>), then EXACTLY THREE operand bytes forming one descriptor.
    # That width is load-bearing, not arbitrary - ShortAudioDescriptor is a fixed three-byte
    # operand (Operands.hpp:695, MAX_LEN = 3) and the decoding constructor derives its count by
    # INTEGER division, numberofdescriptor = frame.length() / 3 (Messages.hpp:538), reading each
    # descriptor at startPos + i*3. Trimming a byte truncates that count to zero and the handler
    # then observes an empty list, so do not add or remove operand bytes and do not substitute a
    # broadcast fixture. This is also the ONLY fixture for opcode 0xA3 under
    # vcomponent_configurations/commands/ - there is deliberately no Process_-prefixed variant,
    # and naming a file that does not exist would make send_vcomponent_command return
    # (0, "YAML file not found: ..."), which is why the post's result is required below.
    ok_reply = _post_hdmicec("Device_Report_Short_Audio_Descriptor.yaml")
    time.sleep(1)

    if not ok_reply:
        log_error("✖ required vComponent descriptor reply post failed")
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
        # Act 1's acknowledgement. requestShortAudioDescriptor publishes exactly one field -
        # success (IHdmiCecSink.h:251) - so this is the only claim the solicitation itself
        # supports. Nothing is asserted about the descriptor contents; see @expected_result.
        if _result_object(curl_response).get("success") is not True:
            log_error("✖ requestShortAudioDescriptor did not acknowledge success")
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
        # Neither True nor False is a claim this testcase can honestly make, and a descriptor
        # exchange is not what sets the flag anyway: it mirrors
        # HdmiCecSinkImplementation::hdmiCecAudioDeviceConnected, set only when a peer is
        # discovered at logical address 5. The sink's own L2 suite asserts the counter-intuitive
        # value for exactly this reason - EXPECT_FALSE(connected) at
        # ../../L2Tests/tests/HdmiCecSink_L2Test.cpp:1827 over COM-RPC and
        # EXPECT_FALSE(result["connected"].Boolean()) at :2539 over JSON-RPC - because no audio
        # system is ever discovered in that in-process host. This suite has never been executed,
        # so pinning the value would fail in one valid environment or the other. `success` is
        # different: the implementation sets it unconditionally, so requiring True is measured.
        if after_result.get("success") is True and isinstance(connected_after, bool):
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID23_Short_Audio_Descriptor_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {after}")
    except json.JSONDecodeError:
        log_error("Invalid JSON response")

    log_error("TCID23_Short_Audio_Descriptor_Flow Failed ❌")
    return False
