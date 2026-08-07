"""
/**
 * @file TCID20_ARC_Initiation_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID20_ARC_Initiation_Flow
 * @details Drives ONE Audio Return Channel INITIATION end to end and then probes the two arms
 *          of the handler's admission gate with frames engineered to fail exactly one arm
 *          each. Six steps, in this order:
 *            1. before-probe - org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus, read for
 *               context and logged beside the after-probe rather than asserted;
 *            2. ENABLE - org.rdk.HdmiCecSink.setupARCRouting with {"enabled": true}, the sink's
 *               own request to bring ARC up (SetupARCRouting, IHdmiCecSink.h:328);
 *            3. POSITIVE INJECTION - Device_Initiate_Arc.yaml delivers the audio system's
 *               <Initiate ARC> to HdmiCecSinkProcessor::process(const InitiateArc&, const
 *               Header&) at HdmiCecSinkImplementation.cpp:508;
 *            4. NEGATIVE ARM A - Device_Initiate_Arc_Broadcast.yaml, same initiator, broadcast
 *               destination;
 *            5. NEGATIVE ARM B - Device_Initiate_Arc_Invalid_Initiator.yaml, directed
 *               destination, wrong initiator;
 *            6. after-probe - the same connected-status read, logged beside the first.
 *
 *          THE ADMISSION GATE IS WHY THE THREE FIXTURES DIFFER BY ONE BYTE. The handler opens
 *          with a single two-arm rejection at HdmiCecSinkImplementation.cpp:510 -
 *          `if((!(header.from.toInt() == 0x5)) || (header.to.toInt() ==
 *          LogicalAddress::BROADCAST)) return;` - so an <Initiate ARC> is admitted only when it
 *          comes FROM logical address 5, the audio system, AND is DIRECTED rather than
 *          broadcast. The header byte alone decides it:
 *            * 0x50 (positive)  initiator 5, destination 0 - both arms satisfied;
 *            * 0x5F (arm A)     initiator 5, destination 0xF broadcast - fails the destination
 *                               arm while holding the initiator constant;
 *            * 0x40 (arm B)     initiator 4, a playback device, destination 0 - fails the
 *                               initiator arm while holding the destination constant.
 *          Changing a header byte therefore changes which arm is under test; do not substitute
 *          fixtures between the steps.
 *
 *          WHAT THE TWO NEGATIVE INJECTIONS DO AND DO NOT PROVE. Both are expected to be
 *          ACCEPTED by the emulator - HTTP 200 means the frame reached the CEC bus - and then
 *          DISCARDED by the handler at the gate. Those two outcomes are not in tension, and
 *          this module never conflates them: a 200 on a negative fixture is evidence of
 *          injection, never of an ARC initiation. The rejection itself is unobservable from
 *          here, because the gate is a bare `return` that publishes no counter, no state and no
 *          notification, so the two posts are LOGGED AND NOT ASSERTED. They are carried anyway
 *          because injecting the frame is what exercises the gate on a real device; claiming
 *          the outcome would be the part this transport cannot support.
 *
 *          ARC IS DELIBERATELY LEFT ENABLED WHEN THIS CASE PASSES. This module is the PRODUCER
 *          half of an ordered pair: TCID21_ARC_Termination_Flow runs immediately after it
 *          (SuitManager.py registers the two at consecutive positions) and is the restorer that
 *          takes ARC back down. Tearing ARC down here would leave TCID21 nothing to terminate,
 *          so the absence of a restore step below is deliberate and is marked at the point
 *          where one would otherwise sit. The pair, run in order, returns the device to the
 *          state it was found in; this case on its own does not, and is not meant to.
 *
 *          ADDRESSED BY DESIGN, NOT BY MEASUREMENT. COVERAGE_GAPS.md ranks the missing sink
 *          vDeviceTests suite 22nd at priority P1 (#gap-plugin-sink-vdevicetests) precisely
 *          because the sink's ARC and audio-routing use cases - the ones that define the sink -
 *          had no end-to-end safety net. In the §4b API table `SetupARCRouting` is recorded as
 *          covered by the sink's own L2 suite (SetupARCRouting_COMRPC and
 *          SetupARCRouting_JSONRPC in ../../L2Tests/tests/HdmiCecSink_L2Test.cpp) with NO E2E
 *          leg. This module is AUTHORED to supply the INITIATION half of that leg and
 *          TCID21_ARC_Termination_Flow the other; runtime validation and measured coverage for
 *          both remain deferred until a device or emulator environment is available.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework with
 *    the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the emulated topology, including the YAMAHA
 *    AudioSystem peer at CEC logical address 5, and has left HDMI-CEC enabled. Without that
 *    peer the admission gate at HdmiCecSinkImplementation.cpp:510 can never be satisfied, since
 *    an <Initiate ARC> from any other address is discarded unread. The peer is supplied BY THE
 *    EMULATED TOPOLOGY: the device under test is never reconfigured to act as its own audio
 *    system, which is the role-inversion construct this suite excludes by design.
 *  - The vComponent HTTP API is reachable, so the three ARC frames can be injected.
 *  - TCID21_ARC_Termination_Flow is expected to run after this case and restore the ARC state
 *    this case leaves enabled.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - setupARCRouting acknowledges the enable request, and the directed <Initiate ARC> frame is
 *    injected and accepted by the emulator.
 *  - The two gate-arm frames are injected and are expected to be DISCARDED by the handler. That
 *    expectation is stated and logged, not asserted, for the reason given in the details
 *    section above.
 *  - THE ARC HANDSHAKE ITSELF IS NOT ASSERTED, because it is not observable from this
 *    transport. A successful initiation publishes arcInitiationEvent (ArcInitiationEvent,
 *    IHdmiCecSink.h:71), a Thunder notification delivered to registered COM-RPC/JSON-RPC
 *    subscribers rather than to a one-shot curl request/response, and no getter on the
 *    interface reports the ARC routing state.
 *  - The ARC state is left ENABLED for TCID21_ARC_Termination_Flow.
 *
 * @pass_criteria
 *  - EVERY vComponent post returns HTTP 200 - the <Initiate ARC> injection and both gate-arm
 *    injections alike - setupARCRouting acknowledges result.success as True, the after-probe
 *    parses with result.success True and a boolean result.connected, and run_test() returns True.
 *
 * @failure_criteria
 *  - A request is not dispatched, a response is the no-response sentinel, ANY vComponent post
 *    does not return HTTP 200 - including either gate-arm frame, whose non-delivery would leave
 *    this case claiming to have exercised a rejection arm it never reached - the enable call does
 *    not acknowledge success, the after-probe reports success other than True or a non-boolean
 *    connected, a JSON parsing error occurs, or run_test() returns False.
 */
"""

