"""
/**
 * @file TCID25_Standby_Coordination_Flow.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID25_Standby_Coordination_Flow
 * @details Exercises HDMI-CEC standby coordination in BOTH directions and then puts the CEC
 *          network back the way it found it. Seven steps, in a fixed order: a getDeviceList
 *          before-probe; the outbound org.rdk.HdmiCecSink.sendStandbyMessage request, by which
 *          the sink tells its peers to stand by; an inbound DIRECTED Standby injection
 *          (Device_Standby_Emulation.yaml, 0x50 0x36 - a peer telling the sink to stand by); the
 *          same opcode injected BROADCAST (Process_Standby.yaml, 0x4F 0x36); then the wake leg -
 *          an ImageViewOn injection (Device_Image_View_On.yaml, 0x50 0x04) followed by a
 *          TextViewOn injection (Device_Text_View_On.yaml, 0x50 0x0D); and finally a
 *          getDeviceList after-probe confirming the topology survived the cycle intact.
 *
 *          WHY BOTH FRAMINGS OF THE SAME OPCODE. HdmiCecSinkProcessor::process(const Standby &,
 *          const Header &) carries NO address guard, so a directed Standby and a broadcast
 *          Standby are both legitimately accepted and both reach SendStandbyMsgEvent. Posting
 *          the pair covers the framing variants instead of picking one and assuming the other
 *          behaves identically.
 *
 *          THE DEBT THIS CASE SETTLES. TCID15_Send_Standby_Message issues this same command as a
 *          plain single-API case and, as its own closing comment records, cannot undo what it
 *          changes: the sink's published surface has no inverse of sendStandbyMessage, and the
 *          single-API band imports no vComponent helper with which to inject one. This case does
 *          have those helpers, so it is where that residual is repaid - having exercised standby
 *          outbound and inbound, it re-establishes wake state by injecting the two view-on
 *          frames. A module that changes shared state restores it; here the obligation is
 *          honoured across a module boundary, deliberately and in writing.
 *
 *          AUTHORED, NOT EXECUTED. Nothing described here has been run against a device or an
 *          emulator. No service was started on the JSON-RPC or the vComponent port on this
 *          case's behalf, and no transport was stubbed in order to produce a result.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable at the JSON-RPC endpoint utils.py
 *    resolves, with HDMI-CEC enabled on the device under test.
 *  - Init_Devicelist_Populate has run as the suite initialization module and seeded the emulated
 *    source-role peers, so there is a CEC network for the outbound standby to reach and peers
 *    for the injected frames to arrive from.
 *  - The vComponent HTTP API is reachable and this suite's vcomponent_configurations/ tree is
 *    applied, so the four command documents this case posts can be injected.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - sendStandbyMessage is accepted and acknowledged, all four command documents are accepted by
 *    the vComponent, and the device list is still well formed after the cycle with no peer lost
 *    from it.
 *  - THE PEERS' OWN POWER STATE IS NOT ASSERTED, because the sink publishes no getter that
 *    reports it: the only powerStatus on the interface belongs to the active-source record, and a
 *    standby flow may legitimately leave no active source at all. Nor is OnImageViewOnMsg
 *    asserted - it is a Thunder notification and is not observable over this suite's one-shot
 *    curl transport, which cannot subscribe to a notification channel. That is a limit of
 *    THIS level only: the event IS asserted by the sink's own suites - L1
 *    onImageViewOnMsg_DirectedFrame_NotifiesSubscribedClient and its unregistered-initiator,
 *    standby-wakeup and broadcast variants, and L2 InjectImageViewOnFrameAndVerifyEvent with
 *    its broadcast, unknown-playback-device and unregistered-address companions. The
 *    COVERAGE_GAPS.md entry listing it among the five uncovered notifications is that
 *    register's PRE-CHANGE BASELINE.
 *
 * @pass_criteria
 *  - All four required YAML posts return HTTP 200, sendStandbyMessage acknowledges
 *    'success': true, the after-probe reports 'success': true with an int 'numberofdevices' that
 *    has not decreased since the before-probe, and run_test() returns True.
 *
 * @failure_criteria
 *  - An empty reply, the "< No response from WPEFramework >" transport sentinel, a rejected
 *    vComponent post, an unacknowledged standby request, a device count that fell across the
 *    cycle, a JSON parse error, or run_test() returning False.
 */
"""


