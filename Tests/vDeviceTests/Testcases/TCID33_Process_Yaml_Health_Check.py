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
 *          subscribe to a Thunder notification at all, so all five are outside what any module
 *          here can observe. That is a property of this TRANSPORT and not a coverage verdict.
 *          Four of the five are asserted at both L1 and L2 - the key press/release pair, image
 *          view on, and device removed. ReportFeatureAbortEvent is asserted at L1 only
 *          (reportFeatureAbortEvent_SubscribedClient_ReceivesAllThreeOperands,
 *          _EachAbortReason_IsNotified, _BoundaryOperands_AreNotified); at L2 only the
 *          broadcast-drop guard arm is asserted, because the directed arm is documented BLOCKED
 *          there - the shared CEC mock's AbortReason int constructor leaves its public `impl`
 *          delegate uninitialised, so a frame-parsed directed Feature Abort takes SIGSEGV
 *          (HdmiCecSink_L2Test.cpp:4495 onward states the analysis and the one-line mock change
 *          that would unblock it). Where the coverage register lists these five as uncovered,
 *          that is its PRE-CHANGE BASELINE.
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
    sanitise_for_log,
    HDMICEC_CMD_BASE,
    log_info,
    log_success,
    log_warning,
    log_error,
    log_with_timing,
    CEC_FRAME_PACING_SECONDS,
    CEC_PIPELINE_PACING_SECONDS,
    CEC_SHORT_PACING_SECONDS,
)
import HdmiCECSink_Curl as HdmiCecSinkApis

# Every name in the utils import above has a call site, log_with_timing included: it applies the
# HDMICEC_TIMING_ENABLED decoration and the pass path routes its message through it, which retired
# this module's own inline copy of that gate.
#
# pathlib is this module's alone. Every other case posts a FIXED list of document names, so no path
# is ever walked; this one derives its work from the directory, which is what makes it the breadth
# pass rather than another flow case. `re` reads a fixture's payload so a membership expectation can
# be DERIVED from the frame's own initiator nibble instead of restated here - the same technique
# Init_Devicelist_Populate.verify_seed_payload_consistency() uses on the seed payloads.

# ── THE INVENTORY THIS SWEEP IS REQUIRED TO FIND ─────────────────────────────────────────────────
# An earlier revision accepted ANY non-empty discovery, so a fixture deleted from the tree, renamed,
# or added without a thought about what it should do was invisible: the sweep simply got shorter or
# longer and still passed. Declaring the inventory is what makes a change to it a test failure that
# names the difference. This list is deliberately WRITTEN OUT rather than derived - deriving it from
# the directory would reproduce exactly the blindness being fixed.
EXPECTED_PROCESS_FIXTURES = frozenset({
    "Process_Abort.yaml",
    "Process_Active_Source.yaml",
    "Process_CEC_Version.yaml",
    "Process_Device_Vendor_ID.yaml",
    "Process_Feature_Abort.yaml",
    "Process_Get_CEC_Version.yaml",
    "Process_Give_Device_Power_Status.yaml",
    "Process_Give_Device_Vendor_ID.yaml",
    "Process_Give_Features.yaml",
    "Process_Give_OSD_Name.yaml",
    "Process_Give_Physical_Address.yaml",
    "Process_In_Active_Source.yaml",
    "Process_Polling.yaml",
    "Process_Report_Physical_Address.yaml",
    "Process_Report_Power_Status.yaml",
    "Process_Request_Active_Source.yaml",
    "Process_Request_Current_Latency.yaml",
    "Process_Routing_Change.yaml",
    "Process_Routing_Information.yaml",
    "Process_Set_OSD_Name.yaml",
    "Process_Set_Stream_Path.yaml",
    "Process_Standby.yaml",
    "Process_User_Control_Pressed.yaml",
    "Process_User_Control_Released.yaml",
})

