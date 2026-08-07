"""
/**
 * @file TCID28_Invalid_VendorID_Nochange.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID28_Invalid_VendorID_Nochange
 * @details Validates that a malformed org.rdk.HdmiCecSink.setVendorId request leaves the
 *          advertised vendor identifier unchanged: the identifier is established, read,
 *          subjected to a request whose parameter key is misspelled, then read again. Both
 *          reads are logged and compared; the two setVendorId replies are captured but not
 *          asserted, because a plugin may refuse an unrecognised parameter with an error or
 *          a bare acknowledgement, and pinning that choice would test the reply rather than
 *          the invariant. The misspelled key lives in HdmiCECSink_Curl.set_vendor_id_invalid
 *          and only there, so this module pins no vendor identifier and compares two
 *          observations. It is the negative leg of a triple - TCID11_Set_Vendor_ID is the
 *          positive write, TCID12_Verify_Vendor_ID_Readback the readback - and its first
 *          request re-establishes the value TCID11 wrote at position 11, a residual TCID11
 *          documents and delegates here, so no restore clause is needed on any path.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable over the JSON-RPC endpoint.
 *  - Init_Devicelist_Populate has run, so the CEC topology is seeded.
 *  - AUTHORED, NOT EXECUTED in this repository: no CI workflow runs this suite, and nothing
 *    described here has been observed against a live device or emulator.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The vendor identifier read after the malformed request equals the one read before it.
 *
 * @pass_criteria
 *  - Both reads parse, both carry a result member, their vendorid values are equal, and
 *    run_test() returns True.
 *
 * @failure_criteria
 *  - The final read is empty or is the no-response sentinel, either read lacks a result
 *    member, the values differ, a parse error occurs, or run_test() returns False.
 */
"""

import time
import os
import json
from utils import send_curl_command, log_success, log_error, log_warning
import HdmiCECSink_Curl as HdmiCecSinkApis


def run_test():
    start_time = time.perf_counter()

    # Legacy intent: invalid curl param handling for setVendorId.
    # Restoration, deliberately FIRST: the baseline write re-establishes the value TCID11 wrote
    # at position 11, so no return below can leave a foreign identifier behind.
    # baseline_set and invalid_set are bound but never read - pyflakes flags both, and the
    # finding is accepted so that all four dispatched requests stay visible as named steps.
    baseline_set = send_curl_command(HdmiCecSinkApis.set_vendor_id)
    baseline_get = send_curl_command(HdmiCecSinkApis.get_vendor_id)
    invalid_set = send_curl_command(HdmiCecSinkApis.set_vendor_id_invalid)
    final_get = send_curl_command(HdmiCecSinkApis.get_vendor_id)

    if not final_get:
        log_error("✖ getVendorId command not sent")
        return False
    # The sentinel is a non-empty string, so the falsy check above cannot catch it.
    if final_get.startswith("< No response"):
        log_error("✖ getVendorId returned no response from WPEFramework")
        return False

    log_warning(f"Baseline vendor response: {baseline_get}")
    log_warning(f"Final vendor response: {final_get}")
    try:
        b = json.loads(baseline_get)
        f = json.loads(final_get)
        # "result" must be in BOTH bodies before comparing: two absent members would each
        # resolve to None and compare equal, passing the case on no evidence at all.
        if (
            "result" in b
            and "result" in f
            and b["result"].get("vendorid") == f["result"].get("vendorid")
        ):
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID28_Invalid_VendorID_Nochange Passed"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
    except Exception:
        # Broader than JSONDecodeError on purpose: a non-object result raises on .get(). Every
        # such shape failure leaves the invariant unconfirmed - the same verdict as a mismatch.
        pass

    log_error("TCID28_Invalid_VendorID_Nochange Failed")
    return False
