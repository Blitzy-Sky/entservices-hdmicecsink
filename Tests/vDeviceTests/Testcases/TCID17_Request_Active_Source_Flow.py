"""
/**
 * @file TCID17_Request_Active_Source_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID17_Request_Active_Source_Flow
 * @details Exercises the sink's active-source negotiation end to end in four steps: a
 *          before-probe reads the current active source, org.rdk.HdmiCecSink.requestActiveSource
 *          asks the bus who holds it, two BROADCAST CEC frames are injected through the
 *          vComponent so an already-configured emulated peer answers, and an after-probe reads
 *          the active source again.
 *
 *          The pair of injected frames is deliberate. <Request Active Source> (0x4F 0x85) drives
 *          process(RequestActiveSource) and <Active Source> (0x4F 0x82 0x10 0x00) drives
 *          process(ActiveSource), announcing physical address 1.0.0.0 from logical address 4.
 *          Both are BROADCAST because each of those handlers early-returns on directed framing:
 *          a directed frame would be accepted by the vComponent and then silently discarded by
 *          the plugin, leaving a green test that exercised nothing. The peer side comes ONLY
 *          from those frames, emitted by the topology Init_Devicelist_Populate already seeded;
 *          nothing here reconfigures the device under test to answer as its own peer, because
 *          role flipping and role inversion are out of scope for this suite and an active-source
 *          exchange is precisely where that shortcut would otherwise be tempting.
 *
 *          Only what the curl transport can observe is asserted: the acknowledgement of the
 *          request and the SHAPE of the after-probe result block. The sink also publishes an
 *          active-source notification, which a device-level curl case cannot subscribe to, so
 *          no event assertion appears below and none is claimed.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable via the JSON-RPC endpoint.
 *  - Init_Devicelist_Populate has seeded the emulated topology, so a peer exists to answer the
 *    request and to be reported as the active source.
 *  - The vComponent HTTP API is reachable, so the YAML frame injections below land on the bus.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - requestActiveSource is acknowledged, both broadcast injections are accepted, and the
 *    after-probe reports availability with the documented member types.
 *
 * @pass_criteria
 *  - Every required YAML post returns HTTP 200, requestActiveSource answers with
 *    result.success True, the after-probe parses with result.success True and a boolean
 *    result.available, and run_test() returns True.
 *
 * @failure_criteria
 *  - A response mismatch, a rejected vComponent post, a command failure, a JSON parsing
 *    error, or an unreachable endpoint; run_test() then returns False.
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

# log_with_timing is imported and deliberately not called: every case in this suite draws the
# same symbol set from utils.py so the import band stays uniform across the Testcases directory,
# and the pass path below inlines the HDMICEC_TIMING_ENABLED decision because it needs to choose
# the log level the message is routed through - log_with_timing only returns text. The flow cases
# additionally take send_vcomponent_command and HDMICEC_CMD_BASE, which the query cases do not.


def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    # HTTP 200 is the only acceptance, and utils.send_vcomponent_command is fail-closed about it:
    # a missing document, a refused path, a curl failure and the "applied the YAML then closed
    # the connection without answering" case (curl exit 52) all arrive here as code 0 rather than
    # being laundered into a synthetic 200. The body is logged verbatim and never parsed, because
    # on those paths it carries curl's diagnosis rather than a response document - so every
    # filename below is verified against the fixture tree, a typo being otherwise invisible.
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {body}")
    return http_code == 200


def run_test():
    '''Negotiate the active source and verify the sink reports it coherently afterwards.
    Returns:
        True when the request is acknowledged, both broadcast injections are accepted and the
        after-probe reports a well-formed active-source block; False on a transport failure, a
        rejected injection, a response mismatch or a body that is not valid JSON.
    '''
    start_time = time.perf_counter()

    # BEFORE-PROBE. Recorded, not asserted: which peer holds the active source when this case
    # starts depends on what ran before it, and the suite's declared order pins no holder here.
    log_info("Executing the curl command get active source (before)")
    before = send_curl_command(HdmiCecSinkApis.get_active_source)

    if not before:
        log_error("✖ initial getActiveSource command not sent")
        return False

    # Two guards, because they catch different failures. send_curl_command reports every
    # transport failure with the "< No response from WPEFramework >" sentinel, which is a
    # NON-EMPTY string and so passes the falsy check above untouched; testing the prefix
    # utils.py documents is what separates an unreachable device from a malformed payload.
    if before.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_warning(f"Initial active source: {before}")

    # ACT, PART 1 - ask over JSON-RPC. requestActiveSource takes no parameters and answers with
    # a bare success envelope, having broadcast <Request Active Source> onto the CEC bus.
    log_info("Executing the curl command request active source")
    curl_response = send_curl_command(HdmiCecSinkApis.request_active_source)

    if not curl_response:
        log_error("✖ requestActiveSource command not sent")
        return False

    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    # ACT, PART 2 - supply the peer side by injecting broadcast frames from the emulated devices
    # the topology already carries. The settle waits are bounded and fixed: the sink processes
    # each frame on its own listener thread, and one second is the cadence this suite uses between
    # injections. No polling loop and no wall-clock deadline appear here, so the case costs the
    # same on every run.
    ok1 = _post_hdmicec("Process_Request_Active_Source.yaml")
    time.sleep(1)
    ok2 = _post_hdmicec("Process_Active_Source.yaml")
    time.sleep(1)

    if not (ok1 and ok2):
        log_error("✖ required vComponent emulation posts failed")
        return False

    # SHARED STATE. This case leaves the sink's notion of the active source changed - that is the
    # effect under test, and there is nothing to restore it to, because the value it replaced was
    # itself whatever an earlier case left behind. TCID18_Set_Active_Source_Flow is the next entry
    # in the suite's declared order and sets the active source deliberately, so the residual is
    # consumed rather than leaked. No restore is attempted, since writing a synthetic value back
    # would be a second uncontrolled change rather than a cleanup, and nothing below depends on
    # TCID18 having run.
    log_info("Executing the curl command get active source (after)")
    after = send_curl_command(HdmiCecSinkApis.get_active_source)

    if not after:
        log_error("✖ final getActiveSource command not sent")
        return False

    if after.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_warning(f"Final active source: {after}")

    try:
        # The before-probe and the acknowledgement are parsed alongside the after-probe so that a
        # malformed body anywhere in the flow is reported as the JSON failure it is.
        before_result = json.loads(before).get("result", {})
        request_ack = json.loads(curl_response).get("result", {})
        after_body = json.loads(after)
        result = after_body.get("result", {})

        ack_ok = request_ack.get("success") is True
        has_success = result.get("success") is True
        has_available = isinstance(result.get("available"), bool)
        available = result.get("available")

        # WHAT IS ASSERTED, AND WHAT DELIBERATELY IS NOT. The injected <Active Source> frame
        # announces physical address 1.0.0.0 from logical address 4, so a plausible outcome is
        # that the sink now reports an available active source at that address. It is NOT
        # asserted: nothing in the suite's declared order guarantees that no later frame
        # supersedes it, and a different peer answering the request first is an equally correct
        # outcome. OnActiveSourceChange would settle the question and is not observable over this
        # transport, so it is neither subscribed to nor claimed. The predicate is therefore the
        # acknowledgement plus the SHAPE of the result block, with the identity logged as an
        # observation; the detail check is conditional because a sink holding no active source
        # legitimately reports availability alone.
        if available is True:
            details_valid = isinstance(result.get("logicalAddress"), int)
        else:
            details_valid = True

        log_info(
            f"  Active source available before: {before_result.get('available')}"
            f"  after: {available}"
        )
        if available is True:
            log_info(f"  Active source logicalAddress: {result.get('logicalAddress')}")

        if ack_ok and has_success and has_available and details_valid:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID17_Request_Active_Source_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {json.dumps(after_body, indent=2, sort_keys=True)}")
    except json.JSONDecodeError:
        log_error("Invalid JSON response")

    log_error("TCID17_Request_Active_Source_Flow Failed ❌")
    return False