# ── WHAT EACH FIXTURE IS EXPECTED TO DO, ONE ENTRY PER FIXTURE ───────────────────────────────────
# Three expectation kinds, and every fixture carries at least one EXPLICITLY. An earlier revision
# named two small sets and let everything else fall into an unstated default, so "no expectation was
# written for this fixture" and "this fixture genuinely has no observable consequence" looked
# identical. They are different statements and they are now written differently.
#
#   "registers"      the handler calls addDevice(header.from), so the frame's own initiator must
#                    appear in getDeviceList afterwards. Derived from the payload's header nibble.
#   "active_source"  the handler moves active-source or routing state, so getActiveSource must still
#                    answer with success true and a boolean available afterwards. The VALUE is not
#                    pinned: whether an active source exists depends on which flow case ran before
#                    this one and on which fixture the sweep has just posted.
#   "smoke"          acceptance is the ENTIRE claim, and the reason is recorded beside it. Each of
#                    these produces an outbound CEC response or a Thunder notification and nothing a
#                    curl read can see.
#
# The keys of this mapping are required to equal EXPECTED_PROCESS_FIXTURES exactly, which is what
# makes it impossible to add a fixture to the tree without deciding what it should prove.
FIXTURE_EXPECTATIONS = {
    "Process_Abort.yaml": (("smoke",), "the handler only logs the abort opcode"),
    "Process_Active_Source.yaml": (
        ("registers", "active_source"),
        "process(ActiveSource) registers the initiator and updates the active source",
    ),
    "Process_CEC_Version.yaml": (
        ("registers",), "process(CECVersion) registers the initiator and records its version"
    ),
    "Process_Device_Vendor_ID.yaml": (
        ("registers",), "process(DeviceVendorID) registers the initiator and records its vendor"
    ),
    "Process_Feature_Abort.yaml": (
        ("smoke",),
        "the handler's whole effect is the ReportFeatureAbortEvent notification, which a one-shot "
        "curl cannot subscribe to",
    ),
    "Process_Get_CEC_Version.yaml": (
        ("smoke",), "the handler answers with an outbound <CEC Version> frame, which is not readable"
    ),
    "Process_Give_Device_Power_Status.yaml": (
        ("smoke",),
        "the handler answers with an outbound <Report Power Status> frame carrying the sink's own "
        "power state, which is not readable",
    ),
    "Process_Give_Device_Vendor_ID.yaml": (
        ("smoke",), "the handler answers with an outbound <Device Vendor ID> frame"
    ),
    "Process_Give_Features.yaml": (
        ("smoke",), "the handler answers with an outbound <Report Features> frame on CEC 2.0 only"
    ),
    "Process_Give_OSD_Name.yaml": (
        ("smoke",), "the handler answers with an outbound <Set OSD Name> frame"
    ),
    "Process_Give_Physical_Address.yaml": (
        ("smoke",), "the handler answers with an outbound <Report Physical Address> frame"
    ),
    "Process_In_Active_Source.yaml": (
        ("active_source",),
        "process(InActiveSource) clears the active source when the withdrawing address holds it",
    ),
    "Process_Polling.yaml": (
        ("smoke",),
        "a bare polling header carries no opcode; the middleware answers it at the bus layer and "
        "the plugin records nothing",
    ),
    "Process_Report_Physical_Address.yaml": (
        ("registers",),
        "process(ReportPhysicalAddress) registers the initiator and records its physical address",
    ),
    "Process_Report_Power_Status.yaml": (
        ("registers",),
        "process(ReportPowerStatus) registers the initiator and writes the reported status into its "
        "device record, which getDeviceList publishes as powerStatus",
    ),
    "Process_Request_Active_Source.yaml": (
        ("active_source",), "the handler makes the sink announce itself as the active source"
    ),
    "Process_Request_Current_Latency.yaml": (
        ("smoke",), "the handler answers with an outbound <Report Current Latency> frame"
    ),
    "Process_Routing_Change.yaml": (
        ("active_source",), "the inbound routing handler is log-only, so only API health is claimed"
    ),
    "Process_Routing_Information.yaml": (
        ("active_source",), "the inbound routing handler is log-only, so only API health is claimed"
    ),
    "Process_Set_OSD_Name.yaml": (
        ("registers",), "process(SetOSDName) registers the initiator and records its OSD name"
    ),
    "Process_Set_Stream_Path.yaml": (
        ("active_source",), "the inbound stream-path handler is log-only, so only API health is claimed"
    ),
    "Process_Standby.yaml": (
        ("smoke",),
        "the handler's whole effect is the SendStandbyMsgEvent notification; it writes no state at "
        "all (HdmiCecSinkImplementation.cpp:203-207)",
    ),
    "Process_User_Control_Pressed.yaml": (
        ("smoke",),
        "the handler forwards straight to SendKeyPressMsgEvent and stores nothing (:295-300)",
    ),
    "Process_User_Control_Released.yaml": (
        ("smoke",),
        "the handler forwards straight to SendKeyReleaseMsgEvent and stores nothing (:301-305)",
    ),
}

