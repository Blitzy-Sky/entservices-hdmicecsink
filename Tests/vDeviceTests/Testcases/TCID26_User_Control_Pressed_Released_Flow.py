"""
/**
 * @file TCID26_User_Control_Pressed_Released_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID26_User_Control_Pressed_Released_Flow
 * @details Exercises the sink's user-control path in both directions, and carries this
 *          suite's device-level boundary-value coverage for it.
 *
 *          Outbound leg: org.rdk.HdmiCecSink.sendUserControlPressed is dispatched over
 *          JSON-RPC and its success acknowledgement asserted, then
 *          org.rdk.HdmiCecSink.sendUserControlReleased is dispatched and asserted in turn.
 *          The two APIs are asymmetric by contract - pressed carries a key code alongside
 *          the logical address, released carries the logical address alone - and both are
 *          driven from the command constants in HdmiCECSink_Curl.py, so no address or key
 *          code is restated here and neither can drift out of step with the fixtures.
 *
 *          Inbound leg: three UserControlPressed frames are injected through the vComponent
 *          emulation API, each closed by a UserControlReleased frame. The three key codes
 *          are the NOMINAL value, the MINIMUM value and the upper BYTE BOUNDARY value, which
 *          is the same three-point sweep the sink L1 suite already applies to these two APIs
 *          as sendUserControlPressed_MinKeyCode and sendUserControlPressed_BoundaryKeyCode
 *          (and their sendUserControlReleased_ counterparts). Reusing that idiom at device
 *          level is deliberate: it is the established repository convention for a
 *          parameterised API rather than a shape invented here.
 *
 *          The rejected-argument half of the boundary set is deliberately NOT attempted at
 *          this level and is not missing from the estate: sendUserControlPressed and
 *          sendUserControlReleased are each already asserted at L1 with
 *          _InvalidLogicalAddress, _InvalidKeyCode, _MissingParams, _MalformedJSON,
 *          _NegativeValues and _StringValues variants. Those cases need to observe a
 *          REJECTION, and this suite's command constants expose only well-formed requests,
 *          so duplicating them here would add no coverage and could only weaken the split.
 *
 *          Closing probe: getDeviceList is read once at the end as a liveness check. It is
 *          not a key-delivery assertion - see @expected_result - but a burst of six injected
 *          frames that left the plugin unable to answer would be a genuine regression, and
 *          this is the only consequence of the flow that the L3 transport can observe.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework
 *    with the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the CEC topology, so the logical address carried by
 *    the outbound command constants resolves to a real emulated peer - the Audio System,
 *    which is this suite's bootstrap peer - rather than to an empty address.
 *  - The vComponent emulation API is reachable, since the three inbound key codes arrive as
 *    posted YAML command documents rather than as anything this module builds itself.
 *  - No continuous integration workflow in this repository executes this suite; this case is
 *    authored for device-level execution and has not been run.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - Both outbound calls are acknowledged with {"success": true}, all six injected frames
 *    are accepted with HTTP 200, and the closing getDeviceList probe still answers.
 *  - The OnKeyPressEvent and OnKeyReleaseEvent notifications the injected frames provoke are
 *    NOT observable at this level and nothing is asserted about them. This suite reaches the
 *    plugin over one-shot curl, which cannot subscribe to a Thunder notification channel, so
 *    an event assertion here would be unfounded. Both notifications remain uncovered - the
 *    coverage register lists them among the events uncovered even by the sink's own L2 suite
 *    - and closing them belongs to that L2 work item, not to this module. They are reported
 *    as uncovered rather than implied to be tested.
 *
 * @pass_criteria
 *  - All six required YAML posts return HTTP 200, both JSON-RPC calls acknowledge
 *    {"success": true}, the closing probe parses as an object whose result carries
 *    success true and an integer numberofdevices, and run_test() returns True.
 *
 * @failure_criteria
 *  - A response mismatch, a failed or silently skipped emulation post, a JSON parsing
 *    failure, an unreachable endpoint or an unavailable device-level prerequisite;
 *    run_test() then returns False.
 */
"""


import time
import os
import json

