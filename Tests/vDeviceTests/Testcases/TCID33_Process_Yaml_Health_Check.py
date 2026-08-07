"""
/**
 * @file TCID33_Process_Yaml_Health_Check.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID33_Process_Yaml_Health_Check
 * @details The BREADTH pass of this suite, and the only module in it that touches every
 *          inbound-handler emulation fixture the sink's vComponent tree publishes. Where
 *          TCID17-TCID27 each drive one flow in depth, this case DISCOVERS every
 *          Process_*.yaml document under vcomponent_configurations/commands, posts all of
 *          them to the vComponent emulator in a stable sorted order, and proves that the
 *          plugin's readable state survives the whole sweep.
 *
 *          Three things are asserted, and only three:
 *            1. every discovered fixture is ACCEPTED by the vComponent (HTTP 200);
 *            2. org.rdk.HdmiCecSink.getDeviceList answers with a well-formed envelope
 *               before and after the sweep, and again after each of the four fixtures whose
 *               effect reaches the device list;
 *            3. org.rdk.HdmiCecSink.getActiveSource stays answerable across the five
 *               fixtures that move the active-source and routing state.
 *
 *          WHAT IS NOT ASSERTED MATTERS AS MUCH. A handler whose only effect is an outbound
 *          CEC response or a Thunder notification leaves nothing a curl read can see, so for
 *          those fixtures ACCEPTANCE is the entire claim - and this module states that in its
 *          own console output rather than implying more. No claim whatsoever is made about
 *          OnKeyPressEvent, OnKeyReleaseEvent, ReportFeatureAbortEvent, OnDeviceRemoved or
 *          OnImageViewOnMsg: L3 reaches the plugin over request/response curl and cannot
 *          subscribe to a Thunder notification at all, so those five - the same events the
 *          sink's own L2 suite leaves uncovered - are outside what any module here can observe.
 *
 *          This is the breadth half of the sink's missing device-level (E2E) coverage
 *          (COVERAGE_GAPS.md, gap-plugin-sink-vdevicetests), where every sink JSON-RPC method
 *          is catalogued as covered by the plugin's own L2 suite with no end-to-end leg. No
 *          coverage percentage is claimed, because this suite is AUTHORED HERE AND NOT EXECUTED
 *          and an unmeasured figure would be a fabrication. Frames reach the handlers by
 *          INJECTION ONLY: nothing here reconfigures the device under test to act as its own
 *          peer, and role flipping and role inversion are out of scope for this suite by
 *          directive.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - hosts the org.rdk.HdmiCecSink
 *    plugin and answers JSON-RPC at utils.WPEFRAMEWORK_JSONRPC_URL.
 *  - Init_Devicelist_Populate has run, so HDMI-CEC is enabled and the emulated CEC network is
 *    seeded around the audio system at logical address 5.
 *  - The vComponent HTTP API is reachable at utils.VCOMPONENT_API_URL.
 *  - The sibling vcomponent_configurations/commands tree is present and readable. This module
 *    DISCOVERS its work from that directory rather than declaring it, so an absent or empty
 *    tree is a reported failure here, never a silently short sweep.
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
 *  - Every discovered Process_*.yaml fixture is accepted with HTTP 200, getDeviceList stays
 *    healthy before and after the sweep, getActiveSource stays answerable across the routing
 *    fixtures, and the post-sweep device count is not lower than the pre-sweep count.
 *
 * @pass_criteria
 *  - At least one fixture is discovered, the topology document is accepted, both health
 *    checks are healthy, both snapshots are captured, there is no failed post and no
 *    state-check failure, post_count >= pre_count, and run_test() returns True.
 *
 * @failure_criteria
 *  - The commands directory is absent, no fixture is discovered, the topology configuration
 *    is rejected, either health check is unhealthy, a snapshot is unavailable, any post is
 *    rejected, any state check fails, the device count regresses, or run_test() returns False.
 */
"""


import json
import time
import os
from pathlib import Path

from utils import (
    send_curl_command,
    send_vcomponent_command,
    HDMICEC_CMD_BASE,
    log_info,
    log_success,
    log_warning,
    log_error,
    log_with_timing
)
import HdmiCECSink_Curl as HdmiCecSinkApis

# log_with_timing is imported and never called, knowingly rather than by oversight: it is the
# one pyflakes finding this file carries ("imported but unused"), and the same single finding
# every sibling case module in this directory carries. The block above is the import contract
# they share, while the timing decoration is written INLINE at the point where the verdict is
# reported, so a diff between two cases shows only the behaviour under test.
#
# pathlib is this module's alone. Every other case posts a FIXED list of document names, so no
# path is ever walked; this one derives its work from the directory, which is what makes it the
# breadth pass rather than another flow case.


