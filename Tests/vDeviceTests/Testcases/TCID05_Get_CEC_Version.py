"""
/**
 * @file TCID05_Get_CEC_Version.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID05_Get_CEC_Version
 * @details Exercises the HDMI CEC Sink plugin's CEC-version surface at device level over
 *          JSON-RPC and validates the reply against the plugin's published-method contract.
 *          A registered reader would answer {"CECVersion": "<version>", "success": true}, so
 *          this case inspects exactly result.CECVersion and result.success - and asserts their
 *          ABSENCE, because org.rdk.HdmiCecSink.getCecVersion is not a registered JSON-RPC
 *          method. HdmiCECSink_Curl.py records four independent confirmations of that and
 *          names its command accordingly (get_cec_version_unregistered); the mechanism is that
 *          the method is absent from IHdmiCecSink.h's published set and therefore from
 *          Exchange::JHdmiCecSink::Register, the plugin's only JSON-RPC registration path.
 *
 *          This module gives the method name its first device-level exercise. Its L1
 *          counterpart, HdmiCecSinkInitializedEventDsTest.DISABLED_getCecVersion in
 *          ../../L1Tests/tests/test_HdmiCecSink.cpp, is AAP Directive 5 defect #5 and remains
 *          disabled: this module does NOT repair it and makes no claim that it does. The
 *          analysis recorded above that test measured the invocation returning 22
 *          (Core::ERROR_UNKNOWN_METHOD) with an empty response, so the cause is the missing
 *          published method, not the commented-out RFC expectation beside it. Publishing the
 *          method is a production change - a getCecVersion declaration on
 *          Exchange::IHdmiCecSink so ThunderTools generates its binding, plus a plugin
 *          implementation so Register() publishes it - which AAP Directive 6 requires be
 *          reported rather than made. It is reported here and deliberately left unmade.
 *
 *          The case is consequently a regression tripwire in the opposite direction too: if
 *          the method is ever published, this test fails and states exactly what to change.
 *          Where the CEC version IS observable device-side: the <Give CEC Version> exchange in
 *          vcomponent_configurations/hdmicec/hdmicec_vcomponent_cec_responses.yaml, and the
 *          device records returned by getDeviceList (see TCID02_Get_Devicelist).
 *
 *          Read-only and self-contained: one JSON-RPC query, no device state written, no
 *          shared state to capture or restore, no sleep, and no service started or emulated.
 *
 * @precondition
 *  - Required plugin is active and reachable via JSON-RPC endpoint.
 *  - Target environment is ready for HDMI CEC emulation/command execution.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The dispatcher reports org.rdk.HdmiCecSink.getCecVersion as unavailable: the reply
 *    carries an "error" member and no "result" member, and no CECVersion value comes back.
 *
 * @pass_criteria
 *  - The reply carries a JSON-RPC "error" object and no "result" member, result.success is
 *    therefore not True, no result.CECVersion string is returned, and run_test() returns True.
 *  - Should the method ever be published, these criteria invert to result.success being True
 *    and result.CECVersion being a non-empty string; the tripwire branch in run_test() says so
 *    in its diagnostic rather than leaving the next reader to work it out.
 *
 * @failure_criteria
 *  - A CECVersion payload is returned (the published surface changed), the command was not
 *    dispatched, no response arrived, a JSON parsing error occurred, or run_test() returns
 *    False.
 */
"""


import time
import os