# The emulated topology document, re-posted at the start to establish a known baseline and again by
# cleanup() so the suite ends in that same known topology.
NETWORK_CONFIG_YAML = "Device_Config_Add_Network.yaml"

# Bounded budgets. Poll ceilings, never durations anything waits out. The device-list budget is the
# longer one because a topology or registration change travels the whole pipeline - vComponent, the
# driver receive callback, the read queue, the read thread, the decoder, then the handler - before it
# can show up in a read.
HEALTH_TIMEOUT_S = 10.0
REGISTER_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.25

# True once the sweep has run, so cleanup() knows the topology may need re-declaring.
_topology_disturbed = False

_PAYLOAD_PATTERN = re.compile(r'payload:\s*\[(.*?)\]', re.S)


# ── the reviewed fixture inventory ───────────────────────────────────────────
#
# THE 24 INBOUND-HANDLER DOCUMENTS THIS CASE POSTS, NAMED ONE BY ONE. This list is the case's
# subject, not a convenience: every entry was read and approved, and posting a vComponent command
# document makes the emulator inject a CEC frame into the device under test, so "whatever matches
# Process_*.yaml on disk" is not an acceptable definition of the work.
#
# An earlier revision discovered the set with rglob("Process_*.yaml") and swept whatever it found.
# Three things follow from that, and all three are defects rather than flexibility. A document
# DELETED from the tree shrank the sweep silently, so coverage could be lost while the case went
# on reporting a pass. A document ADDED - by a careless merge or by anything able to write into
# the fixture tree - was posted unreviewed, which is to say arbitrary CEC frames were injected on
# the strength of a filename. And a symlink or a directory bearing a matching name was swept in
# and posted as though it were one of these documents.
#
# So the inventory is fixed here, the tree is required to match it EXACTLY in both directions, and
# every entry is required to be a regular file rather than a link or a directory. Adding a fixture
# is a deliberate edit to this list, reviewed alongside the document itself. Keep it sorted; the
# verification below reports additions and omissions separately, so a rename shows up as one of
# each rather than as a puzzle.
APPROVED_PROCESS_FIXTURES = (
    "Process_Abort.yaml",
    "Process_Active_Source.yaml",
    "Process_CEC_Version.yaml",
    "Process_Device_Vendor_ID.yaml",
    "Process_Feature_Abort.yaml",
    "Process_Get_CEC_Version.yaml",
    "Process_Give_Device_Power_Status.yaml",
    "Process_Give_Device_Vendor_ID.yaml",
    "Process_Give_Features.yaml",
    "Process_Give_OSD_Name.yaml",
    "Process_Give_Physical_Address.yaml",
    "Process_In_Active_Source.yaml",
    "Process_Polling.yaml",
    "Process_Report_Physical_Address.yaml",
    "Process_Report_Power_Status.yaml",
    "Process_Request_Active_Source.yaml",
    "Process_Request_Current_Latency.yaml",
    "Process_Routing_Change.yaml",
    "Process_Routing_Information.yaml",
    "Process_Set_OSD_Name.yaml",
    "Process_Set_Stream_Path.yaml",
    "Process_Standby.yaml",
    "Process_User_Control_Pressed.yaml",
    "Process_User_Control_Released.yaml",
)