import time
import json

from utils import (
    send_curl_command,
    send_vcomponent_command,
    sanitise_for_log,
    HDMICEC_CMD_BASE,
    log_info,
    log_success,
    log_error,
    log_warning,
    log_with_timing,
    CEC_FRAME_PACING_SECONDS,
)
import HdmiCECSink_Curl as HdmiCecSinkApis


def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {sanitise_for_log(body)}")
    return http_code == 200


def _result_object(response_text):
    """Return the JSON-RPC result mapping from a response body, or an empty mapping.

    A JSON-RPC error envelope carries "error" instead of "result", and a malformed body could
    carry a non-object "result" or not be an object at all. Every such case collapses to {} so
    the caller reports a MISSING FIELD rather than raising AttributeError out of run_test(). A
    body that is not JSON at all still raises json.JSONDecodeError, which run_test() handles as
    the documented failure. Three call sites share this, which is why it is factored out.
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


# The CEC logical address of an audio system: the only initiator process(InitiateArc) admits, and
# the address Init_Devicelist_Populate bootstraps as a precondition of the whole suite.
AUDIO_SYSTEM_LOGICAL_ADDRESS = 5

# Bounded budget for the reachable observations. A poll interval, never a duration anything waits
# for: each loop leaves on the first satisfying reading and reports honestly on expiry.
OBSERVE_TIMEOUT_S = 8.0
OBSERVE_POLL_S = 0.25


def _device_inventory():
    '''Return (readable, count, sorted_logical_addresses) from the published getDeviceList method.

    readable is False when the reply could not be read as a JSON-RPC result reporting success,
    which is deliberately distinct from an empty inventory.
    '''
    response = send_curl_command(HdmiCecSinkApis.get_device_list)
    if not response or response.startswith("< No response"):
        return False, None, None
    try:
        result = _result_object(response)
    except json.JSONDecodeError:
        return False, None, None
    if result.get("success") is not True:
        return False, None, None
    device_list = result.get("deviceList")
    if not isinstance(device_list, list):
        return False, None, None
    addresses = sorted(
        device["logicalAddress"]
        for device in device_list
        if isinstance(device, dict) and isinstance(device.get("logicalAddress"), int)
    )
    return True, result.get("numberofdevices"), addresses


def _audio_connected():
    '''Return the sink's audio-device-connected flag, or None when it cannot be read.'''
    response = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    if not response or response.startswith("< No response"):
        return None
    try:
        result = _result_object(response)
    except json.JSONDecodeError:
        return None
    if result.get("success") is not True:
        return None
    connected = result.get("connected")
    return connected if isinstance(connected, bool) else None


def _wait_for_audio_connected(expected):
    '''Poll the audio-connected flag until it reads `expected`; returns (matched, observed).'''
    deadline = time.monotonic() + OBSERVE_TIMEOUT_S
    observed = None
    while True:
        observed = _audio_connected()
        if observed is expected:
            return True, observed
        if time.monotonic() >= deadline:
            return False, observed
        time.sleep(OBSERVE_POLL_S)


def run_test():
    '''Initiate ARC from the audio system and assert everything this transport can observe.

    WHAT IS AND IS NOT REACHABLE HERE, stated once because it governs every assertion below. The
    ARC state machine is internal: m_currentArcRoutingState has no published getter, and the
    outcome of an initiation is signalled only through the ArcInitiationEvent notification and an
    outbound <Report ARC Initiated> frame. A curl transport can subscribe to no notification and
    read no outbound frame, and the vComponent API is POST-only, so neither the ARC state nor its
    event can be observed at this level. Asserting them would be asserting something unmeasured.
    What IS asserted: every fixture post is REQUIRED rather than advisory, the enable call is
    acknowledged, and the invariants the flow must preserve - the audio system stays discovered and
    the device inventory is undisturbed - are checked on both sides of the exchange.
    Returns:
        True when every required post is accepted, setupARCRouting is acknowledged and both
        invariants hold; False on any transport failure, a refused post, an unreadable reply, or a
        disturbed invariant.
    '''
    start_time = time.perf_counter()

    # ── BEFORE: the invariants this flow must preserve ──────────────────────────────────────────
    inventory_readable, before_count, before_addresses = _device_inventory()
    if not inventory_readable:
        log_error("✖ the device inventory could not be read before the ARC exchange")
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    if AUDIO_SYSTEM_LOGICAL_ADDRESS not in before_addresses:
        log_error(
            f"✖ the audio system at logical address {AUDIO_SYSTEM_LOGICAL_ADDRESS} is not in the "
            "device list, so process(InitiateArc) would discard every frame this case injects - "
            "its gate admits that initiator only"
        )
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    connected_before = _audio_connected()
    if connected_before is not True:
        # Asserted, not merely logged: addDevice() sets the flag unconditionally when a peer becomes
        # present at address 5, and nothing in an ARC exchange clears it, so this is a measured
        # precondition rather than an environment-dependent reading.
        log_error(
            f"✖ audio device connected reads {connected_before!r} before the exchange, expected "
            "True given the audio system is present"
        )
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    log_info(
        f"Before: {before_count} devices at {before_addresses}, audio connected={connected_before}"
    )

    # ── ACT 1 - ENABLE ─────────────────────────────────────────────────────────────────────────
    # setupARCRouting({"enabled": true}) is the sink's own request to bring ARC up, and it must
    # precede the injections: Process_InitiateArc() is what an admitted frame reaches, and enabling
    # first is the order a real audio system and television negotiate in.
    #
    # Its acknowledgement is asserted but claims little: SetupARCRouting sets success true
    # UNCONDITIONALLY after calling startArc() (HdmiCecSinkImplementation.cpp:1600-1613), so a true
    # here means the call was dispatched, not that ARC came up. That distinction is why the ARC
    # state is reported as unobservable rather than inferred from this reply.
    curl_response = send_curl_command(HdmiCecSinkApis.setup_arc_routing_true)
    if not curl_response:
        log_error("✖ setupArcRouting command not sent")
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    # The transport sentinel is TRUTHY, so the falsy check above cannot see it; the prefix test is
    # the detection contract utils.py documents.
    if curl_response.startswith("< No response"):
        log_error("✖ setupArcRouting returned no response from WPEFramework")
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    log_warning(f"Response: {curl_response}")

    # ACT 2 - POSITIVE INJECTION, THE ONE POST THIS CASE REQUIRES. Device_Initiate_Arc.yaml
    # carries payload ["0x50","0xC0"]: header 0x50 is initiator 5 to destination 0, opcode 0xC0
    # is <Initiate ARC>. That header is the only one of the three fixtures that clears both arms
    # of the gate at HdmiCecSinkImplementation.cpp:510, so this is the frame that represents an
    # actual initiation and its post is required rather than advisory. Naming a file that does
    # not exist would make send_vcomponent_command return (0, "YAML file not found: ..."), which
    # would otherwise read as a passing ARC case that never injected anything - hence the check.
    ok_positive = _post_hdmicec("Device_Initiate_Arc.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)

    if not ok_positive:
        log_error("✖ required vComponent ARC initiation post failed")
        return False

    # ACT 3 - THE TWO GATE ARMS. DELIVERY IS REQUIRED; THE HANDLER'S VERDICT IS NOT ASSERTED.
    #
    # Each fixture below differs from the positive one in its HEADER BYTE ONLY, so each isolates
    # a single arm of the rejection at HdmiCecSinkImplementation.cpp:510 with the other arm held
    # constant. Both are expected to be accepted by the emulator and then discarded by the
    # handler.
    #
    # THE TWO OUTCOMES ARE SEPARATE, AND ONLY ONE OF THEM IS THIS TRANSPORT'S BUSINESS:
    #   * whether the frame was INJECTED - an HTTP 200 from the vComponent - is a fact about
    #     delivery, and it is now REQUIRED. A misnamed document, an unreadable fixture or an
    #     unreachable emulator each return (0, diagnostic) from send_vcomponent_command, and with
    #     that outcome tolerated this case would report having exercised both gate arms while
    #     injecting neither frame. Since a frame that never reached the bus also cannot change the
    #     connected flag, the unchanged-state reading below would hold for the wrong reason - the
    #     same trap the positive injection above is already guarded against.
    #   * whether the HANDLER accepted or discarded it is NOT asserted, and that remains the
    #     honest reporting line of this module. The gate is a bare `return`: it increments no
    #     counter, changes no state and raises no notification, so a rejection leaves nothing this
    #     transport can read. Requiring a non-200 would be worse still - it would assert the
    #     opposite of what should happen, since the emulator is expected to accept a well-formed
    #     frame regardless of what the plugin then does with it.
    #
    # So a 200 required below is evidence that the frame was INJECTED. It is not, and must not be
    # read as, evidence of an ARC initiation.
    ok_broadcast = _post_hdmicec("Device_Initiate_Arc_Broadcast.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)
    log_info(
        "  gate arm A (header 0x5F, broadcast destination) injected; expected to be discarded "
        f"by the handler - post accepted: {ok_broadcast}"
    )

    ok_invalid_initiator = _post_hdmicec("Device_Initiate_Arc_Invalid_Initiator.yaml")
    time.sleep(CEC_FRAME_PACING_SECONDS)
    log_info(
        "  gate arm B (header 0x40, initiator is not the audio system) injected; expected to be "
        f"discarded by the handler - post accepted: {ok_invalid_initiator}"
    )

    if not ok_broadcast:
        log_error(
            "✖ the broadcast-destination gate-arm frame was not delivered to the bus, so this "
            "case cannot claim to have exercised that arm of the rejection"
        )
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False

    if not ok_invalid_initiator:
        log_error(
            "✖ the invalid-initiator gate-arm frame was not delivered to the bus, so this case "
            "cannot claim to have exercised that arm of the rejection"
        )
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False

    # After-probe: the same read as the before-probe, so the pair can be compared in the log.
    after = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    if not after:
        log_error("✖ final getAudioDeviceConnectedStatus command not sent")
        return False
    if after.startswith("< No response"):
        log_error("✖ final getAudioDeviceConnectedStatus returned no response")
        return False
    log_warning(f"Final audio connection: {after}")

    # SHARED STATE LEFT BEHIND, ON PURPOSE. A teardown would sit exactly here - the disabling
    # counterpart of Act 1, setupARCRouting with {"enabled": false}, which HdmiCECSink_Curl.py
    # publishes as the `false` half of its setup_arc_routing pair - and it is absent by design
    # rather than by omission. TCID21_ARC_Termination_Flow is the paired consumer that terminates
    # what this case initiates, and SuitManager.py runs it immediately after this one, so
    # restoring here would leave that case nothing to terminate and would silently convert it
    # into a no-op. This module is consequently NOT self-restoring; the ORDERED PAIR is. Do not
    # add a teardown here without moving the termination coverage somewhere it can still be
    # reached.

    try:
        if _result_object(curl_response).get("success") is not True:
            log_error("✖ setupArcRouting did not acknowledge success")
            log_error("TCID20_ARC_Initiation_Flow Failed ❌")
            return False
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    log_success("✔ setupARCRouting(enabled=true) acknowledged")

    # ── ACT 2 and 3 - EVERY INJECTION IS REQUIRED ──────────────────────────────────────────────
    # Device_Initiate_Arc.yaml carries ["0x50","0xC0"]: header 0x50 is initiator 5 to destination 0,
    # opcode 0xC0 is <Initiate ARC>. That header is the only one of the three that clears both arms
    # of the gate at HdmiCecSinkImplementation.cpp:510, so it represents an actual initiation.
    #
    # The other two differ from it in the HEADER BYTE ONLY, each isolating one arm of that gate with
    # the other held constant: 0x5F is broadcast-destination, 0x40 has an initiator that is not the
    # audio system. An earlier revision posted them WITHOUT requiring the post to succeed, on the
    # grounds that the handler's rejection is unobservable. The rejection is indeed unobservable -
    # the gate is a bare return - but whether the frame was DELIVERED is not, and that is what a
    # 200 asserts. A silently missing fixture would otherwise leave a green ARC case that injected
    # nothing, which is precisely the failure being fixed here. So all three posts are required,
    # and each is checked where it is made so a refusal names the document that was refused.
    for yaml_name, description in (
        ("Device_Initiate_Arc.yaml", "the admitted <Initiate ARC> from the audio system"),
        ("Device_Initiate_Arc_Broadcast.yaml", "gate arm A: broadcast destination"),
        ("Device_Initiate_Arc_Invalid_Initiator.yaml", "gate arm B: initiator is not the audio system"),
    ):
        if not _post_hdmicec(yaml_name):
            log_error(f"✖ required injection refused - {description} ({yaml_name})")
            log_error("TCID20_ARC_Initiation_Flow Failed ❌")
            return False
        log_success(f"✔ delivered {description}")

    # ── AFTER: the invariants must still hold ──────────────────────────────────────────────────
    # The two rejected frames must change nothing, and the admitted one must not disturb the device
    # list either - ARC initiation moves the ARC state machine, not the inventory. Both are real
    # regression guards: a handler that registered a device from a rejected frame, or dropped one
    # while initiating, would fail here.
    connected_after, observed = _wait_for_audio_connected(True)
    if not connected_after:
        log_error(
            f"✖ audio device connected reads {observed!r} after the ARC exchange, expected True - "
            "an ARC initiation must not undiscover the audio system"
        )
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False

    after_readable, after_count, after_addresses = _device_inventory()
    if not after_readable:
        log_error("✖ the device inventory became unreadable after the ARC exchange")
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    if (after_count, after_addresses) != (before_count, before_addresses):
        log_error(
            f"✖ the ARC exchange disturbed the device inventory: "
            f"{before_count}/{before_addresses} -> {after_count}/{after_addresses}"
        )
        log_error("TCID20_ARC_Initiation_Flow Failed ❌")
        return False
    log_success(
        f"✔ invariants hold: {after_count} devices at {after_addresses}, audio connected=True"
    )

    elapsed_time = time.perf_counter() - start_time
    log_success(log_with_timing("TCID20_ARC_Initiation_Flow Passed ✅", elapsed_time))
    return True


# THE RESTORE IS THE PAIR'S, AND IT IS GUARANTEED RATHER THAN HOPED FOR.
#
# A teardown would sit exactly here - setupARCRouting with {"enabled": false}, which
# HdmiCECSink_Curl.py publishes as the `false` half of its setup_arc_routing pair - and it is absent
# by design. TCID21_ARC_Termination_Flow is the paired consumer that terminates what this case
# initiates; disabling ARC here would leave that case nothing to terminate and silently convert it
# into a no-op.
#
# What makes the pair reliable rather than merely ordered: the restore lives in TCID21's cleanup()
# hook, and SuitManager runs cleanup() for every registered case unconditionally - INCLUDING a case
# it skipped because its producer failed. So if this initiation fails and TCID21 never runs as a
# test, ARC is still disabled afterwards. The dependency is declared in
# SuitManager.TEST_DEPENDENCIES rather than left to registration order alone.
#
# The initial ARC state needs no capture: m_currentArcRoutingState is ARC_STATE_ARC_TERMINATED from
# construction (:635), Init_Devicelist_Populate does not touch ARC, and no case registered before
# this one calls setupARCRouting - so "terminated" is the state at entry by construction, which is
# exactly what TCID21's cleanup() re-establishes.