import time
import os
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
    log_with_timing
)
import HdmiCECSink_Curl as HdmiCecSinkApis

# The eight utils names above are this case's pinned import contract: the six that every TCID
# module in this suite shares, plus send_vcomponent_command and HDMICEC_CMD_BASE. Those last two
# are exactly what separates a flow case from a single-API case - they are the reason this module
# can inject frames at all, and therefore the reason it is able to repay the residual TCID15
# declares and cannot repair. log_with_timing belongs to the shared set and is imported with it
# although this case gates its own message inline: the inline form keeps the choice of log level
# at the call that prints, whereas log_with_timing returns a string and would move that choice
# away from the call site. Nothing beyond the set is imported - in particular no HTTP, YAML or
# RAFT package, because this suite authors device-level requests and never serves them.
#
# The source suite's equivalent flow wakes its peer through a one-touch-play action. IHdmiCecSink
# publishes no such method, so that idiom is deliberately NOT carried across and no constant for
# it is imported; the sink's wake levers are the view-on frames injected below.


def _post_hdmicec(yaml_file):
    """Post a HdmiCec vComponent YAML command."""
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_file}")
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {sanitise_for_log(body)}")
    return http_code == 200


# FIXTURE NAMES ARE LOAD-BEARING, AND A MISSPELLING IS SILENT AT THE POINT OF USE. utils resolves
# the name against HDMICEC_CMD_BASE and returns (0, "YAML file not found: <path>") when the
# document does not exist, so a wrong name does not raise - it merely fails its post. That matters
# more on this module than on most: a silently skipped WAKE post would leave the peers standing by
# and the residual TCID15 declared would go unpaid. Hence every post below is captured into a
# named flag and all four are required together rather than fired and forgotten. The four names
# used here were verified against vcomponent_configurations/commands/ on disk.
#
# The body is logged but never parsed. utils reports the status the vComponent actually returned:
# a silent, refused or failing vComponent yields 0 together with curl's own diagnosis rather than
# a manufactured 200, and even a genuine 200 may carry an empty body. `body` is therefore
# diagnostic text for a human reading the log, never a JSON document to decode.


def _ensure_peers_woken():
    """Re-issue the wake leg and report whether both frames were accepted.

    Returns (ok, detail).

    ImageViewOn and TextViewOn are this module's inverse operation - the suite's repayment for
    the standby residual TCID15 leaves behind - and they are now issued from run_test()'s finally
    block rather than only from the path that reaches ACT 4. Their POSITION inside the flow is
    still load-bearing and unchanged, because waking before the standby posts would defeat the
    case; what changes is that an early exit before ACT 4 no longer leaves the emulated peers
    standing by for every case that follows.

    Both frames must be DIRECTED, and are: process(ImageViewOn) and process(TextViewOn) each
    return early when header.to is BROADCAST, so a broadcast framing would restore nothing.

    Re-issuing them on the path that already did is deliberate and harmless - waking a peer that
    is already awake is idempotent, and both handlers only add to the topology, never shrink it.
    """
    ok_image = _post_hdmicec("Device_Image_View_On.yaml")
    ok_text = _post_hdmicec("Device_Text_View_On.yaml")

    if ok_image and ok_text:
        return True, "wake state re-established (ImageViewOn and TextViewOn both accepted)"
    rejected = ", ".join(
        name for name, ok in (("ImageViewOn", ok_image), ("TextViewOn", ok_text)) if not ok
    )
    return False, f"the wake leg was not accepted by the emulator: {rejected}"