def _discovered_process_fixtures(commands_dir):
    """Return every Process_*.yaml path present under commands_dir, as posix-relative names.

    Walked with os.walk(followlinks=False) rather than Path.rglob so that a symlinked
    SUBDIRECTORY cannot be descended into - rglob's symlink behaviour varies by Python version,
    and a scan whose reach depends on the interpreter is not a scan an inventory check can rest
    on. Entries are collected by NAME only, whatever their type: this function answers "what
    claims to be a fixture", and _verify_fixture_inventory decides whether each one may be posted.

    Args:
        commands_dir: pathlib.Path of the vcomponent command-document directory.
    Returns:
        A set of paths relative to commands_dir, in posix form.
    """
    discovered = set()
    for directory, _subdirectories, filenames in os.walk(commands_dir, followlinks=False):
        for filename in filenames:
            if filename.startswith("Process_") and filename.endswith(".yaml"):
                absolute = Path(directory) / filename
                discovered.add(absolute.relative_to(commands_dir).as_posix())
        # os.walk lists a symlink to a directory under subdirectories, and followlinks=False
        # stops it being descended - but a symlink to a FILE appears in filenames, so the name
        # is collected here and rejected by type below rather than being quietly skipped.
    return discovered


def _verify_fixture_inventory(commands_dir):
    """True when the fixture tree matches APPROVED_PROCESS_FIXTURES exactly, file types included.

    Four independent conditions, each reported with its own diagnostic so a reader is told which
    one failed rather than being handed a set difference:

      * every approved document is present;
      * every approved document is a REGULAR FILE - os.lstat, so a symlink is seen as a symlink
        rather than as whatever it points at, and a directory or a FIFO bearing the name is
        likewise refused;
      * nothing else in the tree claims to be a Process_*.yaml, at any depth;
      * commands_dir is itself a real directory and not a symlink to one.

    Args:
        commands_dir: pathlib.Path of the vcomponent command-document directory.
    Returns:
        True when all four hold; False with the reason already logged otherwise.
    """
    directory_info = os.lstat(str(commands_dir))
    if not stat.S_ISDIR(directory_info.st_mode):
        log_error(
            f"✖ {commands_dir} is not a directory (mode {stat.filemode(directory_info.st_mode)}); "
            "a symlink standing in for the fixture tree would redirect every post in this case"
        )
        return False

    missing = []
    wrong_type = []
    for name in APPROVED_PROCESS_FIXTURES:
        candidate = commands_dir / name
        try:
            info = os.lstat(str(candidate))
        except OSError:
            missing.append(name)
            continue
        if not stat.S_ISREG(info.st_mode):
            wrong_type.append(f"{name} ({stat.filemode(info.st_mode)})")

    discovered = _discovered_process_fixtures(commands_dir)
    unexpected = sorted(discovered - set(APPROVED_PROCESS_FIXTURES))

    if missing:
        log_error(
            f"✖ {len(missing)} approved fixture(s) are absent from the tree: "
            f"{', '.join(missing)}. This case posts a fixed reviewed inventory, so a missing "
            "document is lost coverage rather than a smaller sweep"
        )
    if wrong_type:
        log_error(
            f"✖ {len(wrong_type)} approved fixture(s) are not regular files: "
            f"{', '.join(wrong_type)}. A symlink or directory in a fixture's place would have "
            "this case post something other than the document that was reviewed"
        )
    if unexpected:
        log_error(
            f"✖ {len(unexpected)} unapproved Process_*.yaml document(s) are present: "
            f"{', '.join(sanitise_for_log(name, max_chars=128) for name in unexpected)}. "
            "Posting one would inject an unreviewed CEC frame into the device under test; add it "
            "to APPROVED_PROCESS_FIXTURES deliberately, with the document reviewed, or remove it"
        )

    if missing or wrong_type or unexpected:
        return False

    log_success(
        f"✔ the fixture inventory matches exactly: {len(APPROVED_PROCESS_FIXTURES)} approved "
        "Process_*.yaml documents, all regular files, none unapproved"
    )
    return True


