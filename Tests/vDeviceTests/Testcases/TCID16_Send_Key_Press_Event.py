"""
/**
 * @file TCID16_Send_Key_Press_Event.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID16_Send_Key_Press_Event
 * @details Sends one key-press event from the sink to a peer by dispatching
 *          org.rdk.HdmiCecSink.sendKeyPressEvent over JSON-RPC, and asserts the success
 *          acknowledgement. This closes the device-level leg of SendKeyPressEvent, which
 *          the coverage register records as covered by the sink's own L2 suite with no
 *          end-to-end verification behind it.
 *
 *          The OnKeyPressEvent notification the resulting frame provokes is NOT observed
 *          here and nothing is asserted about it: this level reaches the plugin over plain
 *          curl, which cannot subscribe to a Thunder notification channel, so an event
 *          assertion at L3 would be unfounded. That notification stays uncovered, and is
 *          reported as uncovered rather than implied to be tested.
 *
 *          Adjacent coverage, deliberately not duplicated here: the pressed and released
 *          pair with the minimum and boundary key codes belong to
 *          TCID26_User_Control_Pressed_Released_Flow, and the rejected-argument cases are
 *          already asserted by the sink L1 suite as sendKeyPressEvent_InvalidLogicalAddress,
 *          sendKeyPressEvent_InvalidKeyCode, sendKeyPressEvent_BoundaryKeyCode and
 *          sendKeyPressEvent_MinKeyCode. No boundary sweep is attempted at this level.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework
 *    with the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the CEC topology, so the logical address carried
 *    by the dispatched command resolves to a real emulated peer - the Audio System, which
 *    is the suite's bootstrap peer - rather than to an empty address.
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
 *  - The plugin acknowledges the request with {"success": true}. The notification the key
 *    press produces is not observable at this level and is therefore not asserted.
 *
 * @pass_criteria
 *  - The reply equals {"jsonrpc":"2.0","id":42,"result":{"success":true}} and run_test()
 *    returns True.
 *
 * @failure_criteria
 *  - A response mismatch, a JSON parsing failure, an unreachable endpoint or an
 *    unavailable device-level prerequisite; run_test() then returns False.
 */
"""


import time
import os


import json
from utils import (
    send_curl_command,
    log_info,
    log_success,
    log_error,
    log_warning,
    log_with_timing
)
import HdmiCECSink_Curl as HdmiCecSinkApis

# log_with_timing belongs to the shared import contract every case in this suite is written
# against; it is kept for parity even though this case formats its own timing line below.


def run_test():
    '''Dispatch one sendKeyPressEvent call and verify the success acknowledgement.
    Returns:
        True when the plugin answers with the expected success envelope; False on a
        transport failure, a response mismatch or a body that is not valid JSON.
    '''
    start_time = time.perf_counter()

    expected_output_response = {
        "jsonrpc": "2.0",
        "id": 42,
        "result": {
            "success": True
        }
    }

    log_info("Executing the curl command send key press event")

    # The logicalAddress and keyCode of this scenario are carried by the command constant
    # itself, so they are never restated here and cannot drift out of step with it.
    curl_response = send_curl_command(
        HdmiCecSinkApis.send_key_press_event
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # A transport failure does not arrive empty: send_curl_command reports it with the
    # "< No response from WPEFramework >" sentinel, which is TRUTHY and so passes the guard
    # above untouched. Detecting it by prefix is the contract that helper documents; without
    # this check an unreachable endpoint would fall through to be parsed as a response.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    # A key press is transient and leaves no persistent plugin state behind, so this case
    # needs none of the restore clauses the suite's stateful write-side cases carry.
    try:
        if json.loads(curl_response) == expected_output_response:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID16_Send_Key_Press_Event Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
        else:
            log_error("TCID16_Send_Key_Press_Event Failed ❌")
            return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID16_Send_Key_Press_Event Failed ❌")
        return False
