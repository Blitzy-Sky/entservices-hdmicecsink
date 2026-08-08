"""
/**
 * @file TCID12_Verify_Vendor_ID_Readback.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *        Reads back the sink's advertised vendor identifier over JSON-RPC and asserts that a
 *        usable identifier is being reported after the preceding case wrote one.
 *
 * @testcase TCID12_Verify_Vendor_ID_Readback
 * @details Dispatches org.rdk.HdmiCecSink.getVendorId and validates the response shape:
 *          result.success is True and result.vendorid is a non-empty string. The keys are
 *          exactly `vendorid` and `success`, per IHdmiCecSink.h
 *          GetVendorId(string &vendorid, bool &success); the differently cased `vendorID`
 *          returned by getActiveSource describes a PEER's vendor and is not read here.
 *
 *          CONSUMER OF AN ORDERED PAIR - the one thing to understand before editing this
 *          module. SuitManager.py registers TCID11_Set_Vendor_ID at position 11 and this case
 *          at position 12, and that order is load-bearing rather than cosmetic: TCID11
 *          ESTABLISHES the vendor identifier and this case READS IT BACK. Two consequences
 *          follow, and both are deliberate. This module WRITES NOTHING - it issues a single
 *          read and owns no payload - because writing here would collapse the pair into one
 *          self-confirming test and destroy the ordering evidence that gives the pair its
 *          value. And nothing is restored afterwards, because nothing was changed.
 *
 *          WHAT THIS ADDS OVER TCID04_Get_Vendor_ID, which dispatches the same request. The
 *          two cases are not duplicates; they occupy opposite sides of the suite's first
 *          write. TCID04 runs at position 4, inside the read-only block, and observes the
 *          BASELINE identifier - whatever the device already held, a value this suite never
 *          established. This case runs after the write and closes the loop: it is the read
 *          half of a write-then-read-back exchange. Neither call proves anything about the
 *          write on its own; the PAIR is what turns "setVendorId was acknowledged" into
 *          "setVendorId took effect", which is the device-level evidence a single call cannot
 *          produce.
 *
 *          NO EXPECTED IDENTIFIER IS PINNED HERE, and that is a correctness decision rather
 *          than a weaker assertion. The written value lives in exactly one place - the sibling
 *          write constant in HdmiCECSink_Curl.py, which this module deliberately does not
 *          reference - so restating it here would couple two files that must be able to change
 *          independently. A literal would further assume the device echoes the written text byte
 *          for byte, which the interface does not promise: the identifier is rendered from a
 *          three-byte VendorID, so prefix, case and zero-padding are the plugin's business. The
 *          shape is therefore asserted and the observed value LOGGED, so a mismatch is diagnosed
 *          from evidence rather than hidden behind a brittle equality check.
 *
 *          Sound in isolation as well as in sequence: an identifier is reported whether or not
 *          TCID11 has run, so under a name filter this case still asserts something real - a
 *          reachable plugin reporting a usable identifier - rather than passing vacuously.
 *
 * @precondition
 *  - Required plugin is active and reachable via JSON-RPC endpoint: a device under test -
 *    physical hardware or a QEMU target - hosts org.rdk.HdmiCecSink and answers at
 *    utils.WPEFRAMEWORK_JSONRPC_URL.
 *  - Init_Devicelist_Populate.run_test() has already seeded the CEC topology and left HDMI-CEC
 *    enabled; SuitManager.py runs it once, before the first test case.
 *  - TCID11_Set_Vendor_ID has run at the preceding registration position and written the vendor
 *    identifier this case reads back. TCID11 deliberately does not restore the previous value,
 *    precisely so that it is still in place here.
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
 *  - getVendorId answers with result.success True and a non-empty result.vendorid string,
 *    confirming that the identifier established by TCID11_Set_Vendor_ID is being advertised.
 *
 * @pass_criteria
 *  - result.success is True, result.vendorid is a non-empty string, and run_test() returns True.
 *
 * @failure_criteria
 *  - An empty or sentinel response, a JSON parsing error, a missing, non-string or blank
 *    vendorid, a success value other than True, or run_test() returning False.
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

# The block above is the six-symbol contract every case in this suite is written against, kept
# intact rather than pruned per case so that every module here presents an identical import block
# and an added or dropped helper shows up in a diff. log_with_timing has no call site because the
# pass path applies the HDMICEC_TIMING_ENABLED gate inline - the same gate that helper implements -
# keeping the decoration visible where the message is emitted. That unused symbol is this module's
# only static-analysis finding, and this comment is the record of it rather than a suppression
# pragma.


def run_test():
    '''Read the vendor identifier back and validate the shape of the response.

    The read half of the ordered pair whose write half is TCID11_Set_Vendor_ID. Shape-only by
    design: the identifier's text is owned by the sibling write constant in HdmiCECSink_Curl.py,
    so this case asserts that a usable identifier is reported and logs what it was.
    Returns:
        True when result.success is True and result.vendorid is a non-empty string; False on a
        transport failure, a JSON parsing failure, or a shape mismatch. A bool is returned on
        every path, which is the contract SuitManager.py binds.
    '''
    start_time = time.perf_counter()

    log_info("Executing the curl command get vendor id for readback verification")

    # THE ONLY REQUEST THIS MODULE MAKES, and it is a read. The value under test was established
    # by TCID11_Set_Vendor_ID at the preceding registration position; this case never writes, so
    # the pair's division of labour - one writer, one reader - stays intact. The constant is
    # dispatched verbatim: HdmiCECSink_Curl.py owns the method name, payload and timeout, and
    # utils.send_curl_command runs it as an argv list with no shell, so nothing is assembled here.
    curl_response = send_curl_command(HdmiCecSinkApis.get_vendor_id)

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # utils.send_curl_command reports every transport failure as its byte-exact
    # NO_RESPONSE_SENTINEL, a TRUTHY string that therefore survives the guard above and needs its
    # own test - the prefix check utils.py documents. Without it an unreachable endpoint would be
    # carried into json.loads and misreported as a malformed payload rather than a missing one,
    # which points at the plugin instead of at the absent device.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    try:
        parsed = json.loads(curl_response)

        # A JSON-RPC envelope is an object carrying an object-valued "result". Anything else
        # json.loads accepts - a bare list, a scalar, or a non-mapping "result" - has no member
        # to read, so it is normalised to an empty mapping and reported as a shape mismatch below
        # instead of raising AttributeError out of run_test() and breaking the bool-on-every-path
        # contract above.
        result = parsed.get("result") if isinstance(parsed, dict) else None
        if not isinstance(result, dict):
            result = {}

        vendor_id = result.get("vendorid")

        # Logged, not compared. The exact identifier is owned by the sibling write constant in
        # HdmiCECSink_Curl.py that TCID11_Set_Vendor_ID dispatches, so printing it is how the
        # pair's outcome becomes visible in the run record without this module restating a value
        # it does not own.
        log_info(f"Read-back vendorid: {vendor_id!r}")

        # `is True` rather than a truthy test, so a JSON 1 or "true" cannot pass for a boolean
        # success. Non-empty AFTER strip() is the strongest honest assertion available: the
        # identifier is rendered from a three-byte VendorID, so a blank or whitespace-only string
        # is always a defect, while any particular value is the write constant's business and not
        # this module's.
        if (
            result.get("success") is True
            and isinstance(vendor_id, str)
            and vendor_id.strip() != ""
        ):
            elapsed_time = time.perf_counter() - start_time
            log_success(log_with_timing("TCID12_Verify_Vendor_ID_Readback Passed ✅", elapsed_time))
            return True

        log_warning("Expected: result.success True and a non-empty string result.vendorid")
        log_warning(f"Actual  : {json.dumps(parsed, indent=2, sort_keys=True)}")
        log_error("TCID12_Verify_Vendor_ID_Readback Failed ❌")
        return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID12_Verify_Vendor_ID_Readback Failed ❌")
        return False
    # NO RESTORE CLAUSE, and its absence is correct rather than an omission. This module changes
    # nothing - it issues one read - so there is no prior state to put back. No artificial wait is
    # needed either: send_curl_command is synchronous, and the write this case verifies completed
    # in the preceding case. The device is left exactly as it was found for the cases that follow.
