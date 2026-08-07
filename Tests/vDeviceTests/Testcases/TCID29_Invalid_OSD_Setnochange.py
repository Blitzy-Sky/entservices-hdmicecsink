"""
/**
 * @file TCID29_Invalid_OSD_Setnochange.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID29_Invalid_OSD_Setnochange
 * @details Validates that a MALFORMED org.rdk.HdmiCecSink.setOSDName request cannot change the
 *          stored OSD name. A known baseline is written and read back, then the invalid request
 *          is issued - HdmiCECSink_Curl.set_osd_name_invalid carries valid JSON whose parameter
 *          key is misspelled, so it arrives with no recognised "name" parameter - and a second
 *          getOSDName must report the same name as the baseline read.
 *
 *          The invariant is the UNCHANGED READ, so the write replies are logged but not
 *          asserted: a target may reject a malformed request or accept and ignore it, and either
 *          is acceptable provided the stored name did not move. TCID10_Set_OSD_Name is the
 *          positive leg of this API and runs earlier in the pinned order; this is the negative.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable over the JSON-RPC endpoint utils.py
 *    resolved, and Init_Devicelist_Populate has seeded the emulated CEC topology - which
 *    SuitManager.py guarantees by running it once before the first test case.
 *  - This suite is AUTHORED, NOT EXECUTED here: no CI workflow runs it and nothing below has
 *    been observed against a live device or emulator.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The malformed request leaves the OSD name untouched, so both getOSDName replies report an
 *    identical result.name value.
 *
 * @pass_criteria
 *  - Both getOSDName responses parse as JSON, both carry a "result" member, their two
 *    result.name values are equal, and run_test() returns True.
 *
 * @failure_criteria
 *  - The final read is not dispatched, the response is the no-response sentinel, either body
 *    fails to parse or carries no "result" member, the names differ, or run_test() returns False.
 */
"""

import time
import os
import json
from utils import send_curl_command, log_success, log_error, log_warning
import HdmiCECSink_Curl as HdmiCecSinkApis


def run_test():
    start_time = time.perf_counter()

    # Legacy intent: invalid curl param handling for setOSDName.
    #
    # The baseline write doubles as this module's STATE RESTORATION: set_osd_name re-establishes
    # the same fixed name TCID10_Set_OSD_Name left behind, so this case ends in the deterministic
    # state it found. baseline_set and invalid_set are bound but never asserted - only the
    # unchanged read proves the invariant - yet the bindings keep all four steps visible.
    baseline_set = send_curl_command(HdmiCecSinkApis.set_osd_name)
    baseline_get = send_curl_command(HdmiCecSinkApis.get_osd_name)
    invalid_set = send_curl_command(HdmiCecSinkApis.set_osd_name_invalid)
    final_get = send_curl_command(HdmiCecSinkApis.get_osd_name)

    if not final_get:
        log_error("✖ getOSDName command not sent")
        return False
    # The sentinel is a non-empty string, so the falsy check above cannot catch it.
    if final_get.startswith("< No response"):
        log_error("✖ getOSDName returned no response from WPEFramework")
        return False

    log_warning(f"Baseline OSD name response: {baseline_get}")
    log_warning(f"Final OSD name response: {final_get}")
    try:
        b = json.loads(baseline_get)
        f = json.loads(final_get)
        # "result" must be in BOTH envelopes before the names are compared: two absent values
        # would otherwise compare equal and turn a failed read into a vacuous pass.
        if "result" in b and "result" in f and b["result"].get("name") == f["result"].get("name"):
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID29_Invalid_OSD_Setnochange Passed"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
    except Exception:
        # A parse or shape failure means the invariant could not be confirmed - a failure.
        pass

    log_error("TCID29_Invalid_OSD_Setnochange Failed")
    return False
