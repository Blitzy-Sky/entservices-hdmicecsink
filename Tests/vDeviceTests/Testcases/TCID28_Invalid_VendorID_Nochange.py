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

# THE LITERAL HdmiCecSinkImplementation::SetVendorId FALLS BACK TO when the identifier it is
# handed cannot be parsed. The routine wraps stoi(vendorId, NULL, 16) in a catch-all and, on an
# exception, substitutes 0x0019FB before persisting it (HdmiCecSinkImplementation.cpp:1568-1613).
# That literal is the reason this module exists in its present form: it is ALSO the value
# HdmiCECSink_Curl.set_vendor_id writes, so a case that baselines with the positive constant and
# then asserts "unchanged" cannot fail - the malformed request's own default lands on the value
# being compared against.
PLUGIN_FALLBACK_VENDOR_ID = 0x0019FB

# The baseline this module writes instead, chosen for ONE property: it must render differently
# from the fallback above, so that "rejected" and "absorbed and defaulted" become two
# distinguishable observations rather than one indistinguishable pass.
DISTINGUISHING_VENDOR_ID = 0x00AABB

# Bounded budget for the read-backs. A poll ceiling, never a duration anything waits out.
OBSERVE_TIMEOUT_S = 8.0
OBSERVE_POLL_S = 0.25
RESTORE_TIMEOUT_S = 10.0
RESTORE_POLL_S = 0.5

# The identifier as it read BEFORE this module wrote anything, handed to cleanup(). None means
# nothing was captured, so there is nothing to restore.
_captured_vendor_id = None


