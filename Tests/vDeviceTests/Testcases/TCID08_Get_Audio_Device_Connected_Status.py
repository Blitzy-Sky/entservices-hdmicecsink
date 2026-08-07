"""
/**
 * @file TCID08_Get_Audio_Device_Connected_Status.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID08_Get_Audio_Device_Connected_Status
 * @details Reads whether an HDMI-CEC audio device is currently connected to the sink by
 *          dispatching org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus over JSON-RPC, and
 *          validates the two fields the API publishes: result.connected and result.success.
 *          The connected flag is ENVIRONMENT-DEPENDENT - it mirrors whether the middleware
 *          has discovered an audio system at CEC logical address 5 - so it is asserted by
 *          TYPE rather than by value, and its observed value is logged. The comment above
 *          the assertion carries the evidence; that choice is deliberate and must not be
 *          strengthened into a value check.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework with
 *    the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the emulated topology, including the VAUDIO
 *    AudioSystem peer at CEC logical address 5, and has left HDMI-CEC enabled.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus answers with a JSON-RPC result object
 *    carrying success true and a boolean connected field.
 *
 * @pass_criteria
 *  - result.success is True, result.connected is a bool, and run_test() returns True.
 *
 * @failure_criteria
 *  - An empty or sentinel response, success not True, connected missing or not a bool, a JSON
 *    parsing error, or run_test() returns False.
 */
"""

import time
import os
import json

# The six-symbol utils import below is the shared contract every testcase module in this suite
# is written against. `log_with_timing` is deliberately retained even though this module gates
# its own timing line on HDMICEC_TIMING_ENABLED directly: keeping the set identical across all
# testcases is what lets one module be diffed against another, so the resulting single
# "imported but unused" lint note is accepted convention here rather than an oversight.
from utils import (
    send_curl_command,
    log_info,
    log_success,
    log_error,
    log_warning,
    log_with_timing
)
import HdmiCECSink_Curl as HdmiCecSinkApis


def run_test():
    start_time = time.perf_counter()

    log_info("Executing the curl command get audio device connected status")

    # The command is taken as a constant, never assembled here: HdmiCECSink_Curl.py owns the
    # method name, payload and timeout for this API, and a locally built request would be a
    # second definition free to drift from it.
    curl_response = send_curl_command(
        HdmiCecSinkApis.get_audio_device_connected_status
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # The transport failure guard that actually fires in this suite. utils.send_curl_command
    # returns the "< No response from WPEFramework >" sentinel - a TRUTHY string - for every
    # failure mode, so the falsy check above cannot catch one on its own. The prefix form is
    # the detection contract utils.py documents for callers.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    try:
        parsed = json.loads(curl_response)
        # A JSON-RPC error envelope carries "error" instead of "result", and a malformed body
        # could carry a non-object "result". Both collapse to an empty mapping so the checks
        # below report a missing field instead of raising out of run_test().
        result = parsed.get("result", {})
        if not isinstance(result, dict):
            result = {}
        connected = result.get("connected")

        # TYPE-ONLY ASSERTION ON `connected` - DO NOT STRENGTHEN THIS INTO A VALUE CHECK.
        # Neither True nor False is a claim this testcase can honestly make. The sink's own L2
        # suite asserts the counter-intuitive value for exactly this reason:
        # ../../L2Tests/tests/HdmiCecSink_L2Test.cpp:1931-1932 checks HasLabel("connected")
        # and then EXPECT_FALSE(result["connected"].Boolean()), because no audio system is
        # ever discovered in that in-process host. The flag mirrors
        # HdmiCecSinkImplementation::hdmiCecAudioDeviceConnected, false from construction and
        # set true only inside addDevice() when a peer appears at logical address 5
        # (HdmiCecSinkImplementation.cpp:2459), then false again by removeDevice() for that
        # address (:2503). Nothing forces that discovery to have completed by suite position
        # 8: the VAUDIO peer at address 5 is seeded by Init_Devicelist_Populate, and the ARC
        # exchange a reader might assume gates the flag is driven later, by
        # TCID20_ARC_Initiation_Flow at position 20. Pinning the value would therefore fail in
        # one valid environment or the other. `success` is different - the implementation sets
        # it unconditionally (:1331-1336) - so requiring True there is a measured claim.
        if result.get("success") is True and isinstance(connected, bool):
            log_info(f"Observed audio device connected state: {connected}")
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID08_Get_Audio_Device_Connected_Status Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {json.dumps(parsed, indent=2, sort_keys=True)}")
        log_error("TCID08_Get_Audio_Device_Connected_Status Failed ❌")
        return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID08_Get_Audio_Device_Connected_Status Failed ❌")
        return False