# The eight-symbol utils import below is the shared contract the emulation-driven ("flow")
# testcases in this suite are written against: the six names every TCID module shares, plus
# send_vcomponent_command and HDMICEC_CMD_BASE, which are what make frame injection possible
# and therefore what make the inbound half of this flow possible at all. log_with_timing
# belongs to that set and is imported with it even though this module gates its own timing
# line on HDMICEC_TIMING_ENABLED inline: the inline form keeps the choice of log level at the
# call that prints, whereas log_with_timing returns a string and would move that choice away
# from the call site. Keeping the set identical across the flow modules is what lets one be
# diffed against another, so the single resulting "imported but unused" lint note is accepted
# convention here rather than an oversight. Nothing beyond the set is imported - in particular
# no HTTP, YAML or RAFT package, because this suite authors device-level requests and never
# serves them.
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


def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {body}")
    return http_code == 200


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could
    carry a non-object "result" or not be an object at all. Every such case collapses to {} so
    the caller reports a MISSING FIELD rather than raising AttributeError out of run_test(),
    which owes its caller a bool on every path. A body that is not JSON at all still raises
    json.JSONDecodeError, which run_test() handles as the documented failure. Three call sites
    share this - the two outbound acknowledgements and the closing probe - which is why it is
    factored out here, in the idiom TCID23_Short_Audio_Descriptor_Flow established.
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


# FIXTURE NAMES ARE LOAD-BEARING, AND A MISSPELLING IS SILENT AT THE POINT OF USE. utils
# resolves the name against HDMICEC_CMD_BASE and returns (0, "YAML file not found: <path>")
# when the document does not exist, so a wrong name does not raise - it merely fails its post.
# That matters more on this module than on any other in the suite, because this is the module
# that carries the boundary sweep: a silently skipped Min or Boundary post would quietly
# reduce the sweep to the nominal case while every remaining check still passed, and the
# missing coverage would be invisible in a green log. Every post below is therefore captured
# into a named flag and all six are required together rather than fired and forgotten. The
# four filenames used here were verified against vcomponent_configurations/commands/ on disk.
#
# The body is logged but never parsed. utils reports the status the vComponent actually
# returned and is fail-closed about it: a silent, refused or failing vComponent yields 0
# together with curl's own diagnosis rather than a manufactured 200, and even a genuine 200
# may carry an empty body. `body` is therefore diagnostic text for a human reading the log,
# never a JSON document to decode.


# FRAMING OF THE SIX INJECTED DOCUMENTS - DIRECTED FROM LOGICAL ADDRESS 5, AND CHOSEN, NOT
# INHERITED.
#
# run_test() posts the Device_-prefixed documents, whose payloads are directed with header
# 0x50 (initiator 5, destination 0). Process_-prefixed equivalents exist beside them -
# Process_User_Control_Pressed and Process_User_Control_Released, header 0x40, initiator 4 -
# and the sink handlers would accept either, because process(UserControlPressed) and
# process(UserControlReleased) apply no destination filter. So this is a genuine choice, and
# it is settled by the topology rather than by taste: Init_Devicelist_Populate seeds the
# audio system at logical address 5 as this suite's bootstrap peer, and HdmiCECSink_Curl.py
# records that logical address 4 has no peer in that topology. Injecting from 5 therefore
# models a frame from a peer that actually exists and matches the address the outbound
# constants target, which keeps both legs of this flow describing one conversation with one
# device. Injecting from 4 would model a frame from nothing.
#
# One family is used throughout for that reason. Do not mix the two sets below: the point of
# the sweep is that the three documents differ ONLY in their key-code operand, so changing
# the header on one of them would turn a controlled comparison into two unrelated variables.


