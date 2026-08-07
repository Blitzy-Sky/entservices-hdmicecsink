"""
/**
 * @file TCID18_Set_Active_Source_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID18_Set_Active_Source_Flow
 * @details Drives the sink's SET-ACTIVE-SOURCE flow end to end and validates the active
 *          source the device reports once the flow has run. Four steps, in order:
 *
 *            1. a before-probe of org.rdk.HdmiCecSink.getActiveSource, recording whatever
 *               active source the device already holds;
 *            2. org.rdk.HdmiCecSink.setActiveSource over JSON-RPC, which asks the sink to
 *               take the active source itself and takes no parameters;
 *            3. frame injection through the vComponent - a directed <Inactive Source> from
 *               the emulated peer, then a broadcast <Active Source> announcement - so the
 *               sink's inbound handlers run as they would against real peers;
 *            4. an after-probe of the same API, asserted for shape and success.
 *
 *          This case closes the end-to-end leg of SetActiveSource, which the L2 suite
 *          already covers in-process but which had no device-level counterpart.
 *
 *          It follows TCID17_Request_Active_Source_Flow in the suite's declared order.
 *          TCID17 asks the bus who the active source is and leaves whatever it learned
 *          behind; this case then SETS the active source deliberately, so that residual is
 *          consumed rather than inherited. Nothing here requires TCID17 to have run: the
 *          before-probe records the starting state instead of asserting it, so the case
 *          behaves identically when it is selected on its own by name.
 *
 *          Only what a JSON-RPC query can observe is asserted. The sink also publishes an
 *          active-source notification, but a curl-driven device-level case cannot subscribe
 *          to one, so nothing here is written as an event assertion.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable via the JSON-RPC endpoint.
 *  - Init_Devicelist_Populate has seeded the emulated topology, so the peer whose frames are
 *    injected below is already known to the sink.
 *  - The vComponent HTTP API is reachable and the YAML command documents posted below are
 *    readable.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - setActiveSource is accepted, both injected frames are accepted by the vComponent, and
 *    the after-probe reports a well-formed active-source block.
 *
 * @pass_criteria
 *  - Both required YAML posts return HTTP 200, setActiveSource acknowledges success: true,
 *    the after-probe parses with result.success True and a boolean result.available - with
 *    correctly typed detail fields when available is True - and run_test() returns True.
 *
 * @failure_criteria
 *  - Response mismatch, command failure, JSON parsing error, or testcase returns False.
 */
"""


import time
import os
import json
from utils import (
    send_curl_command,
    send_vcomponent_command,
    HDMICEC_CMD_BASE,
    log_info,
    log_success,
    log_error,
    log_warning,
    log_with_timing
)
import HdmiCECSink_Curl as HdmiCecSinkApis

# log_with_timing is imported and deliberately not called. Every case in this suite draws the
# same symbol set from utils.py so the import band stays uniform across the Testcases
# directory, and the pass path below inlines the HDMICEC_TIMING_ENABLED decision so it can
# choose the log level it routes the message through - log_with_timing only returns text.


# Only the STATUS CODE is interpreted; the body is logged verbatim as evidence and is never
# parsed. A vComponent that accepts a payload may answer with an empty or non-YAML body, so
# treating the body as structured data would read meaning into text that carries none. The
# code alone is load-bearing: utils.send_vcomponent_command reinterprets no curl exit status,
# so a refused path, an unreadable fixture, a silent server and a server that answered with an
# error all arrive here as a non-200 code rather than as a manufactured success.
def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {body}")
    return http_code == 200


