"""
/**
 * @file TCID11_Set_Vendor_ID.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *        Writes the sink's advertised vendor identifier over JSON-RPC and asserts the
 *        success acknowledgement.
 *
 * @testcase TCID11_Set_Vendor_ID
 * @details Dispatches org.rdk.HdmiCecSink.setVendorId with the vendor identifier published by
 *          HdmiCECSink_Curl.set_vendor_id and asserts that the device answers with the whole
 *          expected JSON-RPC envelope, success member included. The request - method,
 *          parameters and the vendor identifier itself - is owned entirely by that sibling
 *          constant; this module never composes a payload of its own, so the value written
 *          here cannot drift from the value the readback case expects.
 *
 *          PRODUCER OF AN ORDERED PAIR - the one thing to understand before editing this
 *          module. SuitManager.py registers this case at position 11 and
 *          TCID12_Verify_Vendor_ID_Readback at position 12, and that order is load-bearing
 *          rather than cosmetic: this case ESTABLISHES the vendor identifier that TCID12 READS
 *          BACK through getVendorId. Two consequences follow, and both are deliberate. This
 *          module performs no readback of its own, because verification is TCID12's
 *          responsibility and duplicating it here would blur the pair's division of labour.
 *          And this module DELIBERATELY DOES NOT RESTORE the previous vendor identifier: a
 *          restore would overwrite the very value the next case exists to observe, turning a
 *          green pair red.
 *
 *          The residual state that leaves behind is safe by suite design rather than by luck.
 *          TCID04_Get_Vendor_ID runs earlier, at position 4, so the device's original
 *          identifier is observed before this case writes over it; and
 *          TCID28_Invalid_VendorID_Nochange re-establishes the same value later, at position
 *          28, from the same sibling constant. No case that follows this one depends on the
 *          pre-existing identifier.
 *
 *          Boundary and malformed-input sweeping of setVendorId is not attempted here. It is
 *          already covered at L1 - setVendorId_Boundary, setVendorId_MinValue,
 *          setVendorId_ValidHex, setVendorId_InvalidFormat, setVendorIdParamMissing and
 *          MalformedJSON_setVendorId - and the sink command module publishes no boundary
 *          constant. What is missing, and what this case supplies, is the device-level leg.
 *
 * @precondition
 *  - Required plugin is active and reachable via JSON-RPC endpoint: a device under test -
 *    physical hardware or a QEMU target - hosts org.rdk.HdmiCecSink and answers at
 *    utils.WPEFRAMEWORK_JSONRPC_URL.
 *  - Init_Devicelist_Populate.run_test() has already seeded the CEC topology and left HDMI-CEC
 *    enabled; SuitManager.py runs it once, before the first test case.
 *  - This suite is AUTHORED, NOT EXECUTED in this repository. No continuous integration
 *    workflow runs it, none of the prerequisites above is present in a build environment, and
 *    nothing described here has been observed against a live device or emulator.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - setVendorId is accepted, and the sink advertises the vendor identifier written here until
 *    TCID12_Verify_Vendor_ID_Readback has read it back.
 *
 * @pass_criteria
 *  - The reply equals {"jsonrpc":"2.0","id":42,"result":{"success":true}} and run_test()
 *    returns True.
 *
 * @failure_criteria
 *  - An empty or sentinel response, a payload differing from the expected envelope in any
 *    member, a JSON parse error, or run_test() returning False.
 */
"""


import time
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


def run_test():
    start_time = time.perf_counter()

    # The WHOLE envelope is compared, not just the success member, so a reply carrying the right
    # success flag under a wrong id or a wrong protocol version is still a failure. The id is 42
    # because every request constant in HdmiCECSink_Curl.py sends "id":42.
    expected_output_response = {
        "jsonrpc": "2.0",
        "id": 42,
        "result": {
            "success": True
        }
    }

    log_info("Executing the curl command set vendor id")

    # The sibling constant is dispatched verbatim. The vendor identifier lives there and only
    # there - TCID12_Verify_Vendor_ID_Readback and TCID28_Invalid_VendorID_Nochange resolve the
    # same definition - so no hex literal and no hand-built payload appears in this module, and
    # an edit to one side of the pair cannot silently desynchronise the other.
    curl_response = send_curl_command(
        HdmiCecSinkApis.set_vendor_id
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # utils.send_curl_command reports every transport failure as the byte-exact
    # "< No response from WPEFramework >" sentinel. That is a TRUTHY string, so it survives the
    # emptiness check above and needs its own guard - the prefix test utils.py documents. Without
    # it an unreachable device would be carried into json.loads and misreported as a payload
    # mismatch, which points at the plugin instead of at the missing endpoint.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    try:
        if json.loads(curl_response) == expected_output_response:
            elapsed_time = time.perf_counter() - start_time
            log_success(log_with_timing("TCID11_Set_Vendor_ID Passed ✅", elapsed_time))
            return True
        else:
            log_error("TCID11_Set_Vendor_ID Failed ❌")
            return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID11_Set_Vendor_ID Failed ❌")
        return False
    # NO `finally:` RESTORE CLAUSE HERE, AND ITS ABSENCE IS THE DELIBERATE CHOICE.
    #
    # This is where the suite's other write cases put their restore - TCID08_Set_Enabled_False in
    # the source suite resets its flag from exactly this position - and this case must not. The
    # vendor identifier written above IS the fixture that TCID12_Verify_Vendor_ID_Readback runs
    # against at the next registration position; restoring it here would delete that fixture
    # before its only consumer ever saw it, and TCID12 would fail for a reason with nothing to do
    # with the plugin under test. The dependency between the two cases is therefore documented
    # rather than accidental, and SuitManager.py's registration list is the mechanism that
    # guarantees the ordering it needs.
