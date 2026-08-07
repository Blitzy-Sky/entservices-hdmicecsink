"""
/**
 * @file TCID32_Invalid_ARC_Routing_Nochange.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID32_Invalid_ARC_Routing_Nochange
 * @details Validates that a malformed org.rdk.HdmiCecSink.setupARCRouting request leaves the
 *          sink's observable routing state unchanged: the active route is read, the malformed
 *          request is dispatched, then the route is read again. Both reads are logged and
 *          compared; the setupARCRouting reply is captured but NOT asserted, because a plugin may
 *          refuse an unrecognised parameter with a JSON-RPC error or with a bare acknowledgement
 *          that carries a defaulted argument, and pinning that choice would test the reply rather
 *          than the invariant. The misspelled key lives in
 *          HdmiCECSink_Curl.setup_arc_routing_invalid - `{"ennabled": true}` instead of
 *          `{"enabled": true}` - and only there, so this module pins no ARC state and compares
 *          two observations.
 *
 *          WHY THE ACTIVE ROUTE IS THE OBSERVABLE, AND WHAT IS NOT CLAIMED. ARC routing state
 *          itself has NO GETTER on the interface: SetupARCRouting
 *          (HdmiCecSinkImplementation.cpp:1600) publishes only a success flag, and the state it
 *          moves - m_currentArcRoutingState - is reported outward solely through the
 *          arcInitiationEvent / arcTerminationEvent notifications (ArcTerminationEvent,
 *          IHdmiCecSink.h:76), which are Thunder notifications delivered to registered
 *          COM-RPC/JSON-RPC subscribers rather than to a one-shot curl request/response. This
 *          module therefore does not, and cannot, assert "ARC did not change" directly. What it
 *          asserts is the strongest invariant this transport can actually witness: a malformed
 *          setupARCRouting perturbs no readable routing state. getActiveRoute is the read chosen
 *          for it because routing is what the case name is about; getAudioDeviceConnectedStatus
 *          is read alongside as a second, independent invariant and is compared on the same
 *          terms. Stating this boundary is the point - a case that claimed to observe the ARC
 *          handshake from here would be claiming something the transport does not support.
 *
 *          THE COMPARISON IS GUARDED AGAINST PASSING ON NO EVIDENCE. Two absent members would
 *          each resolve to None and compare equal, which would green this case without observing
 *          anything. Both bodies are therefore required to carry a "result" mapping AND to report
 *          success True with a boolean `available` before any value comparison is believed, which
 *          is the same shape TCID07_Get_Active_Route requires of a well-formed reply. Only then
 *          are the route fields compared, and `length` / `pathList` are compared as
 *          present-or-absent on both sides because the television being its own active source
 *          yields available true with ActiveRoute "TV" and neither field - a legitimate reply
 *          shape TCID07 documents.
 *
 *          THIS CASE IS SELF-RESTORING BY CONSTRUCTION. It dispatches exactly one write, and that
 *          write is malformed and expected to be rejected; every other request is a read. No
 *          valid enable or disable is sent, so nothing here can re-arm the ARC state that
 *          TCID21_ARC_Termination_Flow took down at position 21, and no restore clause is needed
 *          on any path. That matters for suite ordering: SuitManager.py runs this case at
 *          position 32, after the idempotency pair 30/31 and immediately before
 *          TCID33_Process_Yaml_Health_Check, and leaving ARC enabled here would silently change
 *          the state those neighbours were written against.
 *
 *          GAP CLOSED. COVERAGE_GAPS.md ranks the missing sink vDeviceTests suite 22nd at
 *          priority P1 (#gap-plugin-sink-vdevicetests). In the §4b API table `SetupARCRouting` is
 *          recorded as covered by the sink's own L2 suite (SetupARCRouting_COMRPC and
 *          SetupARCRouting_JSONRPC in ../../L2Tests/tests/HdmiCecSink_L2Test.cpp) with NO E2E
 *          leg, and both of those L2 cases exercise the WELL-FORMED argument only. TCID20 and
 *          TCID21 supply the positive end-to-end initiation and termination halves; this module
 *          supplies the NEGATIVE leg that neither the L2 suite nor those two cases cover - the
 *          malformed-parameter path - which is the negative/corner-case class Directive 2 asks
 *          for on every API it names.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable over the JSON-RPC endpoint.
 *  - Init_Devicelist_Populate has run, so the CEC topology is seeded and HDMI-CEC is enabled.
 *  - TCID21_ARC_Termination_Flow is expected to have left ARC disabled; this case neither
 *    depends on that value nor changes it, because it compares two observations instead of
 *    pinning one.
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
 *  - The active route read after the malformed request equals the one read before it, and the
 *    audio-device connected status is likewise unchanged.
 *  - The setupARCRouting reply to the malformed request is logged and not asserted: see
 *    @details.
 *  - No ARC routing state change is claimed, because none is observable from this transport.
 *
 * @pass_criteria
 *  - Both route reads parse and carry a result member reporting success True with a boolean
 *    available, their available / ActiveRoute / length / pathList values are equal, the two
 *    connected-status reads agree, and run_test() returns True.
 *
 * @failure_criteria
 *  - A request is not dispatched, a response is the no-response sentinel, either read lacks a
 *    result member or reports success other than True or a non-boolean available, any compared
 *    route field differs, the connected-status values differ, a parse error occurs, or
 *    run_test() returns False.
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
    log_warning
)
import HdmiCECSink_Curl as HdmiCecSinkApis


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could
    carry a non-object "result" or not be an object at all. Every such case collapses to {} so
    the caller reports a MISSING FIELD rather than raising AttributeError out of run_test(). A
    body that is not JSON at all still raises json.JSONDecodeError, which run_test() handles as
    the documented failure. Four call sites share this, which is why it is factored out.
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


def _route_fields(result):
    """Reduce a getActiveRoute result mapping to the tuple this case compares.

    `length` and `pathList` are read with .get() and so collapse to None when absent, which is a
    legitimate reply shape rather than an error: the television being its own active source
    yields available true with ActiveRoute "TV" and neither field, as TCID07_Get_Active_Route
    documents. Comparing them as present-or-absent on BOTH sides is therefore the correct
    treatment - it detects a field appearing, disappearing or changing, without demanding a field
    the plugin is not obliged to send. The well-formedness of the reply is established separately
    by the caller before this tuple is believed.
    Args:
        result: The "result" mapping from a getActiveRoute reply
    Returns:
        A tuple of the four route-describing fields, in a fixed order.
    """
    return (
        result.get("available"),
        result.get("ActiveRoute"),
        result.get("length"),
        result.get("pathList"),
    )


def run_test():
    start_time = time.perf_counter()

    # Legacy intent: invalid curl param handling for setupARCRouting.
    #
    # The four requests below are dispatched as named steps so a reader can see the shape of the
    # experiment: read, read, malformed write, read, read. `invalid_setup` is bound but never
    # asserted - pyflakes flags it, and the finding is accepted so the dispatched request stays
    # visible - because the reply to a malformed parameter is exactly what this case declines to
    # pin. Its body is logged instead, which is the evidence a reader needs without turning a
    # plugin's error-reporting choice into a pass criterion.
    baseline_route = send_curl_command(HdmiCecSinkApis.get_active_route)
    baseline_audio = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    invalid_setup = send_curl_command(HdmiCecSinkApis.setup_arc_routing_invalid)
    time.sleep(1)
    final_route = send_curl_command(HdmiCecSinkApis.get_active_route)
    final_audio = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)

    # Transport guards. utils.send_curl_command returns the
    # "< No response from WPEFramework >" sentinel - a TRUTHY string - for every failure mode, so
    # the falsy check alone cannot catch one; the prefix form is the detection contract utils.py
    # documents for callers. Every response this case actually compares is guarded, the malformed
    # write excepted, since nothing is concluded from it.
    for label, response in (
        ("baseline getActiveRoute", baseline_route),
        ("baseline getAudioDeviceConnectedStatus", baseline_audio),
        ("final getActiveRoute", final_route),
        ("final getAudioDeviceConnectedStatus", final_audio),
    ):
        if not response:
            log_error(f"✖ {label} command not sent")
            return False
        if response.startswith("< No response"):
            log_error(f"✖ {label} returned no response from WPEFramework")
            return False

    log_warning(f"Baseline route response: {baseline_route}")
    log_info(f"Malformed setupARCRouting reply (logged, not asserted): {invalid_setup}")
    log_warning(f"Final route response: {final_route}")

    try:
        before_route = _result_object(baseline_route)
        after_route = _result_object(final_route)
        before_audio = _result_object(baseline_audio)
        after_audio = _result_object(final_audio)

        # WELL-FORMEDNESS FIRST, VALUES SECOND. Requiring success True and a boolean `available`
        # on BOTH reads is what stops two empty or two error bodies from comparing equal and
        # greening this case on no observation at all. This is the same reply shape
        # TCID07_Get_Active_Route requires of getActiveRoute.
        for label, result in (("baseline", before_route), ("final", after_route)):
            if result.get("success") is not True:
                log_error(f"✖ {label} getActiveRoute did not report success")
                return False
            if not isinstance(result.get("available"), bool):
                log_error(f"✖ {label} getActiveRoute did not report a boolean available")
                return False

        before_fields = _route_fields(before_route)
        after_fields = _route_fields(after_route)
        log_info(f"Observed active route: before={before_fields} after={after_fields}")

        if before_fields != after_fields:
            log_error("✖ active route changed across the malformed setupARCRouting request")
            log_warning(f"Expected: {before_fields}")
            log_warning(f"Actual  : {after_fields}")
            log_error("TCID32_Invalid_ARC_Routing_Nochange Failed")
            return False

        # SECOND, INDEPENDENT INVARIANT. The connected flag mirrors
        # HdmiCecSinkImplementation::hdmiCecAudioDeviceConnected, which is set by peer discovery
        # rather than by ARC routing, so its VALUE is not something this case pins - only its
        # STABILITY across the malformed request is, and both sides must agree on the type for the
        # comparison to mean anything.
        connected_before = before_audio.get("connected")
        connected_after = after_audio.get("connected")
        log_info(
            "Observed audio device connected state: "
            f"before={connected_before} after={connected_after}"
        )
        if not isinstance(connected_before, bool) or not isinstance(connected_after, bool):
            log_error("✖ getAudioDeviceConnectedStatus did not report a boolean connected")
            return False
        if connected_before != connected_after:
            log_error("✖ audio device connected status changed across the malformed request")
            log_error("TCID32_Invalid_ARC_Routing_Nochange Failed")
            return False

        elapsed_time = time.perf_counter() - start_time
        msg = "TCID32_Invalid_ARC_Routing_Nochange Passed"
        if os.environ.get("HDMICEC_TIMING_ENABLED"):
            log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
        else:
            log_success(msg)
        return True
    except Exception:
        # Broader than JSONDecodeError on purpose: a non-object result raises on .get(). Every
        # such shape failure leaves the invariant unconfirmed - the same verdict as a mismatch.
        log_error("Invalid JSON response")

    log_error("TCID32_Invalid_ARC_Routing_Nochange Failed")
    return False