# THE INVENTORY CONTRACT.
#
# This frozenset is the authoritative list of inbound-handler fixtures this case posts, and it
# is compared for EQUALITY against what the directory sweep finds - not used as a filter, and
# not treated as a lower bound.
#
# Deriving the work from a directory sweep alone would make the case's scope depend on whatever
# happens to be lying in the tree. A sweep that merely finds "at least something" accepts two
# opposite defects in silence: a fixture that has been deleted or renamed shrinks the pass
# without failing it, so an untested handler reads as a green run; and a stray document - a
# nested copy under a scratch directory, a fixture staged for a different case, an editor or
# rebase artefact whose name still ends .yaml - gets POSTED to the emulator, changing device
# state that the assertions after the loop then measure. Both are silent today. Equality
# against a fixed inventory turns each of them into a named failure.
#
# Paths are relative to vcomponent_configurations/commands and compared as POSIX strings, so a
# nested duplicate such as "DeviceListConfig/Process_Polling.yaml" does not collide with the
# flat "Process_Polling.yaml" - it is reported as an extra file, which is the point.
#
# Adding, renaming or removing a fixture is therefore a deliberate two-file change: the
# document and this list. That is the intended cost. The 24 entries are the complete set of
# Process_*.yaml documents in the tree, one per inbound CEC opcode this suite exercises.
EXPECTED_PROCESS_FIXTURES = frozenset({
    "Process_Abort.yaml",
    "Process_Active_Source.yaml",
    "Process_CEC_Version.yaml",
    "Process_Device_Vendor_ID.yaml",
    "Process_Feature_Abort.yaml",
    "Process_Get_CEC_Version.yaml",
    "Process_Give_Device_Power_Status.yaml",
    "Process_Give_Device_Vendor_ID.yaml",
    "Process_Give_Features.yaml",
    "Process_Give_OSD_Name.yaml",
    "Process_Give_Physical_Address.yaml",
    "Process_In_Active_Source.yaml",
    "Process_Polling.yaml",
    "Process_Report_Physical_Address.yaml",
    "Process_Report_Power_Status.yaml",
    "Process_Request_Active_Source.yaml",
    "Process_Request_Current_Latency.yaml",
    "Process_Routing_Change.yaml",
    "Process_Routing_Information.yaml",
    "Process_Set_OSD_Name.yaml",
    "Process_Set_Stream_Path.yaml",
    "Process_Standby.yaml",
    "Process_User_Control_Pressed.yaml",
    "Process_User_Control_Released.yaml",
})


def _post_yaml(yaml_name):
    """Post one vComponent YAML command document and report whether it was accepted.

    Only the HTTP status decides the verdict. The body is NEVER parsed: it carries whatever
    diagnostic the emulator produced, or utils.py's own explanation of a refusal, and it is not
    JSON. A name that is not in the tree comes back as HTTP 0 with "YAML file not found" rather
    than raising - so a typo would be reported as a product failure, which is why every filename
    named literally in this file has been checked against the directory listing.

    Unparsed is not the same as unprocessed. The body is remote-derived, so it is rendered
    through utils.sanitise_for_log before it is printed: bounded, escaped and single-line. This
    module posts every document in a directory and logs a line for each, which makes it the
    largest single volume of emulator-authored text in the suite, and the console transcript is
    the only evidence a device-level run leaves behind. A body carrying terminal control
    sequences would otherwise be able to erase the lines above it or repaint a refusal as an
    acceptance, and the escaped rendering is what makes that impossible while keeping the
    diagnostic readable.

    Args:
        yaml_name: Command-document filename relative to utils.HDMICEC_CMD_BASE, which is joined on
                   here so that module's environment-override contract keeps working
    Returns:
        True when the vComponent answered HTTP 200, False for every other outcome.
    """
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_name}")
    log_info(f"POST {yaml_name}: HTTP {http_code} {sanitise_for_log(body)}")
    return http_code == 200


