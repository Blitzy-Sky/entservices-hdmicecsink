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
 *  - The reply is a JSON-RPC 2.0 envelope answering the id this case sent, carries an "error"
 *    object and no "result" member, and that error's code is one of exactly two values:
 *    -32601, or 22. Those are the two renderings of a single framework status,
 *    Core::ERROR_UNKNOWN_KEY - the status Thunder's dispatcher returns when no registered
 *    handler matches the method name - so together they are the dispatcher saying the method
 *    does not exist, and no third code says it.
 *  - Should the method ever be published, these criteria invert to result.success being True
 *    and result.CECVersion being a non-empty string; the tripwire branch in run_test() says so
 *    in its diagnostic rather than leaving the next reader to work it out.
 *
 * @failure_criteria
 *  - A CECVersion payload is returned (the published surface changed), the command was not
 *    dispatched, no response arrived, a JSON parsing error occurred, the reply is not a
 *    JSON-RPC 2.0 envelope, the reply answers a different request id, the error member carries
 *    no integer code, or the code is ANY value other than -32601 or 22. That last clause is
 *    what keeps a different fault from being read as this one: -32603 (internal error), -32602
 *    (invalid parameters), -32604 (privileged), -32000 (timeout) and 2 (ERROR_UNAVAILABLE - the
 *    service absent, which is this case's precondition rather than its subject) are all
 *    refusals, and none of them is evidence that the METHOD is unpublished.
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
    expected_request_id,
    sanitise_for_log,
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
    log_warning(f"Response: {sanitise_for_log(curl_response, max_chars=2048)}")

    try:
        parsed = json.loads(curl_response)

        # ENVELOPE AND CORRELATION, CHECKED HERE AND NOT ONLY UPSTREAM. utils.send_curl_command
        # already requires curl to have exited zero, the HTTP status to have been 2xx, the body
        # to have been exactly one syntactically valid JSON-RPC envelope, and the id to match
        # the one sent - so a caller that only needs a reply is safe without this block. This
        # case needs more than a reply: its verdict is the assertion "the dispatcher rejected
        # THIS call because the method does not exist", and an error object belonging to some
        # other request, or arriving in something that is not a JSON-RPC envelope at all, would
        # satisfy the code check below while saying nothing about this method. So the two
        # properties the verdict rests on are asserted where the verdict is formed.
        if not isinstance(parsed, dict):
            log_error(
                "✖ the reply is valid JSON but not a JSON-RPC envelope "
                f"({type(parsed).__name__}), so no error member can be attributed to this call"
            )
            log_error("TCID05_Get_CEC_Version Failed ❌")
            return False

        if parsed.get("jsonrpc") != "2.0":
            log_error(
                "✖ the reply does not declare jsonrpc 2.0 "
                f"(jsonrpc={sanitise_for_log(parsed.get('jsonrpc'), max_chars=32)}), so it is "
                "not a response this case can read a rejection out of"
            )
            log_error("TCID05_Get_CEC_Version Failed ❌")
            return False

        sent_id = expected_request_id(HdmiCecSinkApis.get_cec_version_unregistered)
        if sent_id is None:
            log_error(
                "✖ get_cec_version_unregistered carries no readable JSON-RPC id, so the reply "
                "cannot be correlated to the call this case makes"
            )
            log_error("TCID05_Get_CEC_Version Failed ❌")
            return False
        if str(parsed.get("id")) != str(sent_id):
            log_error(
                f"✖ the reply answers request id "
                f"{sanitise_for_log(parsed.get('id'), max_chars=32)}, not the {sent_id} this "
                "case sent, so its error member describes a different call"
            )
            log_error("TCID05_Get_CEC_Version Failed ❌")
            return False

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
            # THE ACCEPTED CODES ARE THE TWO RENDERINGS OF ONE FRAMEWORK STATUS, AND NOTHING
            # ELSE. Thunder's plugin dispatcher initialises its result to Core::ERROR_UNKNOWN_KEY
            # and returns it when no registered handler matches the method name
            # (Thunder/Source/plugins/JSONRPC.h:605), and Core::JSONRPC::Error::Info::SetError
            # maps that status to the canonical JSON-RPC -32601 "Method not found"
            # (Thunder/Source/core/JSONRPC.h, the ERROR_UNKNOWN_KEY case of the switch). The
            # value of ERROR_UNKNOWN_KEY is 22 (Thunder/Source/core/Portability.h:850), which is
            # the raw status the L1 measurement recorded from an in-process invocation where no
            # SetError mapping is applied. So -32601 and 22 are the same statement made on two
            # surfaces, and no third code says it.
            #
            # (The symbol in the earlier note here was wrong and is corrected: Thunder has no
            # Core::ERROR_UNKNOWN_METHOD - grep of the vendored tree finds none - and 22 is
            # ERROR_UNKNOWN_KEY.)
            #
            # ANY OTHER CODE IS A DIFFERENT FAULT AND FAILS. -32603 is an internal error, -32602
            # invalid parameters, -32604 a privilege refusal, -32000 a timeout, and 2
            # (ERROR_UNAVAILABLE) is the service not being there at all - which is the
            # precondition of this case rather than its subject. Treating those as "the method
            # does not exist" is how a deactivated plugin, a malformed request or an unreachable
            # service would report this case as passing, so each of them ends it as a failure
            # naming the code that was actually returned.
            method_not_found_codes = (-32601, 22)
            error_code = error.get("code")
            if not isinstance(error_code, int) or isinstance(error_code, bool):
                log_error(
                    "✖ the reply's error member carries no integer code "
                    f"(code={sanitise_for_log(error_code, max_chars=64)}), so the dispatcher's "
                    "reason for refusing cannot be established"
                )
                log_error("TCID05_Get_CEC_Version Failed ❌")
                return False
            if error_code not in method_not_found_codes:
                log_error(
                    f"✖ JSON-RPC error code {error_code} is not one of "
                    f"{method_not_found_codes}, the two renderings of ERROR_UNKNOWN_KEY. The "
                    "call was refused for some other reason - a deactivated plugin, an invalid "
                    "request or an unavailable service - and this case asserts specifically "
                    "that the METHOD is unpublished, so that is not the contract under test. "
                    f"Error text: {sanitise_for_log(error.get('message'), max_chars=256)}"
                )
                log_error("TCID05_Get_CEC_Version Failed ❌")
                return False

            log_success(
                f"✔ the dispatcher reported the method as unknown (code {error_code})"
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