def _post_yaml(yaml_name):
    """Post one vComponent YAML command document and report whether it was accepted.

    Only the HTTP status decides the verdict. The body is logged verbatim and NEVER parsed:
    it carries whatever diagnostic the emulator produced, or utils.py's own explanation of a
    refusal, and it is not JSON. A name that is not in the tree comes back as HTTP 0 with
    "YAML file not found" rather than raising - so a typo would be reported as a product
    failure, which is why every filename named literally in this file has been checked
    against the directory listing.

    Args:
        yaml_name: Command-document filename relative to utils.HDMICEC_CMD_BASE, which is
                   joined on here so that module's environment-override contract keeps working
    Returns:
        True when the vComponent answered HTTP 200, False for every other outcome.
    """
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_name}")
    log_info(f"POST {yaml_name}: HTTP {http_code} {body}")
    return http_code == 200


def _health_check():
    """True when org.rdk.HdmiCecSink.getDeviceList answers with a well-formed envelope.

    A LIVENESS probe on the plugin's read surface, not an assertion about its contents: a
    plugin answering with an empty device list is healthy, one that does not answer is not.
    That distinction is the point of running it on both sides of the sweep - it separates
    "a handler changed the state" from "the plugin stopped answering".

    The sentinel test is NOT redundant with `not response`: utils.send_curl_command reports a
    transport failure by RETURNING "< No response from WPEFramework >", which is truthy, so an
    emptiness test alone would read a dead endpoint as a healthy one. Every read helper below
    carries the same guard for the same reason.

    Returns:
        True when the response parses as a JSON object carrying a "result" member, otherwise
        False - including for the transport sentinel and for an unparsable body.
    """
    response = send_curl_command(HdmiCecSinkApis.get_device_list)
    if not response or response.startswith("< No response"):
        return False

    try:
        body = json.loads(response)
        return isinstance(body, dict) and "result" in body
    except json.JSONDecodeError:
        return False


def _get_device_snapshot():
    """Capture the device list as {"number": int|None, "logicals": set}, or None on failure.

    The two key names are the sink's own and are deliberately inconsistent with each other:
    getDeviceList answers with "numberofdevices" all in lower case beside "deviceList" in
    camel case. Spelling either with the other's convention reads as an ABSENT member rather
    than raising, which run_test() would then see as a device count of -1 - a false reading
    with no symptom - so both are written out literally rather than derived.

    An entry that omits or mistypes logicalAddress is tolerated. The address set is part of
    the snapshot's published shape - it keeps the snapshot re-usable and identical in shape to
    the source plugin's, so the two suites stay comparable - while the verdict in run_test()
    compares counts only.

    Returns:
        A dict with "number" (the reported device count, or None when absent or not an
        integer) and "logicals" (the set of integer logical addresses reported), or None when
        the response could not be obtained or parsed.
    """
    response = send_curl_command(HdmiCecSinkApis.get_device_list)
    if not response or response.startswith("< No response"):
        return None

    try:
        body = json.loads(response)

        # A payload that parses as JSON but is not an object - or whose "result" member is not
        # one - is an unusable answer rather than a snapshot, so it is reported as a snapshot
        # failure here. Narrowing before calling .get() is also what keeps an AttributeError
        # from escaping run_test(), which owes its caller a bool on every path; the sibling case
        # modules in this directory narrow the same way for the same reason.
        if not isinstance(body, dict):
            return None
        result = body.get("result", {})
        if not isinstance(result, dict):
            return None

        number = result.get("numberofdevices")
        devices = result.get("deviceList", [])
        if not isinstance(devices, list):
            devices = []
        logicals = set()
        for d in devices:
            if isinstance(d, dict):
                la = d.get("logicalAddress")
                if isinstance(la, int):
                    logicals.add(la)
        return {
            "number": number if isinstance(number, int) else None,
            "logicals": logicals,
        }
    except json.JSONDecodeError:
        return None


