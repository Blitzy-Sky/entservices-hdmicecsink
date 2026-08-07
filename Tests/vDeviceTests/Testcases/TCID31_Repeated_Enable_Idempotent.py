"""
/**
 * @file TCID31_Repeated_Enable_Idempotent.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID31_Repeated_Enable_Idempotent
 * @details Validates that org.rdk.HdmiCecSink.setEnabled is idempotent in the enable
 *          direction: two consecutive setEnabled(true) requests must leave getEnabled
 *          reporting true rather than oscillating back to false. Both reads are logged,
 *          only the second asserted. This is the enable-side twin of
 *          TCID30_Repeated_Disable_Idempotent, which disables at the preceding position.
 *          The enabled state it leaves behind IS the suite invariant - established by
 *          Init_Devicelist_Populate, asserted by TCID01_Get_Enabled_Status - so no
 *          restoration is needed here, and enabling an enabled plugin is a legitimate
 *          no-op, so the module is correct in isolation too.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable over the JSON-RPC endpoint.
 *  - Init_Devicelist_Populate has run, so HDMI-CEC starts enabled.
 *  - TCID30_Repeated_Disable_Idempotent has run at the preceding position.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - After two consecutive enable requests the reported state is true, and HDMI-CEC is
 *    left enabled for the remaining test cases.
 *
 * @pass_criteria
 *  - The second getEnabled response parses and reports result.enabled as True, and
 *    run_test() returns True.
 *
 * @failure_criteria
 *  - The request is not dispatched, the response is the no-response sentinel, the body
 *    does not parse, result.enabled is not True, or run_test() returns False.
 */
"""

import time
import os
import json
from utils import send_curl_command, log_success, log_error, log_warning
import HdmiCECSink_Curl as HdmiCecSinkApis


def run_test():
    start_time = time.perf_counter()

    # Legacy intent: getEnabled when already enabled.
    send_curl_command(HdmiCecSinkApis.set_enabled_true)
    first_get = send_curl_command(HdmiCecSinkApis.get_enabled)
    send_curl_command(HdmiCecSinkApis.set_enabled_true)
    second_get = send_curl_command(HdmiCecSinkApis.get_enabled)
    # No restoration request follows, deliberately: enabled IS the suite invariant, so this
    # module's terminal state is already the wanted one. That is the intentional asymmetry
    # with TCID30_Repeated_Disable_Idempotent, which must send a trailing set_enabled_true.

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
        # `is True`: a missing key yields None, and a truthy non-bool is not the contract.
        if enabled is True:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID31_Repeated_Enable_Idempotent Passed"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
    except Exception:
        pass

    log_error("TCID31_Repeated_Enable_Idempotent Failed")
    return False
