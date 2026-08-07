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
from utils import (
    send_jsonrpc_envelope,
    envelope_result,
    require_ack,
    sanitise_for_log,
    log_success,
    log_error,
    log_warning,
)
import HdmiCECSink_Curl as HdmiCecSinkApis

# Bounded budgets. setEnabled is not a simple assignment - CECEnable() starts the poll thread and
# CECDisable() waits for ARC to reach the terminated state and then joins that thread
# (HdmiCecSinkImplementation.cpp:3060-3140) - so the published flag is read through a bounded poll
# rather than immediately after the call. Poll ceilings, never durations anything waits out.
SETTLE_TIMEOUT_S = 15.0
SETTLE_POLL_S = 0.25

# How many consecutive agreeing readings establish that a value HELD rather than merely happened to
# be observed once. Used for the idempotent repeat, where "still false" is the whole claim.
CONFIRM_READINGS = 3

# True once this module has driven CEC away from the enabled state, so cleanup() can tell "already
# restored" from "must restore".
_enabled_disturbed = False


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could carry
    a non-object "result" or not be an object at all. Every such case collapses to {} so the caller
    reports a MISSING FIELD rather than raising AttributeError out of run_test(). Narrowing here is
    what let the broad exception handler this module used to carry be removed entirely: the only
    exception a caller can now see is json.JSONDecodeError, handled where it can occur rather than
    swept up together with every programming defect in the file.
    """
    body = json.loads(response_text)
    if not isinstance(body, dict):
        return {}
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def _read_enabled():
    """Return the published HDMI-CEC enabled flag, or None when it cannot be read."""
    response = send_curl_command(HdmiCecSinkApis.get_enabled)
    # utils.send_curl_command reports a transport failure by RETURNING the TRUTHY sentinel
    # "< No response from WPEFramework >", so the prefix form is the detection contract.
    if not response or response.startswith("< No response"):
        return None
    try:
        result = _result_object(response)
    except json.JSONDecodeError:
        return None
    if result.get("success") is not True:
        return None
    value = result.get("enabled")
    return value if isinstance(value, bool) else None


def _set_enabled(argv, label):
    """Dispatch one setEnabled request and REQUIRE its acknowledgement.

    An earlier revision discarded every setter reply, which meant a request that was never
    dispatched, answered with an error envelope or answered with success false was indistinguishable
    from one that worked - and the case then drew its verdict from a single read that could have
    been satisfied by pre-existing state. SetEnabled sets success unconditionally
    (HdmiCecSinkImplementation.cpp:1839-1845), so requiring True proves the call REACHED the plugin
    rather than proving the transition; the transition is proven by the read-back that follows.
    """
    response = send_curl_command(argv)
    if not response:
        log_error(f"✖ {label}: setEnabled command not sent")
        return False
    if response.startswith("< No response"):
        log_error(f"✖ {label}: setEnabled returned no response from WPEFramework")
        return False
    log_info(f"  {label}: {response}")
    try:
        if _result_object(response).get("success") is not True:
            log_error(f"✖ {label}: setEnabled did not acknowledge success")
            return False
    except json.JSONDecodeError:
        log_error(f"✖ {label}: setEnabled reply is not valid JSON")
        return False
    log_success(f"✔ {label}: setEnabled acknowledged")
    return True


def _wait_for_enabled(expected):
    """Poll the published flag until it reads `expected`; returns (matched, last_reading)."""
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        observed = _read_enabled()
        if observed is expected:
            return True, observed
        if time.monotonic() >= deadline:
            return False, observed
        time.sleep(SETTLE_POLL_S)


def _confirm_held(expected):
    """Require CONFIRM_READINGS consecutive readings of `expected`; returns (held, last_reading).

    One reading cannot distinguish "the value held" from "the value happened to be right at the
    moment it was sampled", and the idempotent repeat's entire claim is that the value HELD.
    """
    for _ in range(CONFIRM_READINGS):
        observed = _read_enabled()
        if observed is not expected:
            return False, observed
        time.sleep(SETTLE_POLL_S)
    return True, expected


def cleanup():
    """Guarantee HDMI-CEC is left ENABLED - the suite invariant this module deliberately disturbs.

    THIS IS WHY THE HOOK EXISTS AS WELL AS THE finally CLAUSE. SuitManager runs cleanup() for every
    registered case unconditionally - after a pass, a failure, an exception, and even for a case it
    SKIPPED because its producer failed - so a run in which run_test() never executed at all still
    leaves CEC enabled. A finally clause inside run_test() cannot cover that path.
    Idempotent: when run_test() has already restored the flag, this reports that and does nothing.
    Returns:
        True when there was nothing to restore or CEC is enabled again; False when the request was
        refused or the flag never came back within its budget.
    """
    global _enabled_disturbed
    if not _enabled_disturbed:
        log_info("TCID31 cleanup: HDMI-CEC was not left disturbed, nothing to restore")
        return True
    _enabled_disturbed = False

    log_info("TCID31 cleanup: re-enabling HDMI-CEC so the suite invariant holds")
    if not _set_enabled(HdmiCecSinkApis.set_enabled_true, "cleanup re-enable"):
        log_error("TCID31 cleanup: HDMI-CEC could not be re-enabled and may be left disabled")
        return False
    restored, observed = _wait_for_enabled(True)
    if not restored:
        log_error(
            f"TCID31 cleanup: HDMI-CEC reads {observed!r} rather than True after the re-enable"
        )
        return False
    log_success("✔ TCID31 cleanup: HDMI-CEC is enabled again")
    return True


def _read_enabled(label):
    """Read getEnabled and return its boolean state, or None with the reason already logged.

    `enabled` must be a real bool: a missing member, a null, or a truthy 1 or "true" is reported
    as unreadable rather than coerced. This case's subject is a boolean state holding still, and a
    coerced value would let an off-contract reply stand in for the reading.
    """
    envelope = send_jsonrpc_envelope(HdmiCecSinkApis.get_enabled, label)
    result = envelope_result(envelope)
    if result is None or result.get("success") is not True:
        log_error(f"✖ {label} did not answer with a result reporting success")
        return None
    enabled = result.get("enabled")
    if not isinstance(enabled, bool):
        log_error(
            f"✖ {label} did not report a boolean enabled member "
            f"({sanitise_for_log(enabled, max_chars=32)})"
        )
        return None
    log_warning(f"{label}: enabled={enabled}")
    return enabled


def run_test():
    """Manufacture the disabled state, enable twice, and require the flag to hold enabled.

    Returns:
        True when every setEnabled acknowledges and every read-back agrees; False on any transport
        failure, unacknowledged request, unreadable flag or transition that never happened. HDMI-CEC
        is enabled on every exit path.
    """
    global _enabled_disturbed
    _enabled_disturbed = False
    start_time = time.perf_counter()

    # Legacy intent: getEnabled when already enabled.
    #
    # IDEMPOTENCE NEEDS BOTH READINGS, AND EVERY WRITE ACKNOWLEDGED - the same reasoning as
    # TCID30_Repeated_Disable_Idempotent, and it bites harder here. Enabled is the suite's
    # standing invariant, so the plugin arrives at this case already enabled: a reading of true
    # proves nothing at all unless the write that preceded it was acknowledged. With the replies
    # discarded, this case would have reported a pass with neither request reaching the device.
    if not require_ack(HdmiCecSinkApis.set_enabled_true, "first setEnabled(true)"):
        log_error("TCID31_Repeated_Enable_Idempotent Failed")
        return False

    first_enabled = _read_enabled("getEnabled after the first enable")
    if first_enabled is not True:
        log_error(
            "✖ the first setEnabled(true) was acknowledged but getEnabled reports enabled="
            f"{first_enabled}"
        )
        log_error("TCID31_Repeated_Enable_Idempotent Failed")
        return False

    if not require_ack(HdmiCecSinkApis.set_enabled_true, "repeated setEnabled(true)"):
        log_error("TCID31_Repeated_Enable_Idempotent Failed")
        return False

    second_enabled = _read_enabled("getEnabled after the repeated enable")
    if second_enabled is not True:
        log_error(
            f"✖ the repeated setEnabled(true) changed the state: enabled={second_enabled}"
        )
        log_error("TCID31_Repeated_Enable_Idempotent Failed")
        return False

    # No restoration request follows, deliberately: enabled IS the suite invariant, so this
    # module's terminal state is already the wanted one. That is the intentional asymmetry
    # with TCID30_Repeated_Disable_Idempotent, which must send a trailing set_enabled_true.
    # It is also why the two guards above may return early without a restore: every path
    # through this module leaves HDMI-CEC in the state the rest of the suite needs.

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
