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
 *    asserted - it is a Thunder notification, one of the five COVERAGE_GAPS.md records as
 *    uncovered even by the sink's own L2 suite, and it is not observable over this suite's
 *    one-shot curl transport. Closing that event belongs to the sink L2 work item, not here.
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
    log_info(f"  vComponent POST {yaml_file}: HTTP {http_code}  {body}")
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


def run_test():
    start_time = time.perf_counter()

    log_info(
        "Executing the standby coordination flow: outbound standby, inbound standby "
        "directed and broadcast, then wake through the two view-on frames"
    )

    # BEFORE-PROBE. The topology is read first so the after-probe has something to be measured
    # against; the one quantitative claim this case makes is a comparison between the two.
    before = send_curl_command(HdmiCecSinkApis.get_device_list)

    if not before:
        log_error("✖ initial getDeviceList command not sent")
        return False

    # The falsy guard above cannot catch a transport failure by itself: send_curl_command reports
    # one by RETURNING the TRUTHY sentinel "< No response from WPEFramework >", and publishes
    # response.startswith("< No response") as the way to detect it. Without this second guard an
    # unreachable device would reach json.loads below and be misreported as a malformed payload
    # rather than as the dead endpoint it actually is.
    if before.startswith("< No response"):
        log_error("✖ no response from WPEFramework - initial device list unavailable")
        return False

    log_warning(f"Initial device list: {before}")

    # ACT 1 - OUTBOUND. sendStandbyMessage takes no parameters and answers with success only
    # (IHdmiCecSink.h:286), so an acknowledgement is the whole of what it can be asserted on.
    log_info("Sending the outbound standby message to the CEC peers")
    curl_response = send_curl_command(HdmiCecSinkApis.send_standby_message)

    if not curl_response:
        log_error("✖ sendStandbyMessage command not sent")
        return False

    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework - standby message not acknowledged")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")
    time.sleep(1)

    # ACTS 2 AND 3 - INBOUND STANDBY, IN BOTH FRAMINGS. process(const Standby &, const Header &)
    # has NO address guard, so the directed frame (0x50 0x36, from logical address 5) and the
    # broadcast frame (0x4F 0x36, from logical address 4) are BOTH accepted and both reach
    # SendStandbyMsgEvent. Posting the pair is what covers the framing variants; do not "correct"
    # either payload to match the other, because the difference between them is the coverage.
    log_info("Injecting the inbound directed Standby frame")
    ok_directed = _post_hdmicec("Device_Standby_Emulation.yaml")
    time.sleep(1)

    log_info("Injecting the inbound broadcast Standby frame")
    ok_broadcast = _post_hdmicec("Process_Standby.yaml")
    time.sleep(1)

    # ACTS 4 AND 5 - THE WAKE LEG. ITS POSITION IN THIS SEQUENCE IS LOAD-BEARING.
    #
    # These two posts MUST follow both standby posts. Reversed, the case would wake the peers and
    # then immediately put them back into standby, leaving the network in exactly the state TCID15
    # leaves it in and defeating the reason this module exists - it is the place the suite repays
    # that residual. Here the ordering IS the restoration, not a stylistic preference.
    #
    # Both frames are DIRECTED (0x50 ...) because they have to be: process(ImageViewOn) and
    # process(TextViewOn) each return early when header.to is BROADCAST - "accepts only direct
    # messages" - so a broadcast framing would be discarded and would restore nothing. Both
    # handlers also call addDevice(header.from) before updating their view-on state, which is what
    # entitles the after-probe below to require that the device count has not fallen: this leg can
    # only hold the topology steady or add to it.
    log_info("Re-establishing wake state: injecting the ImageViewOn frame")
    ok_image = _post_hdmicec("Device_Image_View_On.yaml")
    time.sleep(1)

    log_info("Re-establishing wake state: injecting the TextViewOn frame")
    ok_text = _post_hdmicec("Device_Text_View_On.yaml")
    time.sleep(1)

    # All four posts are required TOGETHER. Tolerating a failed wake post would report a pass on a
    # run that left the peers standing by, which is the one outcome this case exists to prevent.
    if not (ok_directed and ok_broadcast and ok_image and ok_text):
        log_error("✖ required vComponent emulation posts failed")
        return False

    # AFTER-PROBE. Read through the same API as the before-probe so the two are comparable.
    after = send_curl_command(HdmiCecSinkApis.get_device_list)

    if not after:
        log_error("✖ final getDeviceList command not sent")
        return False

    if after.startswith("< No response"):
        log_error("✖ no response from WPEFramework - final device list unavailable")
        return False

    log_warning(f"Final device list: {after}")

    try:
        # Both probes are decoded defensively, in the idiom TCID02_Get_Devicelist established: a
        # payload that parses as JSON but is not an object - or whose "result" member is not one -
        # is a response MISMATCH that must fail through the predicates below rather than escape
        # from here as an AttributeError, because run_test() owes its caller a bool on every path.
        # The empty mapping substituted in that case leaves the real payload intact for the dump.
        before_envelope = json.loads(before)
        after_envelope = json.loads(after)

        before_result = before_envelope.get("result") if isinstance(before_envelope, dict) else {}
        after_result = after_envelope.get("result") if isinstance(after_envelope, dict) else {}
        if not isinstance(before_result, dict):
            before_result = {}
        if not isinstance(after_result, dict):
            after_result = {}

        before_count = before_result.get("numberofdevices")
        after_count = after_result.get("numberofdevices")
        log_info(f"  device count before: {before_count}  after: {after_count}")

        has_success = after_result.get("success") is True
        has_count = isinstance(after_count, int)

        # STABILITY IS THE CLAIM, NOT POWER STATE. A standby/wake cycle must not cost the sink a
        # peer, and because the wake leg's handlers call addDevice() the count can only hold or
        # grow - so a fall is a genuine regression and is the strongest quantitative assertion
        # available at this level. An unparseable before-count fails here rather than being
        # waived: Init_Devicelist_Populate guarantees a seeded topology before the first case
        # runs, so a before-probe reporting no integer count means the precondition never held,
        # and silently skipping the comparison would hide that behind a pass.
        #
        # What is deliberately NOT asserted, and why: the peers' own power state, because no sink
        # getter reports it - GetActiveSource's powerStatus describes the active-source record
        # only, and this flow may legitimately leave no active source - and OnImageViewOnMsg,
        # because it is a Thunder notification that this suite's one-shot curl transport cannot
        # observe. Asserting either would mean inventing an observation the transport cannot make.
        #
        # has_count above supplies the after-side type check and is reused rather than repeated
        # here, so the comparison is only ever reached with two integers in hand.
        count_did_not_fall = (
            has_count and isinstance(before_count, int) and after_count >= before_count
        )

        if has_success and has_count and count_did_not_fall:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID25_Standby_Coordination_Flow Passed ✅"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True

        # Which predicate broke is named before the payloads are dumped, so a failure is read
        # from the verdict rather than reconstructed from two JSON documents.
        log_warning(
            f"Checks - acknowledged: {has_success}  count is int: {has_count}  "
            f"count held or grew: {count_did_not_fall}"
        )
        log_warning(f"Before : {json.dumps(before_envelope, indent=2, sort_keys=True)}")
        log_warning(f"After  : {json.dumps(after_envelope, indent=2, sort_keys=True)}")
    except json.JSONDecodeError:
        # No return from this handler, by design: control falls through to the single failure tail
        # below, so the case reports one verdict from one place however it failed. The diagnostic
        # is logged here because a parse error and a payload mismatch are different faults and the
        # suite's own idiom - TCID15 and TCID02 both do this - is to name which one occurred.
        log_error("Invalid JSON response")

    log_error("TCID25_Standby_Coordination_Flow Failed ❌")
    return False
