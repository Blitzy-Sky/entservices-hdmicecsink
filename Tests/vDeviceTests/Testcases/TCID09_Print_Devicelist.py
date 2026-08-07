"""
/**
 * @file TCID09_Print_Devicelist.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID09_Print_Devicelist
 * @details Invokes the sink plugin's printDeviceList diagnostic over JSON-RPC and validates
 *          the acknowledgement it returns. printDeviceList is a developer helper: the plugin
 *          walks its CEC device list and emits each present device's parameters - logical
 *          address, device type, physical address, OSD name, vendor ID, CEC version and the
 *          per-field update flags - into the PLUGIN LOG. None of that text is carried in the
 *          JSON-RPC reply, and this suite's curl transport reaches only the reply, so THE DUMP
 *          CONTENT IS NOT ASSERTED HERE: the case verifies that the request is accepted and
 *          acknowledged with both documented result members, and nothing further. Confirming
 *          what the dump contains needs the device log, which is out of reach at this level.
 *
 *          The case is read only: one request, no plugin state altered and nothing to
 *          restore, which is why it belongs in the suite's leading read-only block, ahead of
 *          the first write case.
 *
 * @precondition
 *  - Required plugin is active and reachable via JSON-RPC endpoint.
 *  - Target environment is ready for HDMI CEC emulation/command execution.
 *  - The suite's initialization module has seeded the CEC device list, so the dump walks real
 *    peers - the audio system at logical address 5 among them - rather than an empty list. An
 *    empty list is still acknowledged, so this case does not require the seeding to have
 *    succeeded; it only becomes a meaningful exercise of the per-device dump once it has.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The plugin acknowledges the request, returning "printed" and "success" in its result.
 *  - The dump itself lands in the plugin log and is not inspected by this testcase.
 *
 * @pass_criteria
 *  - result.success is True, result.printed is a boolean, and run_test() returns True.
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

# The six-symbol import set above is this suite's shared convention, so log_with_timing is
# imported even though this case formats its timing line inline; keeping the set uniform means a
# case that later needs the helper changes no import block. Nothing else belongs here: this
# module inspects no log, so it needs no process-spawning or file-reading import.


def run_test():
    start_time = time.perf_counter()

    log_info("Executing the curl command print device list")

    curl_response = send_curl_command(
        HdmiCecSinkApis.print_device_list
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # A transport failure comes back as a TRUTHY sentinel string, which the guard above cannot
    # catch; utils.py documents this startswith form as how callers are to detect it.
    if curl_response.startswith("< No response"):
        log_error("✖ curl command sent but WPEFramework returned no response")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    try:
        actual_output_response = json.loads(curl_response)
        result = actual_output_response.get("result", {})
        # The default only applies when the member is ABSENT, so a reply carrying
        # "result": null - or an error envelope with a non-object result - would leave a
        # non-dict here and turn the lookups below into an AttributeError escaping run_test.
        # Normalising the shape keeps a malformed reply a reported verdict rather than a
        # raised exception, which is what makes the bool-on-every-path guarantee literal.
        if not isinstance(result, dict):
            result = {}
        has_success = result.get("success") is True

        # printed is TYPE-asserted rather than required to be True: it is the plugin's own
        # report of whether it dumped, and it is reported even when the device list holds no
        # present device, so a True value would attest to nothing this case can verify.
        printed_flag = result.get("printed")
        has_printed_flag = isinstance(printed_flag, bool)
        log_info(f"Reported printed flag: {printed_flag}")

        if has_success and has_printed_flag:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID09_Print_Devicelist Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(
            f"Actual  : {json.dumps(actual_output_response, indent=2, sort_keys=True)}"
        )
        log_error("TCID09_Print_Devicelist Failed ❌")
        return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID09_Print_Devicelist Failed ❌")
        return False