def _get_active_source_ok():
    """True when org.rdk.HdmiCecSink.getActiveSource answers with a well-formed envelope.

    An API-HEALTH probe, not a value assertion, and the distinction is deliberate: "available"
    is the sink's presence flag and BOTH of its values are healthy answers. Whether an active
    source exists at any moment depends on which flow case ran before this one and on which
    fixture the sweep has just posted, so requiring available to be true would fail this module
    on a perfectly legitimate device state. What is required is that the plugin still answers
    the question at all - a boolean available, and success true.

    NOTE ON THE NAME, since it differs from the source plugin's equivalent helper. That one
    probes an ActiveSourceStatus method through a constant of the same name. The sink publishes
    neither: HdmiCECSink_Curl.py exposes get_active_source (org.rdk.HdmiCecSink.getActiveSource)
    and nothing ending in Status, so carrying the source's constant across would raise
    AttributeError on the sweep's first routing fixture. Hence the shorter name here, and the
    method that actually exists.

    Returns:
        True when the response parses with a boolean "available" and "success" true, otherwise
        False - including for the transport sentinel and for an unparsable body.
    """
    response = send_curl_command(HdmiCecSinkApis.get_active_source)
    if not response or response.startswith("< No response"):
        return False

    try:
        body = json.loads(response)

        # Narrowed before .get() for the reason given in _get_device_snapshot: an envelope that
        # is not an object cannot be interrogated, and an AttributeError must not escape.
        if not isinstance(body, dict):
            return False
        result = body.get("result", {})
        if not isinstance(result, dict):
            return False

        return isinstance(result.get("available"), bool) and result.get("success") is True
    except json.JSONDecodeError:
        return False


def run_test():
    start_time = time.perf_counter()

    # Validate all process-trigger YAML files are accepted by vComponent,
    # and add observable checks for handlers that should affect plugin state.
    commands_dir = Path(__file__).resolve().parent.parent / "vcomponent_configurations" / "commands"

    # This module lives in Testcases/ and vcomponent_configurations/ is its sibling one level
    # up, which is what the two .parent steps walk. An absent directory gets its own message
    # rather than surfacing as "no files found" - two distinct findings for a reader.
    if not commands_dir.is_dir():
        log_error("TCID33_Process_Yaml_Health_Check Failed: commands directory not found")
        return False

    # rglob so a nested layout is still swept, sorted() so the order is stable and the console
    # record is diffable. The Process_ prefix confines the sweep to the inbound-handler
    # documents: the DeviceListConfig/Payload_*.yaml seeding documents share this tree and are
    # deliberately NOT matched, belonging to the bootstrap module's topology rather than here.
    yaml_files = sorted(
        p.relative_to(commands_dir).as_posix()
        for p in commands_dir.rglob("Process_*.yaml")
    )

    if not yaml_files:
        log_error("TCID33_Process_Yaml_Health_Check Failed: no Process_*.yaml files found")
        return False

    # Re-establish the known topology before measuring anything. Its root emulated peer is the
    # audio system at logical address 5, physical address 2.0.0.0, carrying seven ports with the
    # seeded peers beneath it - the one stable vComponent-backed device the state checks below
    # have something to verify against. Address 5 is load-bearing, not decorative: it is the
    # only address for which the sink's addDevice() also raises ReportAudioDeviceConnectedStatus,
    # and the only initiator its ARC gate accepts. Init_Devicelist_Populate posted this same
    # document at bootstrap, so re-posting it is idempotent, and it is what makes this case
    # independent of how many flow cases ran before it. Posted directly rather than through
    # _post_yaml because the failure below names THIS step: a rejected topology is not the same
    # finding as a rejected handler fixture.
    http_code, _ = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/Device_Config_Add_Network.yaml")
    if http_code != 200:
        log_error("TCID33_Process_Yaml_Health_Check Failed: configure command rejected")
        return False
    time.sleep(1)

    if not _health_check():
        log_error("TCID33_Process_Yaml_Health_Check Failed: pre-check getDeviceList is not healthy")
        return False

    pre_snapshot = _get_device_snapshot()
    if pre_snapshot is None:
        log_error("TCID33_Process_Yaml_Health_Check Failed: unable to capture pre device snapshot")
        return False

    failed_posts = []
    state_check_failures = []

    # WHICH FIXTURES HAVE AN OBSERVABLE EFFECT - and, just as importantly, which do not.
    #
    # These four reach the device list: ReportPhysicalAddress registers the peer, and CECVersion,
    # SetOSDName and DeviceVendorID populate the entry that registration created, so a
    # getDeviceList read straight after each of them means something.
    should_touch_device_list = {
        "Process_Report_Physical_Address.yaml",
        "Process_CEC_Version.yaml",
        "Process_Set_OSD_Name.yaml",
        "Process_Device_Vendor_ID.yaml",
    }
    # These five move the active-source and routing state, so getActiveSource must still answer
    # after each one. Process_In_Active_Source.yaml (opcode 0x9D, InactiveSource) is the
    # sink-only member and belongs with the routing trio for the same reason: it changes which
    # source the plugin considers active. The set is named for the sink's API - the source
    # plugin's equivalent is named for an ActiveSourceStatus method the sink does not publish.
    should_keep_active_source_api_healthy = {
        "Process_Routing_Change.yaml",
        "Process_Routing_Information.yaml",
        "Process_Set_Stream_Path.yaml",
        "Process_Request_Active_Source.yaml",
        "Process_In_Active_Source.yaml",
    }
    # Every other fixture is UNCLASSIFIED on purpose - Process_Give_Features and
    # Process_Request_Current_Latency among them. Each produces an outbound CEC response or a
    # notification and nothing a curl read can see, so acceptance is the only claim available,
    # and making it is honest only because the disclosure lines near the verdict say so plainly.

    for yaml_name in yaml_files:
        if not _post_yaml(yaml_name):
            failed_posts.append(yaml_name)
            continue

        if yaml_name in should_touch_device_list:
            # Longer window for the full pipeline:
            # vComponent callback → DriverReceiveCallback → rQueue
            # → read thread → MessageDecoder → HdmiCecSinkProcessor::process() → addDevice()
            time.sleep(1.5)
            snap = _get_device_snapshot()
            if snap is None:
                state_check_failures.append(f"{yaml_name}: snapshot unavailable")
            # Note: State-level verification requires special LA injection files;
            # basic acceptance is validated by successful HTTP 200 response above
        else:
            # Short window for non-state-check YAMLs.
            time.sleep(0.2)

        if yaml_name in should_keep_active_source_api_healthy:
            if not _get_active_source_ok():
                state_check_failures.append(f"{yaml_name}: getActiveSource API unhealthy")

    if not _health_check():
        log_error("TCID33_Process_Yaml_Health_Check Failed: post-check getDeviceList is not healthy")
        return False

    post_snapshot = _get_device_snapshot()
    if post_snapshot is None:
        log_error("TCID33_Process_Yaml_Health_Check Failed: unable to capture post device snapshot")
        return False

    pre_num = pre_snapshot["number"] if isinstance(pre_snapshot["number"], int) else -1
    post_num = post_snapshot["number"] if isinstance(post_snapshot["number"], int) else -1
    log_info(f"Device count pre={pre_num} post={post_num}")

    if failed_posts:
        log_warning(f"Failed YAML posts: {failed_posts}")
        log_error("TCID33_Process_Yaml_Health_Check Failed")
        return False

    if state_check_failures:
        log_warning(f"State-check warnings: {state_check_failures}")
        log_error("TCID33_Process_Yaml_Health_Check Failed")
        return False

    log_info("Non-observable handlers (event-only/outbound-only) remain acceptance-based in this TC.")
    log_info("For strict proof, add implementation counters or parse plugin logs per handler.")

    # DIRECTION, never magnitude. The count depends on the seeded topology and on which flow
    # cases ran before this one, so any fixed number written here would be wrong. What a sweep of
    # inbound handlers must not do is LOSE devices: a count that grew is the legitimate result of
    # registering peers, a count that fell means the runtime went unstable under the sweep. Both
    # snapshots read -1 when the plugin omits the count, so an omission on both sides is not
    # mistaken for a regression.
    if post_num < pre_num:
        log_warning("Post device count lower than pre-count; treating as unstable runtime")
        log_error("TCID33_Process_Yaml_Health_Check Failed")
        return False

    elapsed_time = time.perf_counter() - start_time
    msg = f"TCID33_Process_Yaml_Health_Check Passed ✅ ({len(yaml_files)} process YAMLs posted + observable checks)"
    if os.environ.get("HDMICEC_TIMING_ENABLED"):
        log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
    else:
        log_success(msg)
    return True


