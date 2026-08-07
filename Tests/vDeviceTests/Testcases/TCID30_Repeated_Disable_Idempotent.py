"""
/**
 * @file TCID30_Repeated_Disable_Idempotent.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID30_Repeated_Disable_Idempotent
 * @details Validates that org.rdk.HdmiCecSink.setEnabled is idempotent in the disable
 *          direction: two consecutive setEnabled(false) requests must leave getEnabled
 *          reporting false rather than oscillating back to true. Both reads are logged;
 *          only the second is asserted. The module then restores the suite invariant
 *          itself - its last request is setEnabled(true), sent before any guard can
 *          return, so no failure path leaves HDMI-CEC disabled downstream.
 *          TCID31_Repeated_Enable_Idempotent runs next and re-verifies the enabled state.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable over the JSON-RPC endpoint.
 *  - Init_Devicelist_Populate has run, so HDMI-CEC starts enabled.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - After two consecutive disable requests the reported state is false, and HDMI-CEC
 *    is restored to enabled before the module returns.
 *
 * @pass_criteria
 *  - The second getEnabled response parses and reports result.enabled as False, and
 *    run_test() returns True.
 *
 * @failure_criteria
 *  - The request is not dispatched, the response is the no-response sentinel, the body
 *    does not parse, result.enabled is not False, or run_test() returns False.
 */
"""

import time
import os
import json
from utils import send_curl_command, log_success, log_error, log_warning
import HdmiCECSink_Curl as HdmiCecSinkApis


def run_test():
    start_time = time.perf_counter()

    # Legacy intent: getEnabled when already disabled.
    send_curl_command(HdmiCecSinkApis.set_enabled_false)
    first_get = send_curl_command(HdmiCecSinkApis.get_enabled)
    send_curl_command(HdmiCecSinkApis.set_enabled_false)
    second_get = send_curl_command(HdmiCecSinkApis.get_enabled)
    # Restoration, deliberately last: every guard below is reached only AFTER this call,
    # so no early return can leak a disabled plugin into the rest of the suite.
    # TCID31_Repeated_Enable_Idempotent follows immediately and verifies the enabled state.
    send_curl_command(HdmiCecSinkApis.set_enabled_true)

    if not second_get:
        log_error("✖ getEnabled command not sent")
        return False
    # The sentinel is a non-empty string, so the falsy check above cannot catch it.
    if second_get.startswith("< No response"):
        log_error("✖ getEnabled returned no response from WPEFramework")
        return False

    log_warning(f"First enabled response: {first_get}")
    log_warning(f"Final enabled response: {second_get}")
    try:
        body = json.loads(second_get)
        enabled = body.get("result", {}).get("enabled")
        # `is False`, not `not enabled`: a missing key yields None and must not pass.
        if enabled is False:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID30_Repeated_Disable_Idempotent Passed"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
    except Exception:
        pass

    log_error("TCID30_Repeated_Disable_Idempotent Failed")
    return False