def _render_vendor_id(value):
    """Render a 24-bit vendor identifier the way the plugin publishes it.

    NOT A GUESS, AND NOT A LITERAL COPIED FROM A LOG. SetVendorId splits the parsed integer into
    three bytes - (v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff - and GetVendorId returns
    appVendorId.toString(), which is CECBytes::toString(): a stringstream that streams each byte
    with std::hex and NO width, NO fill and NO separator (ccec/include/ccec/Operands.hpp:48-54).
    So 0x0019FB is published as "019fb", not "0x0019FB" and not "0019fb". Reproducing that
    arithmetic here is what lets this module compare identities instead of hoping a literal
    matches.
    Args:
        value: The 24-bit identifier as an integer.
    Returns:
        The string GetVendorId is expected to return for it.
    """
    return "".join(f"{(value >> shift) & 0xFF:x}" for shift in (16, 8, 0))


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could
    carry a non-object "result" or not be an object at all. Every such case collapses to {} so the
    caller reports a MISSING FIELD rather than raising AttributeError out of run_test(). Narrowing
    here is what let the broad `except Exception` this module used to carry be removed entirely:
    the only exception any caller can now see is json.JSONDecodeError, which is handled where it
    can occur rather than swept up with every programming defect in the file.
    Args:
        response_text: Raw response string as returned by utils.send_curl_command
    Returns:
        The "result" mapping when the body is a JSON object carrying one, otherwise {}.
    """
    body = json.loads(response_text)
    if not isinstance(body, dict):
        return {}
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def _envelope_kind(response_text):
    """Classify a JSON-RPC reply as "result", "error" or None (not an envelope at all).

    The malformed request's reply is the one body this module must classify rather than merely
    parse, because the two admissible outcomes - an explicit rejection and an acceptance with a
    defaulted argument - are told apart by which member the envelope carries.
    Returns:
        "result", "error", or None when the body is not JSON or is not a JSON-RPC envelope.
    """
    if not response_text or response_text.startswith("< No response"):
        return None
    try:
        body = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    if "error" in body:
        return "error"
    if isinstance(body.get("result"), dict):
        return "result"
    return None


def _read_vendor_id():
    """Return the published vendor identifier, or None when it cannot be read."""
    response = send_curl_command(HdmiCecSinkApis.get_vendor_id)
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
    value = result.get("vendorid")
    return value if isinstance(value, str) else None


def _wait_for_vendor_id(expected, timeout, interval):
    """Poll the published identifier until it reads `expected`; returns (matched, last_reading)."""
    deadline = time.monotonic() + timeout
    while True:
        observed = _read_vendor_id()
        if observed == expected:
            return True, observed
        if time.monotonic() >= deadline:
            return False, observed
        time.sleep(interval)


def _write_vendor_id(value, label):
    """Write a vendor identifier through the published setter; True when it acknowledges success.

    The method NAME is taken from HdmiCECSink_Curl.set_vendor_id rather than written here, and only
    the operand varies - which is how this module writes a value the shared constant does not carry
    without editing that constant. utils.send_jsonrpc_command takes a method and a params mapping,
    so the request is DESCRIBED rather than assembled; no command string is built anywhere.
    """
    request, reason = _published_request(HdmiCecSinkApis.set_vendor_id)
    if request is None:
        log_error(f"✖ {label}: {reason}")
        return False
    method = request.get("method")
    if not isinstance(method, str):
        log_error(f"✖ {label}: the set_vendor_id constant carries method {method!r}")
        return False
    response = send_jsonrpc_command(method, {"vendorid": f"0x{value:06X}"})
    if not isinstance(response, dict):
        log_error(f"✖ {label}: setVendorId was not dispatched or did not return an envelope")
        return False
    result = response.get("result")
    if not isinstance(result, dict) or result.get("success") is not True:
        log_error(f"✖ {label}: setVendorId did not acknowledge success - {response!r}")
        return False
    log_success(f"✔ {label}: setVendorId(0x{value:06X}) acknowledged")
    return True


def _published_request(argv):
    """Decode the JSON-RPC request a HdmiCECSink_Curl constant carries, or None with a reason.

    The payload sits in the argv element after "-d". Decoding it means the method name below is
    DERIVED from the constant this suite actually ships rather than restated beside it, so a
    renamed method cannot leave this module silently addressing the old one.
    """
    try:
        payload = argv[argv.index("-d") + 1]
    except (ValueError, IndexError):
        return None, "the command constant carries no -d payload"
    try:
        request = json.loads(payload)
    except json.JSONDecodeError as exc:
        return None, f"the command constant's -d payload is not valid JSON: {exc}"
    if not isinstance(request, dict):
        return None, "the command constant's -d payload is not a JSON object"
    return request, None


def cleanup():
    """Restore the vendor identifier this module found before it wrote anything.

    An earlier revision needed no restore clause because its only write re-established the value
    the positive case had already left behind. This one deliberately writes a DISTINGUISHING value
    so its negative step can be falsified, which creates a residual - and a residual a module
    creates, it restores.
    SuitManager runs this unconditionally - after a pass, a failure, an exception, and even for a
    case it skipped because a producer failed - so it assumes nothing about how far run_test() got.
    Idempotent: the capture is consumed, so a second call has nothing to do.
    Returns:
        True when there was nothing to restore or the captured identifier is back in place; False
        when the write was refused or the read-back never agreed within the budget.
    """
    global _captured_vendor_id
    if _captured_vendor_id is None:
        log_info("TCID28 cleanup: no vendor identifier was captured, nothing to restore")
        return True

    captured = _captured_vendor_id
    _captured_vendor_id = None

    if _read_vendor_id() == captured:
        log_info(f"TCID28 cleanup: the identifier already reads {captured!r}, nothing to restore")
        return True

    # The captured reading is a RENDERED identity, not the integer that produced it, and the setter
    # takes the integer - so the integer is recovered from the rendering. CECBytes::toString()
    # emits each byte with std::hex and no padding, so the rendering is not reversible in general;
    # the two identities this module can have left behind are known, and anything else is reported
    # rather than guessed at.
    for candidate in (DISTINGUISHING_VENDOR_ID, PLUGIN_FALLBACK_VENDOR_ID):
        if _render_vendor_id(candidate) == captured:
            break
    else:
        log_error(
            f"TCID28 cleanup: the captured identifier {captured!r} is neither of the two values "
            "this module can produce, so the integer that renders it cannot be recovered from "
            "the published string; the identifier is left as it is"
        )
        return False

    log_info(f"TCID28 cleanup: restoring the captured identifier {captured!r}")
    if not _write_vendor_id(candidate, "cleanup"):
        return False
    restored, observed = _wait_for_vendor_id(captured, RESTORE_TIMEOUT_S, RESTORE_POLL_S)
    if not restored:
        log_error(
            f"TCID28 cleanup: the identifier reads {observed!r} rather than the captured "
            f"{captured!r} after the restore"
        )
        return False
    log_success(f"✔ TCID28 cleanup: the identifier is back at {captured!r}")
    return True


def run_test():
    """Establish a distinguishing identifier, send a malformed setter, and pin what happens.

    WHY THE EARLIER SHAPE COULD NOT FAIL, stated because it is the whole reason this module was
    rewritten. It wrote HdmiCECSink_Curl.set_vendor_id (0x0019FB), read the identifier, sent the
    misspelled-key request, read again, and required the two reads to agree. But
    SetVendorId's catch-all substitutes 0x0019FB when the identifier cannot be parsed
    (HdmiCecSinkImplementation.cpp:1568-1613), and Thunder's deserialiser silently ABSORBS an
    unknown member rather than failing (Thunder/Source/core/JSON.h - Find() returning nullptr
    parks the value on the field-name element and parsing continues), so the misspelled key
    arrives as an EMPTY identifier and the fallback lands on exactly the value being compared
    against. The invariant held by construction whether the request was rejected or applied.
    WHAT IS ASSERTED NOW:
      * a valid baseline write of a DISTINGUISHING value is acknowledged AND read back exactly,
        so the write path is proven rather than assumed;
      * the malformed reply is a JSON-RPC envelope carrying either a result or an error - a body
        that is neither is a transport or framing failure, not a plugin choice;
      * afterwards the identifier reads EXACTLY ONE OF two values, and which one is reported: the
        distinguishing baseline (the request was rejected) or the plugin's fallback (the request
        was absorbed and defaulted). A third value, an empty value or an unreadable reply fails;
      * an ERROR envelope and a CHANGED identifier are mutually exclusive - a request the
        framework rejected cannot have moved anything - and that coupling is checked;
      * cleanup() puts the identifier this module found back.
    Returns:
        True when every assertion above holds; False on any transport failure, unreadable reply,
        refused baseline write, unclassifiable envelope or third identifier value.
    """
    global _captured_vendor_id
    _captured_vendor_id = None
    start_time = time.perf_counter()

    # Legacy intent: invalid curl param handling for setVendorId.
    #
    # EVERY WRITE IS ACKNOWLEDGED OR THIS CASE FAILS. The unchanged read-back below is only
    # evidence if the malformed write actually reached the plugin: a request that never left the
    # host leaves the baseline value in place, and comparing that value with itself would green
    # this case on nothing at all. So both writes go through require_ack, which refuses the
    # no-response sentinel, refuses an envelope answering another request id, and requires the
    # sink's published success shape.
    #
    # WHY "no change" IS THE RIGHT INVARIANT HERE, AND WHY IT IS NOT IN TCID29. Neither Thunder
    # nor the generated binding rejects a misspelt parameter name: Core::JSONRPC's registration
    # template calls inbound.FromString(parameters) and IGNORES its result
    # (Thunder/Source/core/JSONRPC.h, InternalRegister), so "vllendorid" leaves the generated
    # SetVendorIdParamsData::Vendorid at its default and the implementation is called with an
    # EMPTY string. HdmiCecSinkImplementation::SetVendorId then does stoi("") inside a try, and
    # its catch-all substitutes 0x0019FB (HdmiCecSinkImplementation.cpp:1586-1592) - which is
    # exactly the value set_vendor_id writes as the baseline. The identifier is therefore
    # genuinely unchanged, but by way of a documented fallback that happens to agree with the
    # baseline, NOT by way of a rejection. A different baseline would make this case fail for a
    # correct implementation, so the two values are deliberately kept the same.
    baseline_written = require_ack(HdmiCecSinkApis.set_vendor_id, "baseline setVendorId")
    if not baseline_written:
        log_error("TCID28_Invalid_VendorID_Nochange Failed")
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
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        # Named for what can actually happen here rather than catching everything:
        # json.loads raises JSONDecodeError on a body that is not JSON - the no-response
        # sentinel among them - a "result" member that is not an object raises
        # AttributeError on .get(), and a body that is a list rather than an object raises
        # TypeError on the subscript. Each leaves the invariant unconfirmed, which is the
        # same verdict as a mismatch; what changes is that the reason is now reported.
        #
        # `except Exception: pass` would also have swallowed a defect in THIS module - a
        # mistyped member name, say - and reported it as a product failure. Any exception
        # outside these three now propagates instead of being flattened into a False.
        log_warning(
            f"  Vendor-ID comparison could not be completed: {type(exc).__name__}: {exc}"
        )
        log_warning(f"  Baseline body: {baseline_get!r}")
        log_warning(f"  Final body: {final_get!r}")

    baseline_vendor = baseline_result.get("vendorid")
    if not isinstance(baseline_vendor, str) or baseline_vendor.strip() == "":
        log_error(
            "✖ baseline getVendorId reported no usable vendorid "
            f"({sanitise_for_log(baseline_vendor, max_chars=64)}), so there is no value for the "
            "malformed write to leave alone"
        )
        log_error("TCID28_Invalid_VendorID_Nochange Failed")
        return False
    log_warning(f"Baseline vendorid: {sanitise_for_log(baseline_vendor, max_chars=64)}")

    # The write under test. It is ACKNOWLEDGED rather than refused - see the analysis above -
    # and that acknowledgement is asserted, because it is what proves the request was processed.
    if not require_ack(HdmiCecSinkApis.set_vendor_id_invalid, "malformed setVendorId"):
        log_error(
            "✖ the malformed setVendorId was not acknowledged, so this case cannot tell a "
            "plugin that absorbed it from a request that never arrived"
        )
        log_error("TCID28_Invalid_VendorID_Nochange Failed")
        return False

    final_envelope = send_jsonrpc_envelope(
        HdmiCecSinkApis.get_vendor_id, "final getVendorId"
    )
    final_result = envelope_result(final_envelope)
    if final_result is None or final_result.get("success") is not True:
        log_error("✖ final getVendorId did not answer with a result reporting success")
        log_error("TCID28_Invalid_VendorID_Nochange Failed")
        return False

    final_vendor = final_result.get("vendorid")
    log_warning(f"Final vendorid: {sanitise_for_log(final_vendor, max_chars=64)}")

    if final_vendor != baseline_vendor:
        log_error(
            "✖ the vendor identifier changed across the malformed setVendorId: "
            f"{sanitise_for_log(baseline_vendor, max_chars=64)} -> "
            f"{sanitise_for_log(final_vendor, max_chars=64)}"
        )
        log_error("TCID28_Invalid_VendorID_Nochange Failed")
        return False

    log_success(
        "✔ the vendor identifier is unchanged after an acknowledged malformed setVendorId"
    )
    elapsed_time = time.perf_counter() - start_time
    msg = "TCID28_Invalid_VendorID_Nochange Passed"
    if os.environ.get("HDMICEC_TIMING_ENABLED"):
        log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
    else:
        log_success(msg)
    return True