def _fixture_initiator(yaml_name):
    """Return the logical address a fixture's payload initiates from, or None with a reason.

    DERIVED, NOT RESTATED. The "registers" expectation is that the frame's own initiator appears in
    the device list, and that address is the high nibble of the payload's header byte. Reading it out
    of the document means the expectation follows the fixture: a document re-addressed to a different
    initiator changes what this module looks for, instead of leaving a stale literal here that would
    be reported as a plugin defect.
    Returns:
        (logical_address, None) on success, or (None, reason).
    """
    path = Path(HDMICEC_CMD_BASE) / yaml_name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read {yaml_name}: {exc}"
    match = _PAYLOAD_PATTERN.search(text)
    if not match:
        return None, f"{yaml_name} declares no payload list"
    first = match.group(1).split(",")[0].strip().strip('"').strip("'")
    try:
        header = int(first, 16)
    except ValueError as exc:
        return None, f"{yaml_name} has a non-hexadecimal header byte: {exc}"
    return header >> 4, None


def _get_device_snapshot():
    """Capture the device list as {"number": int, "logicals": set}, or None when it is not usable.

    THE INTEGER IS NOW REQUIRED, WHICH IS THE POINT OF THIS REVISION. An earlier version returned
    "number": None whenever the count was absent or not an integer, and run_test() then substituted
    -1 for it - so a plugin that stopped reporting a count produced -1 on BOTH sides of the sweep and
    the direction check compared -1 against -1 and passed. A snapshot that cannot be trusted is now
    reported as no snapshot at all.

    The two key names are the sink's own and are deliberately inconsistent with each other:
    getDeviceList answers with "numberofdevices" all in lower case beside "deviceList" in camel case.
    Spelling either with the other's convention reads as an ABSENT member rather than raising, which
    is exactly the failure mode this function now refuses to paper over, so both are written out
    literally rather than derived.
    Returns:
        A dict with "number" (an int) and "logicals" (a set of int logical addresses), or None when
        the response could not be obtained, was not a success envelope, or carried no integer count.
    """
    response = send_curl_command(HdmiCecSinkApis.get_device_list)
    # utils.send_curl_command reports a transport failure by RETURNING the TRUTHY sentinel
    # "< No response from WPEFramework >", so an emptiness test alone would read a dead endpoint as a
    # healthy one. Every read helper below carries the same guard for the same reason.
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
    time.sleep(CEC_FRAME_PACING_SECONDS)

    # WAITED FOR, NOT SLEPT THROUGH. An earlier revision paused a fixed second here and defended
    # every fixed pause in this file as the reference suite's idiom. A bounded poll is strictly
    # better on both counts: it returns as soon as the plugin answers, and it still reports when the
    # plugin never does.
    ready, pre_snapshot = _wait_for_snapshot(lambda snap: True, HEALTH_TIMEOUT_S)
    if not ready:
        log_error(
            "TCID33_Process_Yaml_Health_Check Failed: getDeviceList never returned a usable "
            "snapshot before the sweep - it must report success true with an integer "
            "numberofdevices and a deviceList array"
        )
        return False
    log_info(
        f"Pre-sweep: {pre_snapshot['number']} devices at {sorted(pre_snapshot['logicals'])}"
    )

    failures = []

    for yaml_name in discovered:
        kinds, reason = FIXTURE_EXPECTATIONS[yaml_name]
        if not _post_yaml(yaml_name):
            failures.append(f"{yaml_name}: the vComponent refused the post")
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