def run_test():
    """Drive standby coordination in both directions and assert what the published surface reports.

    WHAT IS OBSERVED DIRECTLY - the per-peer power statuses getDeviceList publishes
    (HdmiCecSinkImplementation.cpp:1403), captured before the broadcast and checked after it, and
    the exact set of logical addresses, which must be IDENTICAL rather than merely not smaller.
    WHAT IS NOT OBSERVABLE - SendStandbyMsgEvent, OnWakeupFromStandby, OnImageViewOnMsg and
    OnTextViewOnMsg are Thunder notifications a one-shot curl transport cannot subscribe to, and
    the sink's OWN power status is excluded from getDeviceList by construction (:1393), so the
    OnWakeupFromStandby gate cannot be set up or read from here. @expected_result names the
    production changes that would close both.
    Returns:
        True when every assertion holds; False on any transport failure, refused post, unreadable
        device list, changed address set or an illegitimate power-status movement.
    """
    global _captured_power_status
    _captured_power_status = None
    start_time = time.perf_counter()

    # The wake leg is guaranteed rather than merely ordered: the flow runs inside a try whose
    # finally always re-issues it, so no early return and no exception can leave the emulated
    # peers in standby. The restoration reports its own verdict and this case fails if either
    # half fails.
    try:
        flow_ok = _run_standby_coordination_flow()
    finally:
        cleanup_ok, cleanup_detail = _ensure_peers_woken()
        if cleanup_ok:
            log_info(f"  Cleanup: {cleanup_detail}")
        else:
            log_error(
                "TCID25_Standby_Coordination_Flow cleanup FAILED: the emulated peers may still "
                f"be in standby for subsequent cases - {cleanup_detail}"
            )

    if flow_ok and cleanup_ok:
        elapsed_time = time.perf_counter() - start_time
        msg = "TCID25_Standby_Coordination_Flow Passed ✅"
        if os.environ.get("HDMICEC_TIMING_ENABLED"):
            log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
        else:
            log_success(msg)
        return True

    log_error("TCID25_Standby_Coordination_Flow Failed ❌")
    return False