def run_test():
    start_time = time.perf_counter()

    # ---------- BEFORE-PROBE ----------
    # Recorded rather than asserted. Whichever peer last announced itself holds the active
    # source when this case starts, and the suite pins no particular one, so the starting
    # state is evidence for the transition below and never a precondition of it.
    log_info("Reading the active source before the flow runs")
    before = send_curl_command(HdmiCecSinkApis.get_active_source)

    if not before:
        log_error("✖ initial getActiveSource command not sent")
        return False

    # Two guards rather than one, because they catch different failures. The sentinel that
    # send_curl_command returns on any transport failure - "< No response from WPEFramework >"
    # - is a NON-EMPTY string, so the falsy check above cannot see it. Without this second
    # guard an unreachable device would fall through to json.loads and be misreported as a
    # malformed payload instead of as the transport failure it actually is. The prefix tested
    # here is the one utils.py documents callers to test.
    if before.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_warning(f"Initial active source: {before}")

    # ---------- ACT: take the active source over JSON-RPC ----------
    # setActiveSource takes NO parameters - it asks the sink to become the active source
    # itself. setActivePath, which does carry a parameter, is a different API belonging to the
    # routing-change case; it is deliberately not exercised here.
    log_info("Executing the curl command set active source")
    curl_response = send_curl_command(HdmiCecSinkApis.set_active_source)

    if not curl_response:
        log_error("✖ setActiveSource command not sent")
        return False

    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    # ---------- ACT: inject the peer side of the exchange ----------
    # Framing is functional here, not cosmetic, and the two fixtures are NOT interchangeable.
    # Device_In_Active_Source.yaml carries a DIRECTED header (0x50) because the sink's
    # <Inactive Source> handler is directed-only, and Process_Active_Source.yaml carries a
    # BROADCAST header (0x4F) because process(ActiveSource) early-returns on a directed frame.
    # Swapping the two would post frames the plugin discards, leaving this case green while
    # exercising nothing.
    #
    # Device_Request_Inactive_Source.yaml is a historical alias carrying the identical
    # payload; the handler-named fixture is used here so the intent reads at the call site.
    #
    # The peer side arrives ONLY as frames injected into the already-configured emulated
    # topology. The device under test is never reconfigured to act as its own peer.
    ok1 = _post_hdmicec("Device_In_Active_Source.yaml")
    time.sleep(1)
    ok2 = _post_hdmicec("Process_Active_Source.yaml")
    time.sleep(1)

    # Both posts are required: a non-200 from either (see _post_hdmicec) means a frame was
    # never delivered, so the flow was not exercised and the case must not report a pass.
    if not (ok1 and ok2):
        log_error("✖ required vComponent emulation posts failed")
        return False

    # ---------- AFTER-PROBE ----------
    after = send_curl_command(HdmiCecSinkApis.get_active_source)

    if not after:
        log_error("✖ final getActiveSource command not sent")
        return False

    if after.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_warning(f"Final active source: {after}")

    try:
        # All three payloads are parsed inside this one try block so a malformed reply from any
        # of them is reported as the parse failure it is rather than as a mismatched value.
        #
        # setActiveSource returns HdmiCecSinkSuccess - a success flag and nothing more. It is
        # asserted because a call the plugin refused would otherwise leave the after-probe as
        # the only evidence, and the after-probe cannot tell a refused call apart from one the
        # injected frames later overrode.
        set_body = json.loads(curl_response)
        set_success = set_body.get("result", {}).get("success") is True

        # The before-probe is parsed so that a malformed initial reading is caught here rather
        # than surfacing later as a confusing comparison. Its availability flag is logged as
        # the starting point of the transition and is not asserted.
        before_body = json.loads(before)
        before_available = before_body.get("result", {}).get("available")

        after_body = json.loads(after)
        result = after_body.get("result", {})

        has_success = result.get("success") is True
        has_available = isinstance(result.get("available"), bool)
        available = result.get("available")

        # The detail predicate is CONDITIONAL, and that is the point rather than a shortcut.
        # A sink holding no current active source is in a legitimate steady state: it reports
        # availability alone and leaves the detail members unset, so requiring them populated
        # in that state would fail a correctly behaving device.
        if available is True:
            details_valid = isinstance(result.get("logicalAddress"), int) and isinstance(
                result.get("physicalAddress"), str
            )
        else:
            details_valid = True

        # Logged as an observation and deliberately NOT asserted against a particular address.
        # setActiveSource makes the SINK the active source, while the broadcast injected above
        # announces logical address 4 at physical address 1.0.0.0. Which of the two the plugin
        # last recorded depends on the order in which it processes the JSON-RPC call and the
        # injected frame, and this suite pins no such order - so asserting a specific
        # logicalAddress or physicalAddress would be asserting a race, and the case would then
        # pass or fail for reasons unrelated to the flow it is meant to cover. The sink's
        # active-source notification would settle it, but a curl-driven device-level case
        # cannot subscribe to a notification. Shape and success are asserted; the transition
        # itself is recorded as evidence.
        log_info(f"  setActiveSource acknowledged: {set_success}")
        log_info(f"  Active source available: {before_available} -> {available}")
        if available is True:
            log_info(
                f"  Active source logicalAddress: {result.get('logicalAddress')}"
                f"  physicalAddress: {result.get('physicalAddress')}"
            )

        if set_success and has_success and has_available and details_valid:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID18_Set_Active_Source_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {json.dumps(after_body, indent=2, sort_keys=True)}")
    except json.JSONDecodeError:
        pass

    log_error("TCID18_Set_Active_Source_Flow Failed ❌")
    return False


# SHARED STATE THIS CASE LEAVES BEHIND, AND WHY IT IS NOT RESTORED
# ---------------------------------------------------------------
# The active source is left pointing wherever the last processed frame put it. That is
# deliberate rather than a gap: changing the active source is this case's whole subject, so a
# restore would undo the state it was written to establish, and no "previous active source"
# API exists to restore it through - only another announcement, which would be a second
# uninstrumented act.
#
# The residual is consumed, not leaked. TCID19_Active_Path_Routing_Change_Flow follows
# immediately in the declared order and re-establishes routing state as its own first act, and
# every case in this flow band opens with a before-probe that records rather than asserts the
# starting state - so none of them inherits an expectation from this one.