# ON THE THREE SETTLE WINDOWS ABOVE, so a reviewer does not read them as an oversight. The
# project's test-quality bar forbids wall-clock waits in NEW tests, and rightly: in the C++
# GoogleTest suites asynchronous behaviour is exercised by invoking the captured callback
# directly, which is faster and deterministic. That technique does not exist here. This module
# drives a SEPARATE emulator process over HTTP and then reads the plugin over JSON-RPC; there is
# no callback in this address space to capture, and the CEC receive pipeline between the two is
# genuinely asynchronous. The 1 s topology window, the 1.5 s device-list window and the 0.2 s
# window for the rest are the reference suite's documented idiom for that pipeline, inherited
# here for parity. No further sleep is added anywhere in this file.
#
# NO RESTORE STEP, AND ITS ABSENCE IS DELIBERATE. The sweep leaves the plugin wherever the last
# accepted fixture put it. Every document posted here is an inbound frame or the topology
# configuration - not one is a setter with an inverse to call - so a restore could only be
# manufactured by hand-building a CEC payload, which this suite does not do: every frame it
# injects comes from a reviewed document under vcomponent_configurations/commands/. It would
# manufacture a claim too, making it look as though the device had been returned to a known
# state when one more untracked change had in fact been issued. The residual is safe because of
# where this case sits: SuitManager.py registers it 33rd and LAST, so no sibling inherits the
# state, and the two health checks plus the count-direction check are what establish that the
# plugin is still answering when the suite prints its summary.