import json
# log_with_timing is imported and not called, deliberately: it is part of the six-symbol import
# set every Band A test case in this suite family carries (28 of the 33 source-plugin cases
# import it and none call it), and the timing line below is emitted through log_success so that
# every case's output lines up. pyflakes reports the unused name; the suite convention wins and
# the tension is recorded here rather than resolved silently in either direction.
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

    log_info("Executing the curl command get CEC version")

    curl_response = send_curl_command(
        HdmiCecSinkApis.get_cec_version_unregistered
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # Both guards are needed and neither is redundant. The falsy guard above is the suite's
    # conventional first check and covers a dependency that stops honouring its contract; the
    # one below is the guard that actually fires today, because send_curl_command reports every
    # transport, tokenisation and parse failure as the TRUTHY utils.NO_RESPONSE_SENTINEL string
    # ("< No response from WPEFramework >"). That distinction is load-bearing for this
    # particular case: an unreachable endpoint must never be mistaken for a dispatcher
    # rejecting the method, which is exactly what this test treats as a pass.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    try:
        parsed = json.loads(curl_response)
        result = parsed.get("result", {})
        error = parsed.get("error")

        # A JSON-RPC 2.0 reply carries "result" or "error", never both, so the presence of a
        # result member is what says the method answered at all. Membership is tested on the
        # envelope rather than on the value so that an off-contract payload type still counts
        # as an answer, and result_fields keeps the field reads below from raising on one.
        answered_with_result = "result" in parsed
        result_fields = result if isinstance(result, dict) else {}

        # The published shape would be {"CECVersion": "<version>", "success": true} - the exact
        # reply the disabled L1 test expects. The version is read for logging and never pinned
        # to "1.4": that literal is only the plugin's static default
        # (HdmiCecSinkImplementation.cpp:103), overridable through the RFC parameter
        # Device.DeviceInfo.X_RDKCENTRAL-COM_RFC.Feature.HdmiCecSink.CECVersion that
        # getCecVersion() reads, so a 2.0-provisioned device would legitimately report 2.0 and
        # pinning the literal would fail such a device for being correctly provisioned.
        reported_version = result_fields.get("CECVersion")
        version_payload_returned = (
            result_fields.get("success") is True
            and isinstance(reported_version, str)
            and reported_version.strip() != ""
        )

        # The contract under test. A result member and an error member are mutually exclusive,
        # so requiring the error and the absence of the result states "the dispatcher has no
        # such method" once, without a second predicate that could never fire.
        rejected = isinstance(error, dict) and not answered_with_result

        if rejected:
            # Thunder maps Core::ERROR_UNKNOWN_KEY to the canonical JSON-RPC -32601 "Method not
            # found" (Thunder/Source/core/JSONRPC.h:93) and passes any other framework status
            # through its default branch unchanged, which is why an unpublished method can be
            # reported as either -32601 or 22 (Core::ERROR_UNKNOWN_METHOD - the value measured
            # at L1). Both are the dispatcher saying the method does not exist, so both are
            # treated as canonical; any other code is still a rejection, but it is logged so it
            # cannot pass by unnoticed.
            method_not_found_codes = (-32601, 22)
            error_code = error.get("code")
            if error_code not in method_not_found_codes:
                log_warning(
                    f"JSON-RPC error code {error_code} is not one of "
                    f"{method_not_found_codes}; the method is still reported as unavailable, "
                    "which is the contract under test"
                )
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID05_Get_CEC_Version Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        if answered_with_result:
            if version_payload_returned:
                log_warning(
                    f"CECVersion reported as {reported_version!r} with success true: "
                    "org.rdk.HdmiCecSink.getCecVersion now answers as a published method. "
                    "Rename get_cec_version_unregistered back to get_cec_version in "
                    "HdmiCECSink_Curl.py, invert the assertion above, and re-enable "
                    "DISABLED_getCecVersion in ../../L1Tests/tests/test_HdmiCecSink.cpp."
                )
            else:
                log_warning(
                    "The dispatcher answered with a result member that is not the published "
                    f"shape (CECVersion={reported_version!r}, "
                    f"success={result_fields.get('success')!r}), so neither the documented "
                    "rejection nor a usable version reading was obtained."
                )
        log_warning(f"Actual  : {json.dumps(parsed, indent=2, sort_keys=True)}")
        log_error("TCID05_Get_CEC_Version Failed ❌")
        return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID05_Get_CEC_Version Failed ❌")
        return False
