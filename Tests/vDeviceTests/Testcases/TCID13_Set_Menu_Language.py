"""
/**
 * @file TCID13_Set_Menu_Language.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID13_Set_Menu_Language
 * @details Sets the sink television's menu language through the org.rdk.HdmiCecSink
 *          setMenuLanguage JSON-RPC method and asserts the plugin's success
 *          acknowledgement. This closes the end-to-end leg of the SetMenuLanguage gap:
 *          the API is exercised by the sink's own L2 suite but had no device-level case,
 *          the asymmetry catalogued as the missing sink vDeviceTests suite (rank 22, P1).
 *
 *          WHAT THE SINGLE CALL DRIVES. The implementation stores the supplied language
 *          through setCurrentLanguage() and broadcasts <Set Menu Language> through
 *          sendMenuLanguage(), whose operand is a three-byte ISO 639-2 code because the
 *          middleware Language operand is fixed at three bytes. The case therefore covers
 *          the outbound language path from JSON-RPC parameter to encoded CEC frame. The
 *          mapToIso639_2() converter is NOT claimed: it maps a BCP-47 presentation language
 *          on the INBOUND user-settings path, and setMenuLanguage forwards its parameter
 *          verbatim without traversing it.
 *
 *          THERE IS NO READ-BACK. The sink interface publishes setMenuLanguage with no
 *          matching getter at any transport, so the applied value cannot be re-read at this
 *          level and no verification of it is claimed. The case asserts exactly what the
 *          transport can observe: the acknowledgement envelope.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is reachable and hosts an
 *    active org.rdk.HdmiCecSink plugin answering JSON-RPC at utils.WPEFRAMEWORK_JSONRPC_URL.
 *  - Init_Devicelist_Populate has seeded the emulated CEC topology and left HDMI-CEC
 *    enabled, so the sink holds an allocated logical address: setCurrentLanguage() and
 *    sendMenuLanguage() both return early while the sink is still UNREGISTERED.
 *  - This suite is AUTHORED, NOT EXECUTED in this repository. No continuous integration
 *    workflow runs it, none of the prerequisites above is present in a build environment,
 *    and nothing described here has been observed against a live device or emulator.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The plugin acknowledges the request with {"success": true}. The applied value is not
 *    read back, because the sink exposes no getter for the menu language.
 *
 * @pass_criteria
 *  - The reply equals {"jsonrpc":"2.0","id":42,"result":{"success":true}} and run_test()
 *    returns True.
 *
 * @failure_criteria
 *  - Response mismatch, command failure, JSON parsing error, or testcase returns False.
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

# log_with_timing is imported but not called, which is deliberate rather than an oversight:
# the timing decoration below is applied inline, exactly as the device-level cases of the
# companion HDMI-CEC suite apply it - 28 of its 33 cases carry this symbol and none calls it.
# These six are the shared Testcases import contract, so the symbol is kept for consistency
# with the sibling cases rather than trimmed to silence one linter finding.


def run_test():
    start_time = time.perf_counter()

    expected_output_response = {
        "jsonrpc": "2.0",
        "id": 42,
        "result": {
            "success": True
        }
    }

    log_info("Executing the curl command set menu language")

    # The shared constant is dispatched as-is: the language it carries, the endpoint it
    # targets and its timeout all belong to HdmiCECSink_Curl.py, and composing a payload here
    # would put a second, divergent definition of this request into the suite.
    curl_response = send_curl_command(
        HdmiCecSinkApis.set_menu_language
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # A transport failure arrives as the TRUTHY "< No response from WPEFramework >" sentinel,
    # not as an empty value, so the falsy check above cannot detect it on its own. Without
    # this guard the sentinel would fall through to json.loads and be reported as a parse
    # error - a wrong diagnosis of an unreachable endpoint.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    # RESIDUAL STATE, DELIBERATELY NOT RESTORED - this is where a finally-block restore would
    # sit, and its absence is a decision rather than an omission. The menu language is left at
    # the value the HdmiCECSink_Curl.set_menu_language constant encodes, so the residual is
    # deterministic: the same value after every run. It is also inert - no other case in this
    # suite reads the menu language, and the sink publishes no getter through which one could.
    # Restoring it would mean writing a second language chosen here, and request payloads
    # belong to the sibling curl module rather than to a test case.
    try:
        if json.loads(curl_response) == expected_output_response:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID13_Set_Menu_Language Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
        else:
            log_error("TCID13_Set_Menu_Language Failed ❌")
            return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID13_Set_Menu_Language Failed ❌")
        return False