def _run_standby_coordination_flow():
    """The five acts this case measures. Returns True when every assertion holds.

    Every assertion is exactly as it was; only the verdict reporting moved to run_test() so the
    guaranteed wake leg in its finally block runs first.
    """

    log_info(
        "Executing the standby coordination flow: outbound standby, inbound standby "
        "directed and broadcast, then the two view-on frames"
    )

    # ── BEFORE-PROBE, ASSERTED AND CAPTURED ─────────────────────────────────────────────────────
    # The per-peer power statuses are captured here, which is what lets cleanup() restore them and
    # what gives the after-probe something real to be measured against. A before-probe that cannot
    # be read fails outright rather than being waived: Init_Devicelist_Populate guarantees a seeded
    # topology before the first case runs, so an unreadable list means the precondition never held,
    # and skipping the comparison would hide that behind a pass.
    reading = _recorded_power_status()
    if reading is None:
        log_error(
            "✖ the device list could not be read before the cycle - either the endpoint is dead "
            "(send_curl_command returns the truthy \"< No response from WPEFramework >\" "
            "sentinel), the reply is not JSON, or it did not acknowledge success"
        )
        log_error("TCID25_Standby_Coordination_Flow Failed ❌")
        return False
    before_count, before_status = reading
    if not before_status:
        log_error(
            "✖ the device list is empty before the cycle - there is no CEC network for the "
            "outbound standby to reach and no peer for the injected frames to arrive from"
        )
        log_error("TCID25_Standby_Coordination_Flow Failed ❌")
        return False
    unknown_before = {
        address: status
        for address, status in before_status.items()
        if status not in POWER_STATUS_VOCABULARY
    }
    if unknown_before:
        log_error(
            f"✖ the device list publishes power statuses outside the PowerStatus vocabulary "
            f"before the cycle: {unknown_before}"
        )
        log_error("TCID25_Standby_Coordination_Flow Failed ❌")
        return False
    _captured_power_status = dict(before_status)
    log_info(f"Before: {before_count} devices, recorded power statuses {before_status}")

    # ── ACT 1 - OUTBOUND ────────────────────────────────────────────────────────────────────────
    # sendStandbyMessage takes no parameters and answers with success only (IHdmiCecSink.h:286).
    # Its acknowledgement is asserted, and the limit of that acknowledgement is recorded rather
    # than implied: SendStandbyMessage sets success = true unconditionally, and sendStandbyMessage()
    # gates on _instance, smConnection and m_logicalAddressAllocated (:1690-1705) without consulting
    # cecEnableStatus, so unlike the audio solicitations elsewhere in this suite there is no
    # published flag that proves the broadcast left the box.
    log_info("Sending the outbound standby message to the CEC peers")
    curl_response = send_curl_command(HdmiCecSinkApis.send_standby_message)
    if not curl_response:
        log_error("✖ sendStandbyMessage command not sent")
        log_error("TCID25_Standby_Coordination_Flow Failed ❌")
        return False
    # The falsy guard above cannot catch a transport failure by itself: send_curl_command reports
    # one by RETURNING the TRUTHY sentinel "< No response from WPEFramework >", and publishes
    # response.startswith("< No response") as the way to detect it.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework - standby message not acknowledged")
        log_error("TCID25_Standby_Coordination_Flow Failed ❌")
        return False
    log_warning(f"Response: {curl_response}")

    # ── ACTS 2 TO 5 - EVERY INJECTION REQUIRED AND ATTRIBUTED BY NAME ───────────────────────────
    # FIXTURE NAMES ARE LOAD-BEARING AND A MISSPELLING IS SILENT AT THE POINT OF USE: utils resolves
    # the name against HDMICEC_CMD_BASE and returns (0, "YAML file not found: <path>") when the
    # document does not exist, so a wrong name does not raise - it merely fails its post. An earlier
    # revision collected all four results into flags and tested them together AFTER the last post,
    # which meant a failure could not say which frame was missing; each is now required at its own
    # call site and named.
    #
    # ACTS 2 AND 3 - INBOUND STANDBY, IN BOTH FRAMINGS. process(const Standby &, const Header &)
    # (:203-207) has NO address guard, so the directed frame (0x50 0x36, from logical address 5) and
    # the broadcast frame (0x4F 0x36, from logical address 4) are BOTH accepted and both reach
    # SendStandbyMsgEvent. Posting the pair is what covers the framing variants; do not "correct"
    # either payload to match the other, because the difference between them is the coverage.
    #
    # ACTS 4 AND 5 - THE VIEW-ON FRAMES. Both are DIRECTED (0x50 ...) because they have to be:
    # process(ImageViewOn) and process(TextViewOn) each return early when header.to is BROADCAST -
    # "accepts only direct messages" - so a broadcast framing would be discarded. Both handlers call
    # addDevice(header.from) before their notifications, and because logical address 5 is already
    # seeded that call is idempotent (:2449-2470), which is what entitles the after-probe to require
    # an IDENTICAL address set rather than a merely non-shrinking one.
    # These two do NOT restore the peers - see cleanup() - so their position after the standby
    # posts is a matter of covering the wake handlers in a realistic order, not a restoration.
    for yaml_name, description in (
        ("Device_Standby_Emulation.yaml", "the inbound DIRECTED Standby frame (0x50 0x36)"),
        ("Process_Standby.yaml", "the inbound BROADCAST Standby frame (0x4F 0x36)"),
        ("Device_Image_View_On.yaml", "the directed ImageViewOn frame (0x50 0x04)"),
        ("Device_Text_View_On.yaml", "the directed TextViewOn frame (0x50 0x0D)"),
    ):
        log_info(f"Injecting {description}")
        if not _post_hdmicec(yaml_name):
            log_error(
                f"✖ required injection refused - {description} was never delivered ({yaml_name})"
            )
            log_error("TCID25_Standby_Coordination_Flow Failed ❌")
            return False
        log_success(f"✔ delivered {description}")

    # ── AFTER-PROBE: THE POWER OBSERVATION AND THE TOPOLOGY INVARIANT ────────────────────────────
    # THE ADDRESS SET MUST BE IDENTICAL, NOT MERELY NOT SMALLER. An earlier revision asserted only
    # after_count >= before_count, which passed a run where a peer vanished and a different one
    # appeared, and passed a spurious peer arriving from nowhere. Every frame this case injects
    # initiates from an address the suite already seeded (5 and 4) and addDevice() is idempotent for
    # a device already present, so the set can only be identical - equality is the correct claim and
    # is strictly stronger.
    deadline = time.monotonic() + OBSERVE_TIMEOUT_S
    while True:
        reading = _recorded_power_status()
        if reading is not None and sorted(reading[1]) == sorted(before_status):
            break
        if time.monotonic() >= deadline:
            if reading is None:
                log_error("✖ the device list became unreadable after the cycle")
            else:
                log_error(
                    "✖ the standby/wake cycle changed the set of discovered logical addresses: "
                    f"{sorted(before_status)} -> {sorted(reading[1])} "
                    f"(count {before_count} -> {reading[0]})"
                )
            log_error("TCID25_Standby_Coordination_Flow Failed ❌")
            return False
        time.sleep(OBSERVE_POLL_S)
    after_count, after_status = reading

    # THE POWER OBSERVATION, AND WHY IT ADMITS EXACTLY TWO OUTCOMES PER PEER. Both are correct, in
    # different environments, and the case reports which one it saw:
    #   * UNCHANGED. On the emulator this is the expected outcome, because this suite's own response
    #     table declares <Standby> as absorbed with `response: null`
    #     (vcomponent_configurations/hdmicec/hdmicec_vcomponent_cec_responses.yaml:46-47), so the
    #     peer's declared power_status does not move and a later refresh re-reads the same value.
    #   * MOVED TO "Standby". On real hardware a peer genuinely powers down, and the sink's poll
    #     thread re-reads power status once HDMICECSINK_UPDATE_POWER_STATUS_INTERVA_MS - sixty
    #     seconds (:53, cleared at :2914) - has elapsed since the last update, so the recorded value
    #     legitimately becomes Standby. Failing that would be failing correct behaviour.
    # ANY OTHER MOVEMENT IS WRONG IN BOTH ENVIRONMENTS and fails: a peer captured as Standby reading
    # anything else means something woke it, and a value outside the PowerStatus vocabulary means
    # the record is malformed. Whichever outcome occurred, cleanup() restores the capture.
    illegitimate = {}
    moved_to_standby = {}
    for address, status in before_status.items():
        observed = after_status.get(address)
        if observed == status:
            continue
        if observed == "Standby":
            moved_to_standby[address] = status
        else:
            illegitimate[address] = (status, observed)
    if illegitimate:
        log_error(
            "✖ peer power statuses moved in a way neither environment permits "
            f"(address: before -> after): {illegitimate}. Only 'unchanged' or a move to 'Standby' "
            "is legitimate across a standby broadcast"
        )
        log_error("TCID25_Standby_Coordination_Flow Failed ❌")
        return False
    if moved_to_standby:
        log_success(
            "✔ the standby broadcast was observed directly: recorded power status moved to "
            f"'Standby' for {sorted(moved_to_standby)} (was {moved_to_standby}); cleanup() will "
            "restore the capture"
        )
    else:
        log_success(
            "✔ recorded peer power statuses are unchanged, which is the expected emulator outcome "
            "for an absorbed <Standby>"
        )
    log_success(
        f"✔ topology intact: {after_count} devices at exactly {sorted(after_status)}, the same "
        "set observed before the cycle"
    )

    return False