def run_test():
    '''Drive the outbound user-control pair, sweep three inbound key codes, verify liveness.

    Every injected press is closed by an injected release, so the flow leaves no key held down
    on the sink and needs no further restore clause. That pairing is functional rather than
    cosmetic: a press left outstanding is exactly the stuck-key fault the coverage register's
    OnKeyReleaseEvent rationale warns about, and it would also leak state into whichever case
    the suite happens to run next.
    Returns:
        True when both outbound calls are acknowledged, all six emulation posts are accepted
        and the closing probe reports success with an integer device count; False on a
        transport failure, a failed post, a response mismatch or a body that is not valid
        JSON.
    '''
    start_time = time.perf_counter()

    log_info(
        "Executing the user-control flow: outbound pressed and released, then inbound "
        "nominal, minimum and boundary key codes, each press closed by a release"
    )

    # ACT 1 - OUTBOUND PRESS. sendUserControlPressed takes a logical address AND a key code
    # (IHdmiCecSink.h:275) and answers with success only, so the acknowledgement is the whole
    # of what it can be asserted on. Both parameter values are carried by the command constant
    # itself and are never restated here, so they cannot drift away from it.
    log_info("Dispatching the outbound sendUserControlPressed call")
    pressed_response = send_curl_command(HdmiCecSinkApis.send_user_control_pressed)

    if not pressed_response:
        log_error("✖ sendUserControlPressed command not sent")
        return False

    # The falsy guard above cannot catch a transport failure by itself: send_curl_command
    # reports one by RETURNING the TRUTHY sentinel "< No response from WPEFramework >", and
    # publishes response.startswith("< No response") as the way to detect it. Without this
    # second guard an unreachable device would reach the parse below and be misreported as a
    # malformed payload rather than as the dead endpoint it actually is.
    if pressed_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework - user control pressed not acknowledged")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {pressed_response}")
    time.sleep(1)

    # ACT 2 - OUTBOUND RELEASE, WHICH CLOSES ACT 1. sendUserControlReleased takes the logical
    # address ALONE (IHdmiCecSink.h:281) - the asymmetry with pressed is by contract, since a
    # release identifies no key - and likewise answers with success only.
    #
    # This call is not an optional extra: without it the outbound leg would leave the peer
    # holding the key this case just pressed, which is the stuck-key condition described in
    # this function's docstring. Ordering is therefore load-bearing, not stylistic.
    log_info("Dispatching the outbound sendUserControlReleased call to close the press")
    released_response = send_curl_command(HdmiCecSinkApis.send_user_control_released)

    if not released_response:
        log_error("✖ sendUserControlReleased command not sent")
        return False

    if released_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework - user control released not acknowledged")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {released_response}")
    time.sleep(1)

    # ACTS 3 TO 8 - THE INBOUND BOUNDARY SWEEP. THIS IS THE REASON THIS MODULE EXISTS.
    #
    # Three UserControlPressed frames are injected, differing ONLY in their key-code operand -
    # nominal, minimum, upper byte boundary - and each is closed by the same UserControlReleased
    # frame. That is the same three-point sweep the sink L1 suite applies to these APIs as
    # sendUserControlPressed_MinKeyCode and sendUserControlPressed_BoundaryKeyCode, lifted to
    # device level rather than reinvented.
    #
    # Each press is paired with a release IN SEQUENCE rather than the three presses being fired
    # and then released together. Pairing them keeps at most one key outstanding at any moment,
    # so a failure part-way through this block cannot leave two or three keys held; and it means
    # the sink observes three complete press-release cycles, which is what a real remote sends.
    #
    # The key codes themselves live in the YAML documents, so no operand value appears in this
    # file and no frame is hand-built here. Adding a fourth key code would mean adding a fourth
    # fixture; the fixture set is the vocabulary of this sweep and is not extended from here.
    log_info("Injecting the inbound UserControlPressed frame with the nominal key code")
    ok_nominal = _post_hdmicec("Device_User_Control_Pressed.yaml")
    time.sleep(1)

    log_info("Injecting the UserControlReleased frame to close the nominal press")
    ok_rel_1 = _post_hdmicec("Device_User_Control_Released.yaml")
    time.sleep(1)

    log_info("Injecting the inbound UserControlPressed frame with the minimum key code")
    ok_min = _post_hdmicec("Device_User_Control_Pressed_Min.yaml")
    time.sleep(1)

    log_info("Injecting the UserControlReleased frame to close the minimum press")
    ok_rel_2 = _post_hdmicec("Device_User_Control_Released.yaml")
    time.sleep(1)

    log_info("Injecting the inbound UserControlPressed frame with the boundary key code")
    ok_boundary = _post_hdmicec("Device_User_Control_Pressed_Boundary.yaml")
    time.sleep(1)

    log_info("Injecting the UserControlReleased frame to close the boundary press")
    ok_rel_3 = _post_hdmicec("Device_User_Control_Released.yaml")
    time.sleep(1)

    # All six posts are required TOGETHER. Tolerating any one of them would report a pass on a
    # run that never delivered part of the sweep - a dropped Min or Boundary press would silently
    # shrink this case to the nominal key code, and a dropped release would leave a key held.
    # Neither shortfall is visible downstream, so it has to be caught here.
    if not (ok_nominal and ok_min and ok_boundary and ok_rel_1 and ok_rel_2 and ok_rel_3):
        log_error("✖ required vComponent emulation posts failed")
        return False

    # CLOSING PROBE - A LIVENESS CHECK, NOT A KEY-DELIVERY CHECK.
    #
    # getDeviceList is read once here because it is the only consequence of this flow the L3
    # transport can actually observe. Six injected frames that left the plugin unable to answer,
    # or answering without a device count, would be a real regression and a genuine signal; that
    # is the whole of what this probe claims. It deliberately does NOT stand in for evidence that
    # any key arrived - see the assertion block below.
    log_info("Reading the device list as a closing liveness check")
    after = send_curl_command(HdmiCecSinkApis.get_device_list)

    if not after:
        log_error("✖ final getDeviceList command not sent")
        return False

    if after.startswith("< No response"):
        log_error("✖ no response from WPEFramework - final device list unavailable")
        return False

    log_warning(f"Final device list: {after}")

    try:
        # All three bodies are decoded together inside one try, so a single JSONDecodeError
        # handler covers them and the case reports one verdict from one place. _result_object
        # absorbs the defensive shapes - a JSON-RPC error envelope, a non-object body, a
        # non-object "result" - into an empty mapping, so a structurally wrong payload fails
        # through the predicates below as a MISMATCH instead of escaping as an AttributeError.
        pressed_result = _result_object(pressed_response)
        released_result = _result_object(released_response)
        after_result = _result_object(after)

        pressed_ack = pressed_result.get("success") is True
        released_ack = released_result.get("success") is True

        after_count = after_result.get("numberofdevices")
        alive_ack = after_result.get("success") is True
        has_count = isinstance(after_count, int)
        log_info(f"  device count reported by the closing probe: {after_count}")

        # WHAT IS ASSERTED, AND WHAT IS DELIBERATELY NOT.
        #
        # Asserted: the two outbound acknowledgements, the six emulation posts (gated above),
        # and that the plugin still answers getDeviceList with a success envelope carrying an
        # integer device count.
        #
        # NOT asserted, and NOT implied anywhere in this module: that any key press or key
        # release actually reached the sink's handlers, and that OnKeyPressEvent or
        # OnKeyReleaseEvent fired. Those are Thunder notifications, and this suite's one-shot
        # curl transport cannot subscribe to a notification channel, so there is no observation
        # to make here - only an assumption that could be dressed up as one. The coverage
        # register lists both events among those uncovered even by the sink's own L2 suite, and
        # they stay uncovered after this module lands. Reporting that honestly is the point:
        # inventing an event assertion at this level would convert a known gap into a false
        # green, which is worse than the gap.
        #
        # The device count is likewise NOT compared against a before-probe. User-control frames
        # carry no address discovery, so this flow has no defensible expectation about how the
        # count should move, and asserting a direction would be a guess. Its type is checked
        # because a probe that answers without an integer count is a malformed response
        # regardless of what this flow did.
        if pressed_ack and released_ack and alive_ack and has_count:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID26_User_Control_Pressed_Released_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        # Which predicate broke is named before the payloads are dumped, so a failure is read
        # from the verdict rather than reconstructed from three JSON documents.
        log_warning(
            f"Checks - pressed acknowledged: {pressed_ack}  "
            f"released acknowledged: {released_ack}  "
            f"probe acknowledged: {alive_ack}  count is int: {has_count}"
        )
        log_warning(f"Pressed  : {pressed_response}")
        log_warning(f"Released : {released_response}")
        log_warning(f"Probe    : {after}")
    except json.JSONDecodeError:
        # No return from this handler, by design: control falls through to the single failure
        # tail below, so the case reports one verdict from one place however it failed. The
        # diagnostic is logged here because a parse error and a payload mismatch are different
        # faults, and this suite's idiom is to name which one occurred.
        log_error("Invalid JSON response")

    log_error("TCID26_User_Control_Pressed_Released_Flow Failed ❌")
    return False

