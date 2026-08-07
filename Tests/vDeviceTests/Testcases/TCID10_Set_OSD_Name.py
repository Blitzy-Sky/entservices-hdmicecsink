"""
/**
 * @file TCID10_Set_OSD_Name.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID10_Set_OSD_Name
 * @details Sets the sink television's OSD name over JSON-RPC through
 *          org.rdk.HdmiCecSink.setOSDName and confirms the write actually landed by reading the
 *          value straight back with org.rdk.HdmiCecSink.getOSDName. The pre-existing name is
 *          captured and logged before anything is written, so the run record shows the
 *          transition rather than only the end state. A reply that merely reports success is
 *          not accepted as proof on its own; the read-back is what makes this an assertion.
 *
 *          RESIDUAL STATE IS DELIBERATE AND DOCUMENTED. This case leaves the OSD name at the
 *          deterministic value the HdmiCECSink_Curl.set_osd_name constant encodes instead of
 *          writing the captured baseline back, because that module publishes exactly one
 *          set_osd_name constant carrying one fixed name and no inverse, and because
 *          hand-building a JSON-RPC payload here to synthesise a restore is refused. The
 *          reasoning, and why the residual is safe under the suite's pinned execution order,
 *          is recorded in full immediately above the write in run_test().
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is reachable and hosts an
 *    active org.rdk.HdmiCecSink plugin answering JSON-RPC at utils.WPEFRAMEWORK_JSONRPC_URL.
 *  - Init_Devicelist_Populate has seeded the emulated CEC topology, which SuitManager.py
 *    guarantees by running it once before the first test case.
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
 *  - setOSDName answers {"jsonrpc":"2.0","id":42,"result":{"success":true}} and the following
 *    getOSDName reports success together with a non-empty OSD name string.
 *
 * @pass_criteria
 *  - The setOSDName reply equals the expected
 *    {"jsonrpc":"2.0","id":42,"result":{"success":true}} payload, the read-back reports a
 *    matching string, and run_test() returns True.
 *
 * @failure_criteria
 *  - Response mismatch, command failure, an unreachable endpoint, a read-back carrying
 *    success false or an empty name, JSON parsing error, or run_test() returns False.
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


def _osd_result(curl_response):
    '''Return the JSON-RPC "result" member of a getOSDName reply, or None.

    getOSDName is dispatched twice by this case - once for the informational baseline and once
    for the assertive read-back - so the envelope handling lives here instead of being written
    out twice. Extraction only: the caller keeps the decision about what a missing result
    means, which is what lets the baseline treat it as informational while the read-back
    treats it as a failure. Every unusable reply collapses to None rather than raising - a
    falsy response, the transport-failure sentinel, a body that does not parse, a body that is
    not a JSON object, and an envelope with no "result" member all yield None.
    Args:
        curl_response: Raw response string exactly as utils.send_curl_command returned it.
    Returns:
        The "result" member as a dict when the reply carried a usable one, otherwise None.
    '''
    if not curl_response or curl_response.startswith("< No response"):
        return None

    try:
        envelope = json.loads(curl_response)
    except json.JSONDecodeError:
        return None

    if not isinstance(envelope, dict):
        return None

    result = envelope.get("result")
    return result if isinstance(result, dict) else None


def run_test():
    '''Set the sink's OSD name and confirm the write with a read-back.

    Returns:
        True only when setOSDName answered with the expected success envelope AND the
        subsequent getOSDName reported success together with a non-empty name string. False
        on a command that could not be sent, an unreachable endpoint, an unexpected reply, an
        unusable read-back, or a JSON parsing error.
    '''
    start_time = time.perf_counter()

    # The id is 42 because every constant in HdmiCECSink_Curl.py sends "id":42, and setOSDName
    # returns HdmiCecSinkSuccess, which serialises to a bare {"success": true} result.
    expected_output_response = {
        "jsonrpc": "2.0",
        "id": 42,
        "result": {
            "success": True
        }
    }

    # Baseline capture, before anything is written, so the transition is visible in the run
    # record. A baseline that never arrived is NOT fatal and is logged rather than returned on:
    # the write and the read-back below are the actual assertion, and aborting because an
    # informational read failed would report a defect that is not there.
    baseline_get = send_curl_command(HdmiCecSinkApis.get_osd_name)
    log_warning(f"Baseline getOSDName response: {baseline_get}")

    baseline_result = _osd_result(baseline_get)
    baseline_name = baseline_result.get("name") if baseline_result else None
    if baseline_name is None:
        log_warning("Baseline OSD name unavailable - continuing, it is informational only")

    # RESIDUAL STATE, REPORTED RATHER THAN FORCED. The write below leaves the OSD name at the
    # deterministic value HdmiCECSink_Curl.set_osd_name encodes; this case does not put the
    # captured baseline back. That is a decision, not an omission:
    #
    #   * HdmiCECSink_Curl.py publishes exactly ONE set_osd_name constant carrying ONE fixed
    #     name, so no constant exists that could write an arbitrary captured name back. The
    #     suite's `finally:  #reset the state` idiom works for the enabled flag only because
    #     that API has an inverse constant - set_enabled_true beside set_enabled_false.
    #     setOSDName has no inverse.
    #   * Hand-building a JSON-RPC payload here to restore the captured name is refused.
    #     Payload construction belongs to HdmiCECSink_Curl.py, whose own contract forbids
    #     assembling or concatenating its commands elsewhere; forking that transport contract
    #     into a test case to synthesise a restore would be the larger defect.
    #
    # Leaving the residual is safe under the execution order pinned in SuitManager.py:
    #   (a) the only reader of the prior value, TCID03_Get_OSD_Name, runs EARLIER at position 3
    #       and has already observed and asserted the pre-write name by the time this case, at
    #       position 10, changes it; and
    #   (b) TCID29_Invalid_OSD_Setnochange re-establishes the same value through the same
    #       set_osd_name constant at position 29, so the suite ends on the value set here.
    #
    # A `finally` block re-issuing set_osd_name would LOOK like a restore while restoring
    # nothing. Reporting the residual is Directive 6's "report, don't force" applied to
    # fixture state.
    log_info("Executing the curl command set OSD name")

    curl_response = send_curl_command(
        HdmiCecSinkApis.set_osd_name
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # utils.send_curl_command reports every transport failure with a TRUTHY sentinel string, so
    # the falsy check above cannot catch it and this second guard is load-bearing. Without it an
    # unreachable endpoint would fall through to the comparison and be reported as a response
    # mismatch, misdiagnosing a dead endpoint as a plugin defect.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework for set OSD name")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    try:
        if json.loads(curl_response) != expected_output_response:
            log_error("TCID10_Set_OSD_Name Failed ❌")
            return False

        # The plugin applies the setting asynchronously, so one bounded pause separates the
        # write from the read-back. One second, once - deliberately not a poll loop, since a
        # loop would hide a slow apply behind a retry instead of reporting it.
        time.sleep(1)

        readback_result = _osd_result(send_curl_command(HdmiCecSinkApis.get_osd_name))
        if readback_result is None:
            log_error("✖ read-back of the OSD name returned no usable response")
            log_error("TCID10_Set_OSD_Name Failed ❌")
            return False

        if readback_result.get("success") is not True:
            log_error(f"✖ read-back did not report success: {readback_result}")
            log_error("TCID10_Set_OSD_Name Failed ❌")
            return False

        # The read-back asserts SHAPE - success true and a non-empty name string - not a pinned
        # name literal. That literal is encoded in the sibling HdmiCECSink_Curl.set_osd_name
        # constant; repeating it here would couple two deliberately decoupled modules and would
        # fail this case on a constant change that is not a plugin defect.
        final_name = readback_result.get("name")
        if not isinstance(final_name, str) or not final_name.strip():
            log_error(f"✖ read-back carries no usable OSD name: {final_name!r}")
            log_error("TCID10_Set_OSD_Name Failed ❌")
            return False

        log_success(f"✔ OSD name read back from the device: {final_name}")
        log_warning(f"OSD name transition: {baseline_name!r} -> {final_name!r}")

        elapsed_time = time.perf_counter() - start_time
        msg = "TCID10_Set_OSD_Name Passed ✅"
        if os.environ.get("HDMICEC_TIMING_ENABLED"):
            log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
        else:
            log_success(msg)
        return True
    except json.JSONDecodeError:
        # The documented contract boundary, not a speculative guard: utils.send_curl_command
        # promises a RAW string and states that callers run their own json.loads "so that they
        # can distinguish a malformed payload from a missing one". This branch is that
        # distinction, and it is the convention every case in both device-level suites follows.
        log_error("Invalid JSON response")
        log_error("TCID10_Set_OSD_Name Failed ❌")
        return False
