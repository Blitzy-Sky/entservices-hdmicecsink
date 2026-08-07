"""
/**
 * @file TCID19_Active_Path_Routing_Change_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID19_Active_Path_Routing_Change_Flow
 * @details Drives the sink's active-path and routing-change surface end to end and observes
 *          the route the plugin reports on either side of it. Five steps, in order:
 *            1. a route BEFORE-probe over org.rdk.HdmiCecSink.getActiveRoute;
 *            2. org.rdk.HdmiCecSink.setActivePath, which resolves the requested path and
 *               broadcasts <Set Stream Path>;
 *            3. org.rdk.HdmiCecSink.setRoutingChange, which resolves the old and new HDMI
 *               port identifiers against the sink's port map and broadcasts <Routing Change>;
 *            4. inbound <Routing Change>, <Routing Information> and <Set Stream Path> frames
 *               injected through the vComponent, so the peer-driven direction of the same
 *               exchange is exercised alongside the locally initiated one;
 *            5. a route AFTER-probe over the same getActiveRoute method.
 *
 *          The two setters are the reason this module exists. SetActivePath and
 *          SetRoutingChange are both catalogued P1 with coverage from the sink's own L2 suite
 *          and no end-to-end leg, because the sink has no device-level suite at all
 *          (COVERAGE_GAPS.md, "Missing sink device-level (E2E) suite"). This module supplies
 *          that leg for both, and in doing so walks the sink's nested route resolution, which
 *          is catalogued zero-hit: HdmiCecSinkImplementation::getActiveRoute
 *          (HdmiCecSinkImplementation.cpp:1958) plus the port-map operations it and the
 *          routing setters depend on - HdmiPortMap::addChild (HdmiCecSinkImplementation.h:294),
 *          removeChild (:327) and getRoute (:351). setRoutingChange reads
 *          hdmiInputs[portID].m_physicalAddr, so a port identifier resolves only once that map
 *          has been built. No coverage claim is made for any of them: this suite is authored
 *          here and NOT executed, so a coverage figure would be unmeasured. What is claimed is
 *          only that these paths are exercised.
 *
 *          The peer-driven direction is reached by INJECTING FRAMES only. No fixture
 *          reconfigures the device under test to act as its own peer; role flipping and role
 *          inversion are out of scope for this suite by directive.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - hosts the org.rdk.HdmiCecSink
 *    plugin and answers JSON-RPC at utils.WPEFRAMEWORK_JSONRPC_URL.
 *  - Init_Devicelist_Populate has run, so HDMI-CEC is enabled and the emulated CEC network is
 *    seeded. That topology is what makes route resolution more than trivially flat: it places
 *    a playback peer at logical address 4 - the active-source candidate the routing flows
 *    switch to - and a second playback peer at logical address 8, so a route change has
 *    somewhere to go, alongside the audio system at 5, a tuner at 3 and a recording device
 *    at 1. The television under test is logical address 0 and is deliberately not seeded.
 *  - The vComponent HTTP API is reachable at utils.VCOMPONENT_API_URL, and the YAML command
 *    documents under utils.HDMICEC_CMD_BASE are readable.
 *  - This suite is AUTHORED, NOT EXECUTED in this repository: no continuous integration
 *    workflow runs it, and nothing below has been observed against a device or an emulator.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - Both setters are accepted, all three injected frames are taken by the vComponent, and
 *    getActiveRoute still answers with a well-formed route description afterwards.
 *
 * @pass_criteria
 *  - Every required vComponent POST returns HTTP 200, setActivePath and setRoutingChange each
 *    acknowledge with success true, the after-probe parses with success true and a boolean
 *    available - and, when available is true, a well-formed length, pathList and ActiveRoute -
 *    and run_test() returns True.
 *
 * @failure_criteria
 *  - A command that cannot be sent, the transport sentinel, a vComponent POST that does not
 *    return HTTP 200, a setter that does not acknowledge success, a body that is not JSON, a
 *    malformed route description, or run_test() returning False.
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

# log_with_timing is imported and never called, knowingly rather than by oversight: it is the one
# pyflakes finding this file carries ("imported but unused"), and it is the same single finding
# the sibling case modules in this directory carry. The block above is the import contract they
# share, while the timing decoration is written INLINE at the point where the result is reported,
# so a diff between two cases shows only the behaviour under test.
#
# send_vcomponent_command and HDMICEC_CMD_BASE are what distinguish a flow module from a
# single-API one: only a flow injects frames, so only a flow needs the vComponent transport and
# the command-document base directory.


def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {body}")
    return http_code == 200


# FRAMING OF THE THREE INJECTED DOCUMENTS - BROADCAST HERE, AND EITHER WOULD WORK.
#
# run_test() posts the Process_-prefixed documents, whose payloads are broadcast (header 0x4F:
# initiator 4, destination F). Directed equivalents exist beside them - Device_Routing_Change,
# Device_Routing_Information and Device_Set_Stream_Path, header 0x50: initiator 5, destination 0
# - and either set reaches the handler under test, because these three sink handlers apply NO
# destination filter: HdmiCecSinkProcessor::process for RoutingChange
# (HdmiCecSinkImplementation.cpp:323), RoutingInformation (:327) and SetStreamPath (:331) never
# inspect header.to at all.
#
# Worth stating, because it is NOT the general rule here and the asymmetry invites a well-meant
# "correction": process(GetMenuLanguage) at :335 discards broadcasts outright, ActiveSource and
# RequestActiveSource filter too, and Init_Devicelist_Populate's own addressing contract has to
# send SetOSDName directed while sending ReportPhysicalAddress and DeviceVendorID broadcast for
# exactly that reason. The routing trio is the exception, so do not "fix" the framing below to
# match a neighbouring case - it is already correct, and the directed documents are equally so.


def run_test():
    start_time = time.perf_counter()

    # ------------------------------------------------------------------ BEFORE-PROBE
    log_info("Executing the curl command get active route (before the routing changes)")

    before = send_curl_command(HdmiCecSinkApis.get_active_route)

    if not before:
        log_error("✖ initial getActiveRoute command not sent")
        return False

    # utils.send_curl_command reports every transport failure as the byte-exact
    # "< No response from WPEFramework >" sentinel. That string is TRUTHY, so it survives the
    # emptiness check above and needs its own prefix guard - the test utils.py documents.
    # Without it an unreachable device would be carried into json.loads below and misreported
    # as a malformed payload, which points at the plugin instead of at the missing endpoint.
    if before.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_warning(f"Initial active route: {before}")

    # ONE try COVERS EVERY PARSE IN THIS MODULE: the two setter acknowledgements and both probe
    # bodies are all read inside the block below, so a single json.JSONDecodeError handler keeps
    # the contract of returning a bool on every path rather than raising into SuitManager's
    # runner. The TRANSPORT guards stay inline at each step, because "no reply arrived" and "a
    # reply arrived and was not JSON" are different verdicts and are reported as such.
    try:
        # -------------------------------------------------------------- ACT 1: setActivePath
        log_info("Executing the curl command set active path")

        # The sibling constant is dispatched verbatim. The requested path lives in
        # HdmiCECSink_Curl.set_active_path and only there, so no physical-address literal
        # appears in this module and an edit on one side cannot desynchronise the other.
        set_path_response = send_curl_command(HdmiCecSinkApis.set_active_path)

        if not set_path_response:
            log_error("✖ setActivePath command not sent")
            return False

        if set_path_response.startswith("< No response"):
            log_error("✖ no response from WPEFramework")
            return False

        # An error reply carries no "result" member, and send_curl_command returns the first
        # line that parses as JSON at all - which need not even be an object. Both are
        # normalised to an empty mapping so the predicate stays a plain .get() call.
        envelope = json.loads(set_path_response)
        result = envelope.get("result") if isinstance(envelope, dict) else None
        if not isinstance(result, dict):
            result = {}

        if result.get("success") is not True:
            log_error("✖ setActivePath did not acknowledge success")
            log_warning(f"Response: {set_path_response}")
            return False

        log_success("✔ curl command sent")
        log_warning(f"Response: {set_path_response}")

        time.sleep(1)

        # ------------------------------------------------------------ ACT 2: setRoutingChange
        log_info("Executing the curl command set routing change")

        # Same discipline as Act 1: the old and new port identifiers live in
        # HdmiCECSink_Curl.set_routing_change. They matter, because setRoutingChange resolves
        # each against hdmiInputs[portID].m_physicalAddr (HdmiCecSinkImplementation.cpp:2373)
        # and rejects an identifier the port map cannot place - exactly the port-map dependency
        # this case is here to walk.
        routing_change_response = send_curl_command(HdmiCecSinkApis.set_routing_change)

        if not routing_change_response:
            log_error("✖ setRoutingChange command not sent")
            return False

        if routing_change_response.startswith("< No response"):
            log_error("✖ no response from WPEFramework")
            return False

        envelope = json.loads(routing_change_response)
        result = envelope.get("result") if isinstance(envelope, dict) else None
        if not isinstance(result, dict):
            result = {}

        if result.get("success") is not True:
            log_error("✖ setRoutingChange did not acknowledge success")
            log_warning(f"Response: {routing_change_response}")
            return False

        log_success("✔ curl command sent")
        log_warning(f"Response: {routing_change_response}")

        time.sleep(1)

        # -------------------------------------------------- ACT 3: inbound frame injection
        # The peer-driven direction of the same exchange. Each post is a plain frame injection
        # through the vComponent: no fixture changes the device's role, and no payload is
        # hand-built here - see the framing note above the helper.
        log_info("Emulating peer-driven routing traffic towards the sink")

        ok1 = _post_hdmicec("Process_Routing_Change.yaml")
        time.sleep(1)
        ok2 = _post_hdmicec("Process_Routing_Information.yaml")
        time.sleep(1)
        ok3 = _post_hdmicec("Process_Set_Stream_Path.yaml")
        time.sleep(1)

        # All three are REQUIRED, and the check comes after all three rather than between them:
        # a missing document or a refused path returns (0, diagnostic) from
        # send_vcomponent_command, so posting the rest first makes the log name every document
        # that failed instead of only the first.
        if not (ok1 and ok2 and ok3):
            log_error("✖ required vComponent emulation posts failed")
            return False

        # ------------------------------------------------------------------- AFTER-PROBE
        log_info("Executing the curl command get active route (after the routing changes)")

        after = send_curl_command(HdmiCecSinkApis.get_active_route)

        if not after:
            log_error("✖ final getActiveRoute command not sent")
            return False

        if after.startswith("< No response"):
            log_error("✖ no response from WPEFramework")
            return False

        log_warning(f"Final active route: {after}")

        # The before-probe is parsed too, so a malformed initial reply is caught rather than
        # carried silently past the flow it was meant to characterise. Its VALUE is reported and
        # not asserted on - see the note below.
        before_envelope = json.loads(before)
        before_result = (
            before_envelope.get("result") if isinstance(before_envelope, dict) else None
        )
        if not isinstance(before_result, dict):
            before_result = {}

        after_envelope = json.loads(after)
        result = after_envelope.get("result") if isinstance(after_envelope, dict) else None
        if not isinstance(result, dict):
            result = {}

        has_success = result.get("success") is True
        has_available = isinstance(result.get("available"), bool)

        available = result.get("available")
        length = result.get("length")
        path_list = result.get("pathList")
        # Read from the key "ActiveRoute", with a CAPITAL A. It is the one field of the sink's
        # JSON-RPC surface that is not lowerCamelCase (IHdmiCecSink.h:191), so it is spelled
        # deliberately here and not by habit.
        route_text = result.get("ActiveRoute")

        if available is True:
            # A route is being reported, so its description must be well formed. ActiveRoute is
            # the one REQUIRED field - the only one the plugin sets on BOTH available-true
            # branches - and it must be non-empty: an available route with no description is not
            # a route.
            #
            # length and pathList are checked only WHEN PRESENT, and that is not laxness. The
            # television being its own active source takes the second branch of GetActiveRoute
            # (HdmiCecSinkImplementation.cpp:1527-1531), which sets available true and
            # ActiveRoute "TV" and never assigns a length or builds a path list - a state
            # reachable from this very flow, since setRoutingChange makes the television the
            # active source whenever the new port identifier names "TV" (:2408-2412). Demanding
            # both fields unconditionally would fail a correct device; the sink's own L2 test
            # guards its iterator the same way.
            route_valid = isinstance(route_text, str) and route_text != ""

            # bool is a subclass of int, so a reply rendering length as true/false would
            # otherwise satisfy an isinstance(int) test.
            if length is not None and (
                not isinstance(length, int) or isinstance(length, bool)
            ):
                route_valid = False

            if path_list is not None and not isinstance(path_list, list):
                route_valid = False

            # "length" is the length of the ROUTE, taken from route.size(), while the path list
            # is built by skipping every UNREGISTERED hop of that route (:1489-1501). So
            # len(pathList) is at most length and equality must not be demanded - but a path
            # list LONGER than the declared route length is a real defect.
            if isinstance(path_list, list) and isinstance(length, int):
                if not isinstance(length, bool) and len(path_list) > length:
                    route_valid = False
        else:
            # available is False, or absent - has_available already fails on absent. With no
            # peer acting as the active source the plugin answers
            # {"available":false,"length":0,"ActiveRoute":"","success":true}, so an empty route
            # set is the correct reply and demanding a populated route would be a false failure.
            route_valid = True

        # THE ROUTE VALUE IS REPORTED, NOT PINNED, AND THAT IS THE HONEST ASSERTION.
        #
        # Nothing above asserts a particular route string or a particular path length, and
        # nothing should. Which route the plugin last recorded depends on how the two setters
        # and the three injected frames interleave with whatever the preceding registered cases
        # left behind, and this suite does not pin that ordering beyond its registration list.
        # Worse for any fixed expectation, the three injected opcodes are handled by log-only
        # bodies (:323, :327, :331) that move no route state at all, so asserting the route
        # CHANGED after injection would assert behaviour the plugin does not implement. Shape
        # and acknowledgement are what is asserted; the before and after values are logged so a
        # human reading the run can see the transition that actually occurred.
        log_info(
            f"Route transition: before={before_result.get('ActiveRoute')!r} "
            f"after={route_text!r}  available: {available}  length: {length}  "
            f"pathList entries: "
            f"{len(path_list) if isinstance(path_list, list) else 'absent'}"
        )

        if has_success and has_available and route_valid:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID19_Active_Path_Routing_Change_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        log_warning(f"Actual  : {json.dumps(after_envelope, indent=2, sort_keys=True)}")
    except json.JSONDecodeError:
        log_error("Invalid JSON response")

    log_error("TCID19_Active_Path_Routing_Change_Flow Failed ❌")
    return False


# NO RESTORE STEP HERE, AND ITS ABSENCE IS DELIBERATE.
#
# This case leaves the routing state wherever the last setter and the last injected frame put
# it. There is no inverse operation to call - the sink's JSON-RPC surface publishes setActivePath
# and setRoutingChange but nothing that restores a previously observed route - and the only
# remaining way to force one would be to hand-build a CEC payload for the purpose, which this
# suite does not do, because every frame it injects comes from a reviewed document under
# vcomponent_configurations/commands/. Manufacturing a restore would also manufacture a claim:
# it would look like the device was returned to a known state when in fact one more untracked
# route change had been issued.
#
# The residual is safe for the cases that follow, by construction rather than by luck: every
# later flow module re-establishes its own preconditions through its own posts before asserting
# anything, and SuitManager.py's registration list - ordered on purpose, with the flows grouped
# at 17-27 - is what guarantees each of them runs after this one rather than beside it. A future
# case needing a specific starting route must seed it itself.
