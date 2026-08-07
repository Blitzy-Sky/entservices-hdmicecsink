"""
/**
 * @file Init_Devicelist_Populate.py
 * @brief Suite initialization for the HDMI-CEC Sink device-level vDevice suite: activates the
 *        sink plugin, configures the emulated CEC network of source-role peers beneath the
 *        sink television, seeds those peers into the middleware's device list, and verifies
 *        getDeviceList reports them.
 *
 * @testcase Init_Devicelist_Populate
 * @details SuitManager.py registers this module as the sink suite's initialization module
 *          (SUITE_INIT_MODULES = {"hdmicecsink": "Init_Devicelist_Populate"}) and calls its
 *          run_test() once, before the first test case. A False return aborts the whole
 *          suite, which makes this the highest-leverage module in the tree: every TCID*.py
 *          case that reads a peer, routes to one, or asserts against one depends on the
 *          device list left behind here.
 *
 *          TOPOLOGY ORIENTATION - the single thing most easily got wrong in this directory.
 *          The device under test is the SINK: the television itself, which owns CEC logical
 *          address 0. Every peer configured and seeded below is therefore a SOURCE-role
 *          device sitting beneath that television - an audio system, two playback devices, a
 *          tuner and a recording device. No virtual television is created here and no
 *          TV-rooted network is posted. That shape belongs to the source plugin's suite,
 *          whose device under test is a source and whose emulated root is consequently a TV.
 *
 *          This is a statement about the ROLE OF THE EMULATED PEERS, and nothing more. Each
 *          peer is a device the vComponent emulates; the device under test keeps its own sink
 *          role throughout and is never reconfigured to stand in for a peer of its own. This
 *          module contains no multi-device simulation by role flipping and no harness that
 *          reverses a device's role at run time.
 *
 *          The device list is populated the way the middleware really populates it, by
 *          injecting CEC frames from the peers and letting HdmiCecSinkProcessor::process()
 *          handle them - ReportPhysicalAddress and CECVersion reach addDevice(), SetOSDName
 *          fills in osdName, DeviceVendorID fills in vendorID - rather than by writing the
 *          list directly. Auto-discovery through the middleware's own poll thread is given
 *          the first 20 seconds and the frame injection is the fallback, so a build that
 *          discovers its peers unaided is never bypassed.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is reachable and hosts the
 *    org.rdk.HdmiCecSink plugin, answering JSON-RPC at utils.WPEFRAMEWORK_JSONRPC_URL.
 *  - The vComponent HTTP API is serving utils.VCOMPONENT_API_URL, and the YAML command
 *    documents posted below are readable under utils.HDMICEC_CMD_BASE.
 *  - The emulated peers enumerated in PEER_SEEDS exist in the vComponent device map, which
 *    Device_Config_Add_Network.yaml establishes as this module's first configuration action.
 *  - This suite is AUTHORED, NOT EXECUTED in this repository. No continuous integration
 *    workflow runs it, none of the prerequisites above is present in a build environment,
 *    and nothing described here has been observed against a live device or emulator.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml
 *
 * @expected_result
 *  - getDeviceList reports success, a device count at or above the minimum in force, and EVERY
 *    peer in PEER_SEEDS present, carrying its OSD name and vendor identifier. HDMI-CEC is left
 *    ENABLED, and that state was established by this run and read back as such, which is the
 *    post-condition every following test case is written against.
 *
 * @pass_criteria
 *  - run_test() returns True after both environment gates resolved to documented values,
 *    setEnabled(true) reported success and getEnabled read back true, every peer in PEER_SEEDS
 *    was observed in getDeviceList, and every detail the active mode enforces has verified.
 *
 * @failure_criteria
 *  - An environment gate carries a value that is not a documented spelling or is out of range;
 *    plugin activation fails; a vComponent command is rejected; setEnabled(true) does not
 *    report success even after the disable/enable toggle; getEnabled does not read back true;
 *    the middleware never learns the bootstrap peer or any secondary peer; getDeviceList is
 *    unreachable or unparsable; a peer is absent from the final snapshot; or, in strict mode, a
 *    peer reports the wrong OSD name or an empty vendor identifier.
 *
 *    PRESENCE of every seeded peer and the ENABLED post-condition are enforced in both modes.
 *    The modes differ only in how strictly the per-device details and the reported count are
 *    read, and neither gate can weaken either of those two guarantees.
 */
"""

import json
import re
import os
import time

from utils import (
    await_plugin_ready,
    send_curl_command,
    send_vcomponent_command,
    HDMICEC_CMD_BASE,
    activate_plugin,
    sanitise_for_log,
    WPEFRAMEWORK_JSONRPC_URL,
    log_info,
    log_success,
    log_warning,
    log_error,
    log_with_timing,
    NO_RESPONSE_SENTINEL,
)
import HdmiCECSink_Curl as HdmiCecSinkApis

# The prefix utils.send_curl_command returns when the transport never answered. Derived from the
# sentinel utils publishes rather than spelled out again here, so the two cannot drift apart -
# the previous copy of this test compared against a hand-written "< No response" literal.
NO_RESPONSE_PREFIX = NO_RESPONSE_SENTINEL.split(" from ")[0]


# ── configuration ────────────────────────────────────────────────────────────

# Callsign of the device under test. The sink plugin is the television; it is not one of the
# peers below.
SINK_CALLSIGN = "org.rdk.HdmiCecSink"

# Bootstrap peer: the audio system at CEC logical address 5.
#
# Two independent reasons fix this choice, and neither is arbitrary. The vComponent offers an
# audio system exactly one logical address - LOGICAL_ADDRESS_AUDIOSYSTEM == 5 - so an emulated
# audio system is always reachable there and nowhere else. And address 5 is the only address
# for which the sink's HdmiCecSinkImplementation::addDevice() additionally raises
# ReportAudioDeviceConnectedStatus, which is what makes the ARC, System Audio Mode and
# short-audio-descriptor flows observable at all. Discovery is therefore declared ready on
# address 5 rather than on whichever peer happens to answer first.
BOOTSTRAP_LOGICAL_ADDRESS = 5

# The audio system's ReportPhysicalAddress payload is the one filename in the fixture set
# whose spelling is not fully determined: the sink payload families are vendor-branded
# (Payload_Report_Physical_Address_<VENDOR>.yaml), while the source plugin's equivalent
# document names this one document by ROLE instead. Both spellings describe the same frame, so
# the first candidate that exists is used and the search order is fixed rather than
# discovered - the resolution is deterministic for any given fixture tree. When neither is
# present the canonical vendor-branded name is kept, so the rejection reported by the
# vComponent names the document the tree is expected to carry.
_BOOTSTRAP_RPA_CANDIDATES = (
    "DeviceListConfig/Payload_Report_Physical_Address_YAMAHA.yaml",
    "DeviceListConfig/Payload_Report_Physical_Address_AudioSystem.yaml",
)

BOOTSTRAP_RPA_YAML = next(
    (
        candidate
        for candidate in _BOOTSTRAP_RPA_CANDIDATES
        if os.path.isfile(os.path.join(HDMICEC_CMD_BASE, candidate))
    ),
    _BOOTSTRAP_RPA_CANDIDATES[0],
)

# The source-role peers this module seeds beneath the sink television, as
# (logical address, expected osdName, expected vendorID, ReportPhysicalAddress payload,
#  SetOSDName payload, DeviceVendorID payload). The bootstrap peer is listed first for
# readability only; it is selected below by address, so the order carries no meaning.
#
# EVERY FIELD HERE IS CROSS-CHECKED AGAINST THE PAYLOAD DOCUMENTS IT NAMES. The logical address
# must equal the initiator nibble of all three payloads' header bytes, and the expected vendor
# identifier must equal the three operand bytes of the DeviceVendorID payload - otherwise the
# frames register one address while the verification looks for another, and the peer silently
# never verifies. That is exactly what had happened to PANASONIC: it was seeded at address 8
# while all three of its payloads carried initiator 0xB (address 11) and declared it a Tuner at
# port 6, which is also what Device_Config_Add_Network.yaml declares. Address 8 is Playback
# Device 2, not a tuner, so the address was the wrong one of the three: PANASONIC is now seeded
# at 6 - Tuner 2, since Tuner 1 at address 3 is taken by SAMSUNG - and its payload headers were
# changed to match (0x6F broadcast, 0x60 directed to the television).
#
# THIS TABLE IS DERIVED FROM ONE AUTHORITATIVE TOPOLOGY AND MUST MATCH IT EXACTLY. The
# emulated network is declared in vcomponent_configurations/commands/Device_Config_Add_Network.yaml,
# which this module posts as its first configuration action, and the vComponent allocates each
# peer's logical and physical address deterministically from that declaration. The six entries
# below are that allocation:
#
#   LA  osdName    device type      physical   where it sits
#   --  ---------  ---------------  ---------  --------------------------------------------
#    1  DENON      Recording Dev.   2.1.0.0    port 1 of the audio system
#    2  LG         Recording Dev.   2.3.0.0    port 3 of the audio system
#    3  SAMSUNG    Tuner            1.0.0.0    the television's first HDMI input
#    4  SONY       Playback Dev.    3.0.0.0    the television's third HDMI input
#    5  YAMAHA     Audio System     2.0.0.0    the television's second HDMI input - the ARC port
#    8  PANASONIC  Playback Dev.    2.2.0.0    port 2 of the audio system
#
# Every address is canonical for its device type, as LogicalAddress::getType() in
# hdmicec/ccec/include/ccec/Operands.hpp defines it: 1 and 2 are recording devices, 3 is a
# tuner, 4 and 8 are playback devices, 5 is the audio system. Address 0 is the television -
# the device under test - and is deliberately absent. An entry here that names an address the
# topology gives to a different peer does not fail loudly: the seed payload announces one
# device and this table waits for another, so the wait simply times out.
#
# ADDRESSING CONTRACT for the referenced payload documents. The sink's frame handlers filter
# on the destination of the frame, and not uniformly: process(ReportPhysicalAddress) and
# process(DeviceVendorID) accept BROADCAST frames only and discard directed ones, whereas
# process(SetOSDName) accepts DIRECTED frames only and discards broadcasts. A SetOSDName
# payload for the sink must therefore be addressed to the television at address 0, while the
# other two must be broadcast. The source plugin guards none of the three, which is why its
# payload documents are broadcast throughout and cannot be reused here verbatim.
PEER_SEEDS = (
    # Audio System - bootstrap peer, and the ARC counterpart of the sink.
    (
        5,
        "YAMAHA",
        "00A0AF",
        BOOTSTRAP_RPA_YAML,
        "DeviceListConfig/Payload_Set_OSD_Name_YAMAHA.yaml",
        "DeviceListConfig/Payload_Vendor_ID_YAMAHA.yaml",
    ),
    # Playback peer on sink HDMI port 4 - the active-source candidate the routing flows
    # switch to. Announced device_type is PlaybackDevice (0x04).
    (
        9,
        "SONY",
        "080046",
        "DeviceListConfig/Payload_Report_Physical_Address_SONY.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_SONY.yaml",
        "DeviceListConfig/Payload_Vendor_ID_SONY.yaml",
    ),
    # Second routing peer, on sink HDMI port 6, so a route change has somewhere to go.
    # Announced device_type is Tuner (0x03).
    (
        11,
        "PANASONIC",
        "008045",
        "DeviceListConfig/Payload_Report_Physical_Address_PANASONIC.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_PANASONIC.yaml",
        "DeviceListConfig/Payload_Vendor_ID_PANASONIC.yaml",
    ),
    # Tuner peer on sink HDMI port 1. Announced device_type is Tuner (0x03).
    (
        3,
        "SAMSUNG",
        "0000F0",
        "DeviceListConfig/Payload_Report_Physical_Address_SAMSUNG.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_SAMSUNG.yaml",
        "DeviceListConfig/Payload_Vendor_ID_SAMSUNG.yaml",
    ),
    # Recording peer on sink HDMI port 7. Announced device_type is RecordingDevice (0x01).
    (
        2,
        "DENON",
        "0009B0",
        "DeviceListConfig/Payload_Report_Physical_Address_DENON.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_DENON.yaml",
        "DeviceListConfig/Payload_Vendor_ID_DENON.yaml",
    ),
    # Second audio-system peer on sink HDMI port 5. Announced device_type is
    # AudioSystem (0x05), which exercises device-list handling of a second audio device
    # alongside the ARC counterpart at address 5.
    (
        10,
        "LG",
        "DeviceListConfig/Payload_Report_Physical_Address_LG.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_LG.yaml",
        "DeviceListConfig/Payload_Vendor_ID_LG.yaml",
    ),
)

# Selected by address rather than by position, so reordering PEER_SEEDS cannot silently move
# the bootstrap device.
BOOTSTRAP_PEER = next(
    peer for peer in PEER_SEEDS if peer[0] == BOOTSTRAP_LOGICAL_ADDRESS
)
REMAINING_PEERS = tuple(
    peer for peer in PEER_SEEDS if peer[0] != BOOTSTRAP_LOGICAL_ADDRESS
)

# CECVersion frame from the bootstrap peer. It carries no new field of its own; it re-enters
# addDevice() for the same address, which is why it is posted as an idempotent confirmation
# after the seed triplet.
BOOTSTRAP_CEC_VERSION_YAML = "DeviceListConfig/Payload_CECVersion.yaml"

# Network definition posted before anything is seeded: the emulated peers and the ports they
# occupy beneath the sink television.
NETWORK_CONFIG_YAML = "Device_Config_Add_Network.yaml"

# No secondary seed path is declared here, and that is deliberate rather than an omission. A
# tuple of Device_Give_*.yaml documents used to sit at this point and was posted when the
# injected triplet had not produced the address. Those documents are directed requests TO the
# television - header 0x50, initiator 5, destination 0 - so they ask the device under test for
# its own details and can never make a peer appear. The reasoning is recorded in full at the
# point where the ladder was removed, in the bootstrap seed loop below.

# How long the middleware's own poll-based discovery is given before seeding begins, and how
# often it is sampled while waiting.
DISCOVERY_TIMEOUT_SECONDS = 20.0
# Interval between two readings of an observable state, not a pause - see the bounded waiters below,
# each of which tests its predicate before waiting and returns its own expiry.
DISCOVERY_POLL_SECONDS = 1.0

# The one deliberately timed construct in this module: inter-frame pacing on the CEC bus, where no
# per-frame state is observable. See the DEFERRED notes at _inject_triplet and the Give* fallback.
GIVE_COMMAND_PACING_SECONDS = 0.25

# Attempts allowed for the bootstrap seed ladder and for each secondary peer.
MAX_SEED_ATTEMPTS = 3

# Smallest budget worth spending on a FOLLOW-UP request.
#
# Every wait in this module is bounded by a deadline and hands each request the time it has left,
# so one call cannot outlive that deadline. The transport expresses its bound in whole seconds,
# so a budget below one second cannot be expressed at all, and a follow-up poll issued with less
# than a second remaining could only finish by overrunning the deadline it was meant to respect.
# Such a poll is not issued: the wait ends and reports what it last saw.
#
# THE FIRST ATTEMPT IS UNCONDITIONAL, and deliberately so. Gating it on this floor too would turn
# any wait of a second or less into a silent no-op - it would return "not found" without ever
# having looked, which is precisely the kind of quiet degradation these waits exist to prevent.
# The first request is therefore always issued, with its budget rounded up to the one-second
# granularity the transport supports, so a deadline shorter than a second can overrun by at most
# that. Every caller in this module asks for 3 seconds or 20, so none is affected in practice.
_MIN_REQUEST_BUDGET_SECONDS = 1.0

# Environment gates, documented in README.txt.
#
#   Init_Devicelist_Populate_STRICT_MULTI = 1 | true | yes | on
#       every peer in PEER_SEEDS must be present with its expected osdName and a non-empty
#       vendorID, and the device count must reach len(PEER_SEEDS).
#   Init_Devicelist_Populate_STRICT_MULTI = 0 | false | no | off, or unset, or empty
#       bootstrap mode: only the bootstrap peer is mandatory; each other shortfall is
#       reported as a note, and the device count must reach
#       Init_Devicelist_Populate_MIN_DEVICES, default 1 and accepted only within
#       1..len(PEER_SEEDS).
#   any OTHER value of either variable
#       a configuration error. Initialization fails and names the variable. It is
#       deliberately NOT interpreted: silently reading an unrecognised value as "off" would
#       disable the strict gate an operator had asked for, and no output would have said so.
STRICT_MULTI_ENV = "Init_Devicelist_Populate_STRICT_MULTI"
MIN_DEVICES_ENV = "Init_Devicelist_Populate_MIN_DEVICES"

# The ONLY accepted spellings for the strict gate, compared case-insensitively after stripping
# surrounding whitespace.
#
# The previous test was `os.environ.get(STRICT_MULTI_ENV, "0") == "1"`, and its failure mode was
# the dangerous direction: any value that was not exactly "1" - "true", "yes", "ON", " 1", or a
# plain typo - silently selected the LENIENT mode. An operator who believed strict checking was
# on would have got a bootstrap-mode pass in which every peer shortfall is a note rather than a
# failure, and nothing in the output would have said so. A gate that can be disabled by a typo
# is not a gate, so an unrecognised value is now refused by name instead of being interpreted.
_TRUE_SPELLINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_SPELLINGS = frozenset({"0", "false", "no", "off"})


# ── helpers ──────────────────────────────────────────────────────────────────

def _normalised_vendor_id(value):
    '''Reduce a vendor identifier to its bytes, so two renderings of the same value compare equal.

    A three-byte CEC vendor identifier can legitimately be rendered as "008045", "0x008045",
    "00:80:45" or "00 80 45", in either case. Comparing the rendered strings would make this
    verification a test of the middleware's formatting rather than of the value it recorded, so
    only the hexadecimal digits are kept and the result is lower cased.

    Args:
        value: A vendor identifier as reported by getDeviceList, or as declared in PEER_SEEDS.
    Returns:
        The lower-cased hexadecimal digits of the value, with any "0x" prefix, separators and
        surrounding whitespace removed; the empty string when the input contains no hex digits.
    '''
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return "".join(character for character in text if character in "0123456789abcdef")


def _post(yaml_name):
    """POST a vcomponent command YAML; returns True on HTTP 200.

    The name is joined onto HDMICEC_CMD_BASE so that a deployment can retarget the whole
    fixture set with one environment variable. Anything other than 200 - including the 0 that
    utils reports for a missing document, a refused path or a curl failure - is a rejection,
    and the reason the emulator gave is logged so it is visible rather than inferred.

    The body is remote-derived, so it is logged through utils.sanitise_for_log rather than
    interpolated raw: an endpoint that answered with terminal control sequences would otherwise
    be able to erase or repaint the surrounding transcript, and that transcript is the whole
    evidence a device-level run produces. Bounded and escaped, the diagnostic is still readable
    and can no longer rewrite anything around it.
    """
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_name}")
    log_info(f"  POST {yaml_name}: HTTP {http_code}  {sanitise_for_log(body)}")
    return http_code == 200


def _resolve_strict_multi():
    """Resolve STRICT_MULTI_ENV to a bool, or None when its value is not a documented spelling.

    Returns None rather than a default so the caller can refuse to run. A gate that selects how
    much of the device list is enforced must not fall back to the weaker setting when it cannot
    be understood: "STRICT_MULTI=true" reaching a comparison against the literal "1" is how a
    run that was configured for full enforcement quietly performs the partial one instead.
    """
    raw = os.environ.get(STRICT_MULTI_ENV, "")
    normalised = raw.strip().lower()
    if normalised in _TRUE_SPELLINGS:
        return True
    if normalised in _FALSE_SPELLINGS:
        return False
    log_error(
        f"Init_Devicelist_Populate Failed ❌: {STRICT_MULTI_ENV}="
        f"'{sanitise_for_log(raw, max_chars=64)}' is not a recognised value; use one of "
        f"{sorted(_TRUE_SPELLINGS)} to enable strict mode or one of "
        f"{sorted(_FALSE_SPELLINGS - {''})} to select bootstrap mode"
    )
    return None


def _resolve_min_devices():
    """Resolve MIN_DEVICES_ENV to an int in [1, len(PEER_SEEDS)], or None when it is invalid.

    Returns None rather than the documented default so the caller can refuse to run. A floor
    that silently becomes 1 because it was mistyped is worse than no floor at all: the run
    still reports the mode it was asked for while enforcing far less than it was told to.
    """
    raw = os.environ.get(MIN_DEVICES_ENV)
    if raw is None:
        return 1

    candidate = raw.strip()
    try:
        # int(x, 10) rejects the underscore-separated and non-decimal spellings int(x) accepts,
        # so "1_0" and "0x2" are configuration errors here rather than surprising values.
        minimum = int(candidate, 10)
    except ValueError:
        log_error(
            f"Init_Devicelist_Populate Failed ❌: {MIN_DEVICES_ENV}="
            f"'{sanitise_for_log(raw, max_chars=64)}' is not a base-10 integer"
        )
        return None

    if not 1 <= minimum <= len(PEER_SEEDS):
        log_error(
            f"Init_Devicelist_Populate Failed ❌: {MIN_DEVICES_ENV}={minimum} is out of range; "
            f"this module seeds {len(PEER_SEEDS)} peers, so the floor must be between 1 and "
            f"{len(PEER_SEEDS)} inclusive"
        )
        return None

    return minimum


def _get_device_list():
    """Call getDeviceList and return the parsed result dict, or None on error.

    The "< No response" prefix is the byte-exact sentinel utils.send_curl_command returns for
    a transport failure; testing for it here is what keeps an unreachable device a clean None
    instead of a JSON decode error further up.

    Args:
        timeout: Optional whole-second bound passed straight through to the transport, so a
                 caller polling against a deadline spends only the time it still has. Omitted
                 by callers that are not deadline-driven, which keeps the transport default.
    """
    response = send_curl_command(HdmiCecSinkApis.get_device_list, timeout=timeout)
    if not response or response.startswith("< No response"):
        return None
    try:
        body = json.loads(response)
        return body.get("result")
    except json.JSONDecodeError:
        return None


def _set_enabled_true():
    """Enable HdmiCecSink plugin; returns True when API reports success."""
    result = _jsonrpc_result(send_curl_command(HdmiCecSinkApis.set_enabled_true), "setEnabled(true)")
    return bool(result) and result.get("success") is True


def _set_enabled_false():
    """Disable HdmiCecSink plugin; returns True when API reports success.

    Used only as the first half of the enable toggle below. This module's post-condition is an
    ENABLED plugin, so a call here is always followed by a call to _set_enabled_true().
    """
    result = _jsonrpc_result(send_curl_command(HdmiCecSinkApis.set_enabled_false), "setEnabled(false)")
    return bool(result) and result.get("success") is True


def _get_enabled_state():
    """Return plugin enabled state as bool, or None on parse/transport error.

    A non-boolean or absent member yields None rather than a coerced False, so "the device did
    not tell us" stays distinguishable from "the device said no".
    """
    result = _jsonrpc_result(send_curl_command(HdmiCecSinkApis.get_enabled), "getEnabled")
    if result is None:
        return None
    enabled = result.get("enabled")
    return enabled if isinstance(enabled, bool) else None


def _build_la_map(device_list):
    """Build logicalAddress -> device entry map from getDeviceList payload.

    Entries that are not objects, and objects whose logicalAddress is not an integer, are
    skipped: the map is only ever keyed by an address a caller can look up with an int.
    """
    return {
        d["logicalAddress"]: d
        for d in device_list
        if isinstance(d, dict) and isinstance(d.get("logicalAddress"), int)
    }


def _inject_triplet(la, rpa_yaml, osd_yaml, vid_yaml, step_timeout_s=2.0, step_poll_s=0.2):
    """Inject ReportPhysicalAddress + SetOSDName + DeviceVendorID for one device.

    Posted in that order because the first frame is what registers the device; the two that
    follow fill in fields on an entry that already exists. The first rejection stops the
    sequence, so a missing fixture is reported against the document that is actually absent
    rather than against whichever frame happened to be posted last.

    Between frames the device is OBSERVED, not paced. Each frame has a visible consequence in
    getDeviceList - the address appears, then osdName is filled in, then vendorID - so each wait
    ends the moment that consequence is seen instead of paying a fixed delay that is either too
    short to be a synchronisation or too long to be free. None of the three waits is fatal: a
    build that has not applied a frame within its budget still receives the remaining frames,
    and the caller's own wait decides what the seed attempt amounted to. Only a REJECTED post is
    fatal here, which is the pre-existing contract.
    """
    for yaml_name in (rpa_yaml, osd_yaml, vid_yaml):
        if not _post(yaml_name):
            return False
        # DEFERRED, not converted: this is inter-FRAME pacing on the CEC bus, and no interface
        # exposes a per-frame state to wait on. _post's reply confirms the vcomponent accepted the
        # document, not that the frame has been carried on the bus and absorbed by the middleware,
        # and the only observable outcome - the device appearing in getDeviceList - is a property of
        # the whole triplet, which _seed_device already waits for through _wait_for_device. Removing
        # the gap would rely on frames posted back-to-back never being coalesced by the transport,
        # which cannot be verified from here. Recorded as deferred rather than deleted or guessed.
        time.sleep(inter_cmd_delay)
    return True


def _wait_for_device(la, expected_name, timeout_s=3.0, poll_s=0.4):
    """Wait until getDeviceList shows expected LA with expected OSD name.

    Returns (found, entry, last_result). Stricter than _wait_for_la: the address alone is not
    enough, the OSD name must have arrived too, which is the condition a caller needs when it
    is about to assert on the name.

    The deadline is MONOTONIC and is genuinely respected. Two things used to make the advertised
    timeout notional: it was measured with time.time(), which a clock step can move backwards or
    forwards mid-wait, and the deadline was only consulted BETWEEN polls, so a request that took
    longer than the whole budget still ran to its own transport bound - a 3 second wait could
    take far longer than 3 seconds. Now the clock cannot jump, each request is given only the
    time that remains, and the poll sleep is capped by it too, so the wait ends when it says it
    will.
    """
    deadline = time.monotonic() + timeout_s
    last_result = None
    attempted = False
    while True:
        remaining = deadline - time.monotonic()
        if attempted and remaining < _MIN_REQUEST_BUDGET_SECONDS:
            break

        attempted = True
        result = _get_device_list(timeout=max(1, int(remaining)))
        last_result = result
        if result and result.get("success") is True:
            la_map = _build_la_map(result.get("deviceList", []))
            entry = la_map.get(la)
            if entry and entry.get("osdName") == expected_name:
                return True, entry, result

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_s, remaining))
    return False, None, last_result


def _wait_for_la(la, timeout_s=3.0, poll_s=0.4):
    """Wait until getDeviceList includes LA; returns (found, entry, result).

    Presence of the address is the registration signal - addDevice() has run - and is
    deliberately weaker than _wait_for_device: the OSD name and vendor identifier are filled
    in by later frames and may still be empty at this point.

    Monotonic and budget-bounded for the same reasons given on _wait_for_device: the clock
    cannot step under the wait, no single request may outlast the deadline, and the poll sleep
    never carries the wait past it.
    """
    deadline = time.monotonic() + timeout_s
    last_result = None
    attempted = False
    while True:
        remaining = deadline - time.monotonic()
        if attempted and remaining < _MIN_REQUEST_BUDGET_SECONDS:
            break

        attempted = True
        result = _get_device_list(timeout=max(1, int(remaining)))
        last_result = result
        if result and result.get("success") is True:
            la_map = _build_la_map(result.get("deviceList", []))
            entry = la_map.get(la)
            if entry is not None:
                return True, entry, result

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_s, remaining))
    return False, None, last_result


def _wait_for_device_list_to_settle(timeout_s=4.0, reread_interval_s=0.4, settled_readings=2):
    """Wait until getDeviceList reports the same logical addresses twice in a row.

    The middleware exposes no "discovery finished" signal, so the set of addresses the public
    getDeviceList reports is the only observable proxy for quiet: two consecutive readings that
    agree mark a window in which nothing was added. The list is read BEFORE any wait, so a list
    that is already stable costs one request; reread_interval_s is the interval between two
    readings of that observable state, and timeout_s is a failure deadline that is returned rather
    than hidden.
    """
    deadline = time.time() + timeout_s
    previous = None
    stable = 0
    while True:
        result = _get_device_list()
        if result and result.get("success") is True:
            current = sorted(_build_la_map(result.get("deviceList", [])).keys())
            if previous is not None and current == previous:
                stable += 1
                if stable >= settled_readings:
                    return True
            else:
                stable = 0
            previous = current
        if time.time() >= deadline:
            return False
        time.sleep(reread_interval_s)


def _seed_device(la, expected_name, rpa_yaml, osd_yaml, vid_yaml, attempts=3):
    """Seed one device into deviceList with retries; returns True when LA appears.

    A rejected injection is retried rather than abandoned on the first attempt, because the
    emulator can refuse a frame transiently while the middleware's poll thread holds the bus.
    The retries are the tolerance; the return value is not. Step 2b treats a False as a failure
    of the whole module in both modes, so a peer that never appears is reported rather than
    absorbed - which is the difference between a device list this module can vouch for and one
    that merely happens to be non-empty.
    """
    for attempt in range(1, attempts + 1):
        log_info(f"  Seed LA={la} ({expected_name}) attempt {attempt}/{attempts}")
        if not _inject_triplet(la, rpa_yaml, osd_yaml, vid_yaml):
            log_warning(f"  Seed warning: injection rejected for LA={la} ({expected_name})")
            continue

        found, entry, _ = _wait_for_la(la, timeout_s=3.0, poll_s=0.4)
        if found:
            log_success(
                f"  ✓ Seed learned LA={la:2d}"
                f"  osdName='{sanitise_for_log(entry.get('osdName', ''), max_chars=128)}'"
                f"  vendorID='{sanitise_for_log(entry.get('vendorID', ''), max_chars=128)}'"
            )
            return True

    log_warning(
        f"  Seed warning: LA={la} ({expected_name}) did not appear after {attempts} attempts"
    )
    return False



def _resolve_strict_multi():
    """Resolve the strict gate. Returns (ok, strict_multi); logs its own refusal.

    Unset or empty selects bootstrap mode, which is the documented default. Every other value
    must be one of the documented spellings; anything else is a configuration error and is
    reported rather than guessed at, because guessing here means silently weakening the check
    the operator asked for.
    """
    raw = os.environ.get(STRICT_MULTI_ENV)
    if raw is None or raw.strip() == "":
        return True, False

    token = raw.strip().lower()
    if token in _TRUE_SPELLINGS:
        return True, True
    if token in _FALSE_SPELLINGS:
        return True, False

    log_error(
        f"Init_Devicelist_Populate Failed ❌: {STRICT_MULTI_ENV}='{raw}' is not one of "
        f"the accepted values {sorted(_TRUE_SPELLINGS | _FALSE_SPELLINGS)}. Refusing rather than "
        "defaulting, because an unrecognised value here would silently select the lenient mode."
    )
    return False, False


def _resolve_min_devices(strict_multi):
    """Resolve the minimum device count. Returns (ok, minimum); logs its own refusal.

    In strict mode the minimum is the full peer count and is not overridable. The override is
    still VALIDATED in that mode even though it is not used, so a misspelling is reported where
    it was made instead of lying dormant until someone turns strict mode off.

    The accepted range is 1 to len(PEER_SEEDS). Below 1 the gate asserts nothing - and 0 or a
    negative number would let an EMPTY device list pass, which is precisely the outcome this
    check exists to prevent. Above len(PEER_SEEDS) it can never be satisfied, because this
    module seeds exactly that many peers, so it would fail every run for a reason no output
    would explain. Both were accepted before; both are refused now.
    """
    limit = len(PEER_SEEDS)
    raw = os.environ.get(MIN_DEVICES_ENV)

    if raw is None or raw.strip() == "":
        return True, (limit if strict_multi else 1)

    try:
        requested = int(raw.strip())
    except ValueError:
        log_error(
            f"Init_Devicelist_Populate Failed ❌: {MIN_DEVICES_ENV}='{raw}' is not an "
            f"integer; expected a whole number between 1 and {limit}."
        )
        return False, 0

    if not 1 <= requested <= limit:
        log_error(
            f"Init_Devicelist_Populate Failed ❌: {MIN_DEVICES_ENV}={requested} is "
            f"outside the accepted range 1..{limit} (this module seeds {limit} peers, and a "
            "minimum below 1 would let an empty device list pass)."
        )
        return False, 0

    if strict_multi:
        log_info(
            f"  Note ⚠: {MIN_DEVICES_ENV}={requested} is validated but not applied - "
            f"strict mode requires the full peer count of {limit}."
        )
        return True, limit

    return True, requested


# ── test ─────────────────────────────────────────────────────────────────────

def verify_seed_payload_consistency():
    '''Check every PEER_SEEDS entry against the payload documents it names, before anything runs.

    This exists because the drift it detects had really happened and had cost nothing to hide:
    PANASONIC was seeded at logical address 8 while all three of its payloads carried initiator
    0xB, so the injected frames registered address 11, the verification looked for address 8, and
    in bootstrap mode the shortfall was reported as a note. A run that seeds one address and
    verifies another looks almost exactly like a run that succeeded.

    Three properties are checked per peer, all of them derivable from files this suite owns, so
    the check needs no device, no emulator and no network:
      * every payload's header byte carries the peer's logical address as its initiator nibble;
      * the ReportPhysicalAddress and DeviceVendorID payloads are broadcast (destination 0xF) and
        the SetOSDName payload is directed to the television (destination 0x0), which is what the
        sink's own frame handlers require;
      * the DeviceVendorID payload's three operand bytes equal the expected vendor identifier.

    Returns:
        True when every peer is consistent; False after logging one line per inconsistency.
    '''
    problems = []
    header_pattern = re.compile(r'payload:\s*\[(.*?)\]', re.S)

    def header_and_operands(yaml_name):
        path = os.path.join(HDMICEC_CMD_BASE, yaml_name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            return None, None, f"cannot read {yaml_name}: {exc}"
        match = header_pattern.search(text)
        if not match:
            return None, None, f"{yaml_name} declares no payload list"
        try:
            values = [
                int(token.strip().strip('"').strip("'"), 16)
                for token in match.group(1).split(",")
                if token.strip()
            ]
        except ValueError as exc:
            return None, None, f"{yaml_name} has a non-hexadecimal payload byte: {exc}"
        if not values:
            return None, None, f"{yaml_name} declares an empty payload"
        return values[0], values[1:], None

    for la, expected_name, expected_vendor, rpa_yaml, osd_yaml, vid_yaml in PEER_SEEDS:
        for yaml_name, expected_destination in (
            (rpa_yaml, 0xF),
            (osd_yaml, 0x0),
            (vid_yaml, 0xF),
        ):
            header, operands, error = header_and_operands(yaml_name)
            if error:
                problems.append(f"LA={la} ({expected_name}): {error}")
                continue
            initiator = (header >> 4) & 0xF
            destination = header & 0xF
            if initiator != la:
                problems.append(
                    f"LA={la} ({expected_name}): {yaml_name} header 0x{header:02X} has "
                    f"initiator {initiator}, so its frames register address {initiator}"
                )
            if destination != expected_destination:
                problems.append(
                    f"LA={la} ({expected_name}): {yaml_name} header 0x{header:02X} is addressed "
                    f"to 0x{destination:X}, expected 0x{expected_destination:X}"
                )
            if yaml_name is vid_yaml and operands is not None and len(operands) >= 4:
                reported = "".join(f"{byte:02X}" for byte in operands[1:4])
                if _normalised_vendor_id(reported) != _normalised_vendor_id(expected_vendor):
                    problems.append(
                        f"LA={la} ({expected_name}): {yaml_name} carries vendor {reported}, "
                        f"but PEER_SEEDS expects {expected_vendor}"
                    )

    if problems:
        for problem in problems:
            log_error(f"Init_Devicelist_Populate Failed ❌: seed/payload mismatch: {problem}")
        return False

    log_success(
        f"  ✓ seed table and payload documents agree for all {len(PEER_SEEDS)} peers"
    )
    return True


def run_test():
    start_time = time.perf_counter()

    """
    Step 0  – activate org.rdk.HdmiCecSink, the sink television under test.
    Step 1  – configure the vcomponent network of SOURCE-ROLE peers beneath the sink TV
              (audio system, two playback devices, a tuner, a recording device) and enable
              HDMI-CEC so the middleware's poll and discovery threads run.
    Step 2  – give auto-discovery its window; where it falls short, inject CEC frames from the
              audio-system peer (LA=5) so the middleware populates deviceList through its
              normal process() handlers:
                ReportPhysicalAddress -> addDevice(5)
                SetOSDName            -> deviceList[5].m_osdName  = "YAMAHA"
                DeviceVendorID        -> deviceList[5].m_vendorID = 0x00A0AF
                CECVersion            -> addDevice(5) (idempotent confirmation)
    Step 2b – seed the remaining source-role peers by the same route.
    Step 3  – call getDeviceList and verify the seeded peers are present with their details.
    """

    # ── Step -1: resolve the environment gates before anything is touched ────
    #
    # Read first, and refused here rather than at Step 3, for two reasons. A configuration
    # error is not worth forty seconds of seeding to discover, and - more importantly - reading
    # the gates only at the end means the mode that governs enforcement is chosen after the
    # evidence has been collected. Both resolvers report their own reason and return None, so a
    # gate that cannot be understood ends the module instead of selecting a mode for it.
    strict_multi = _resolve_strict_multi()
    if strict_multi is None:
        return False

    min_devices_required = _resolve_min_devices()
    if min_devices_required is None:
        return False

    if strict_multi:
        # Strict mode's floor is the full peer count and is not overridable downwards; an
        # explicit lower floor is honoured as documentation of intent but cannot weaken it.
        min_devices_required = max(min_devices_required, len(PEER_SEEDS))

    log_info(
        f"Init_Devicelist_Populate gates: {STRICT_MULTI_ENV}={strict_multi}  "
        f"effective minimum device count={min_devices_required}"
    )

    # ── Step 0: ensure plugin is active (standalone-safe) ───────────────────
    log_info(
        f"Init_Devicelist_Populate Step 0: activate plugin {SINK_CALLSIGN} "
        f"via {WPEFRAMEWORK_JSONRPC_URL}"
    )
    if not activate_plugin(SINK_CALLSIGN):
        log_error(
            f"Init_Devicelist_Populate Failed ❌: plugin activation failed ({SINK_CALLSIGN})"
        )
        return False
    # Readiness is observable through Controller.1.status, so it is waited for rather than
    # estimated: activate_plugin returns when the request is accepted, while Initialize() and the
    # CEC threads come up after it. Costs nothing when the plugin is already up, and an expiry is
    # reported rather than treated as ready.
    if not await_plugin_ready(SINK_CALLSIGN):
        log_warning(
            f"Init_Devicelist_Populate Note : {SINK_CALLSIGN} did not report state 'activated' "
            "before the deadline; the steps below will run anyway and report their own outcome"
        )

    # ── Step 1: configure ────────────────────────────────────────────────────
    log_info(
        "Init_Devicelist_Populate Step 1: configure vcomponent network of source-role peers"
    )
    if not _post(NETWORK_CONFIG_YAML):
        log_error("Init_Devicelist_Populate Failed ❌: configure command rejected")
        return False

    # AN ENABLED PLUGIN IS A REQUIRED POSTCONDITION OF THIS MODULE, NOT AN ASPIRATION.
    #
    # Every one of the 33 test cases in this suite starts from the premise that HDMI-CEC is
    # enabled and the device list is populated, and almost none of them re-establishes it.
    # With CEC disabled the plugin's own guards make the whole suite meaningless while
    # keeping it green-looking: the poll and discovery threads never run, so no peer is ever
    # added; an injected frame reaches no handler, so every seed is absorbed silently; and
    # the write-side APIs return early - Process_SetSystemAudioMode_msg and
    # RequestAudioDevicePowerStatus both bail while cecSettingEnabled is false. Reporting
    # that as a warning and returning True hands 33 cases a precondition that does not hold,
    # and each of them then fails, or passes vacuously, for a reason unrelated to what it
    # tests. So both the enable REQUEST and the enabled READBACK are hard gates here.
    #
    # The toggle is retained as the one recovery this module owns - a plugin that is already
    # in an inconsistent enable state is often repaired by driving it false and true again -
    # but its outcome is now decisive rather than advisory.
    if not _set_enabled_true():
        log_warning(
            "Init_Devicelist_Populate Note ⚠: setEnabled(true) did not report success; "
            "attempting toggle"
        )
        _set_enabled_false()
        if not _set_enabled_true():
            log_error(
                "Init_Devicelist_Populate Failed ❌: setEnabled(true) did not report success, "
                "before or after a false/true toggle. HDMI-CEC cannot be brought up, so the "
                "middleware's poll and discovery threads will not run and no test case in this "
                "suite has its stated precondition"
            )
            return False

    # The readback is a SEPARATE gate from the request above, because the two can disagree:
    # setEnabled returns success once the request is accepted, while getEnabled reports the
    # state the plugin actually settled into. A success that does not stick is exactly the
    # case this catches.
    enabled_state = _get_enabled_state()
    log_info(f"  HdmiCecSink enabled={enabled_state}")
    if enabled_state is not True:
        log_error(
            "Init_Devicelist_Populate Failed ❌: HDMI-CEC did not read back as enabled "
            f"(getEnabled returned {enabled_state!r}). The identity comparison is deliberate: "
            "a missing member, a transport sentinel and a string \"true\" all arrive here as "
            "something other than the boolean True, and none of them is evidence that CEC is on"
        )
        return False

    time.sleep(1)

    # ── Step 2: inject CEC payload frames ────────────────────────────────────
    log_info(
        "Init_Devicelist_Populate Step 2: wait for middleware to auto-discover devices via poll"
    )

    # After configure, the middleware's poll thread discovers every peer whose logical address
    # the vcomponent ACKs. For each ACKed address it calls addDevice() and then
    # requestCecDevDetails(), which makes the vcomponent answer with SetOSDName /
    # DeviceVendorID. Discovery is given its full window before any frame is injected, so a
    # build that populates the list unaided is measured rather than overwritten.
    # Monotonic, budget-propagating and sleep-capped, exactly as the two wait helpers above and
    # for the same reason: this is the longest wait in the module, so an unbounded overrun here
    # delays the whole suite before its first case has run.
    deadline = time.monotonic() + DISCOVERY_TIMEOUT_SECONDS
    last_la_map = {}
    attempted = False
    while True:
        remaining = deadline - time.monotonic()
        if attempted and remaining < _MIN_REQUEST_BUDGET_SECONDS:
            break

        attempted = True
        result = _get_device_list(timeout=max(1, int(remaining)))
        if result and result.get("success") is True:
            last_la_map = _build_la_map(result.get("deviceList", []))
            found_las = sorted(last_la_map.keys())
            log_info(f"  Polling: discovered LAs={found_las}")
            # Step 2 is considered ready once the bootstrap audio system is present.
            if BOOTSTRAP_LOGICAL_ADDRESS in last_la_map:
                break

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(DISCOVERY_POLL_SECONDS, remaining))

    (bootstrap_la, bootstrap_name, bootstrap_vendor, bootstrap_rpa, bootstrap_osd,
     bootstrap_vid) = BOOTSTRAP_PEER

    # Fallback path: some builds do not auto-discover from poll within timeout. Seed the
    # bootstrap peer explicitly so a usable non-empty list is still guaranteed.
    if bootstrap_la not in last_la_map:
        log_warning(
            "Init_Devicelist_Populate Step 2 fallback: auto-discovery incomplete, "
            f"injecting LA={bootstrap_la} seed frames"
        )
        for attempt in range(1, MAX_SEED_ATTEMPTS + 1):
            log_info(f"  Seed LA={bootstrap_la} attempt {attempt}/{MAX_SEED_ATTEMPTS}")
            ok_triplet = _inject_triplet(
                bootstrap_la, bootstrap_rpa, bootstrap_osd, bootstrap_vid
            )
            ok_cecver = _post(BOOTSTRAP_CEC_VERSION_YAML)
            if not ok_triplet or not ok_cecver:
                log_error(
                    f"Init_Devicelist_Populate Failed ❌: LA={bootstrap_la} seed injection "
                    "rejected"
                )
                return False

            found, entry, _ = _wait_for_la(bootstrap_la, timeout_s=3.0, poll_s=0.4)
            if found:
                log_success(
                    f"  ✓ Seed learned LA={bootstrap_la:2d}"
                    f"  osdName='{sanitise_for_log(entry.get('osdName', ''), max_chars=128)}'"
                    f"  vendorID='{sanitise_for_log(entry.get('vendorID', ''), max_chars=128)}'"
                )
                break

            # Secondary seed path: ask the configured peer to answer for itself, so the
            # middleware learns it from a genuine reply rather than an injected announcement.
            log_info(f"  Seed fallback: send Give* commands to LA={bootstrap_la}")
            for yaml_name in GIVE_COMMAND_YAMLS:
                if not _post(yaml_name):
                    log_warning(f"  Seed fallback warning: {yaml_name} rejected")
                # DEFERRED for the same reason as _inject_triplet above: inter-frame bus pacing,
                # with no per-frame state to observe. The observable outcome of the whole group is
                # the address appearing in getDeviceList, which _wait_for_la below waits for.
                time.sleep(GIVE_COMMAND_PACING_SECONDS)

            found, entry, _ = _wait_for_la(bootstrap_la, timeout_s=3.0, poll_s=0.4)
            if found:
                log_success(
                    f"  ✓ Give* learned LA={bootstrap_la:2d}"
                    f"  osdName='{sanitise_for_log(entry.get('osdName', ''), max_chars=128)}'"
                    f"  vendorID='{sanitise_for_log(entry.get('vendorID', ''), max_chars=128)}'"
                )
                break
        else:
            log_error(
                "Init_Devicelist_Populate Failed ❌: middleware did not learn "
                f"LA={bootstrap_la} after fallback seed attempts"
            )
            return False

    # The address is registered; the OSD name arrives on a later frame, so wait for it instead
    # of sleeping blindly. An absent name is a note here rather than a failure - strict mode
    # fails on it in Step 3 and bootstrap mode tolerates it - so it never masks the
    # registration result that Step 2 exists to establish.
    named, _, _ = _wait_for_device(bootstrap_la, bootstrap_name, timeout_s=3.0, poll_s=0.4)
    if named:
        log_success(f"  ✓ Bootstrap LA={bootstrap_la:2d} reports osdName='{bootstrap_name}'")
    else:
        log_warning(
            f"Init_Devicelist_Populate Note ⚠: LA={bootstrap_la} is registered but osdName "
            f"'{bootstrap_name}' has not arrived yet"
        )

    # With the bootstrap peer in place, explicitly seed the remaining configured peers. This
    # avoids depending on poll-based discovery for every secondary device.
    #
    # EVERY SEED MUST LAND, IN BOTH MODES. The result of each _seed_device call is collected and
    # acted on rather than discarded: this module has just posted three frames per peer and
    # watched for the address, so a peer that never appeared after MAX_SEED_ATTEMPTS is a
    # failure of the fixture set or of the emulator, and it is reported here where it happened.
    # Leaving it to Step 3 is what previously let bootstrap mode absorb the shortfall as a note
    # - the module would claim to have seeded five peers, seed fewer, and still return True.
    #
    # This is the one enforcement the mode gate deliberately does NOT reach. The modes exist to
    # decide how strictly the per-device DETAILS are read - the osdName that arrives on a later
    # frame and the vendorID that arrives after that - not whether the devices this module
    # declares are there at all.
    log_info(
        "Init_Devicelist_Populate Step 2b: seed remaining configured source-role peers"
    )
    unseeded = []
    for la, expected_name, expected_vendor, rpa_yaml, osd_yaml, vid_yaml in REMAINING_PEERS:
        if not _seed_device(
            la, expected_name, rpa_yaml, osd_yaml, vid_yaml, attempts=MAX_SEED_ATTEMPTS
        ):
            unseeded.append(f"LA={la} ({expected_name})")

    if unseeded:
        log_error(
            "Init_Devicelist_Populate Failed ❌: the middleware did not register "
            f"{len(unseeded)} of the {len(REMAINING_PEERS)} secondary peers this module seeds "
            f"after {MAX_SEED_ATTEMPTS} attempts each: {', '.join(unseeded)}"
        )
        return False

    # Let the middleware finish absorbing the seeds before the final snapshot is taken - observed
    # rather than timed. Two consecutive readings of getDeviceList that report the same set of
    # logical addresses mark a window in which nothing was added, which is the only "settled" signal
    # this interface exposes. Bounded, and an expiry only means the snapshot below is taken while
    # discovery is still moving, which Step 3 then reports on its own terms.
    if not _wait_for_device_list_to_settle():
        log_warning(
            "Init_Devicelist_Populate Note : the device list was still changing when the settle "
            "window expired; the Step 3 snapshot is taken against a list that may still grow"
        )

    # ── Step 3: verify device list ───────────────────────────────────────────
    log_info(
        "Init_Devicelist_Populate Step 3: verify getDeviceList contains the "
        f"{len(PEER_SEEDS)} seeded source-role peers"
    )

    result = _get_device_list()
    if result is None:
        log_error("Init_Devicelist_Populate Failed ❌: getDeviceList returned no response")
        return False

    success = result.get("success")
    num_devices = result.get("numberofdevices")
    device_list = result.get("deviceList", [])

    # The whole snapshot is remote-derived - every osdName and vendorID in it came off the wire
    # - so it is escaped and bounded before it reaches the console. The gates were resolved and
    # reported at Step -1, so nothing about how strictly this snapshot is read is decided here.
    log_warning(
        f"  success={sanitise_for_log(success, max_chars=32)}  "
        f"numberofdevices={sanitise_for_log(num_devices, max_chars=32)}  "
        f"devices={sanitise_for_log(device_list, max_chars=2048)}"
    )

    if success is not True:
        log_error("Init_Devicelist_Populate Failed ❌: getDeviceList success != true")
        return False

    # Both gates are resolved through validators that refuse an unrecognised value by name.
    # run_test() still owes its caller a bool on every path, so a refusal is a logged False
    # rather than an exception - but it IS a failure now, not a silent fallback.
    gate_ok, strict_multi = _resolve_strict_multi()
    if not gate_ok:
        return False

    gate_ok, min_devices_required = _resolve_min_devices(strict_multi)
    if not gate_ok:
        return False

    if not isinstance(num_devices, int) or num_devices < min_devices_required:
        log_error(
            f"Init_Devicelist_Populate Failed ❌: numberofdevices={num_devices}, "
            f"expected >= {min_devices_required}"
        )
        return False

    if not isinstance(device_list, list):
        log_error("Init_Devicelist_Populate Failed ❌: deviceList is not a list")
        return False

    # Expected devices: LA -> expected osdName, derived from the single peer table above so the
    # verification and the seeding cannot drift apart.
    expected_devices = {
        la: (expected_name, expected_vendor)
        for la, expected_name, expected_vendor, _, _, _ in PEER_SEEDS
    }

    # Build lookup: logicalAddress -> entry
    la_map = _build_la_map(device_list)

    failures = []
    warnings = []

    for la, (expected_name, expected_vendor) in expected_devices.items():
        entry = la_map.get(la)
        if entry is None:
            # PRESENCE IS MANDATORY IN BOTH MODES. Step 2 established the bootstrap peer and
            # Step 2b established every other one, each with its own hard failure, so an address
            # missing here means the middleware forgot a device it had already registered. That
            # is a failure in either mode - a run cannot report that it populated the device
            # list with these peers and then hand the following 33 test cases a list without
            # them. Only the per-device DETAILS below are mode-gated.
            failures.append(f"LA={la} ({expected_name}): not in deviceList")
            continue

        osd_name = entry.get("osdName", "")
        vendor_id = entry.get("vendorID", "")
        # Both came off the wire, so both are escaped and bounded before they are logged or
        # quoted into a failure line.
        safe_osd_name = sanitise_for_log(osd_name, max_chars=128)
        safe_vendor_id = sanitise_for_log(vendor_id, max_chars=128)

        mismatches = []
        if osd_name != expected_name:
            mismatches.append(f"LA={la}: osdName='{osd_name}', expected '{expected_name}'")
        # The vendor identifier is compared by VALUE, not merely tested for emptiness: a peer
        # that answered with somebody else's vendor ID - or with the placeholder the plugin
        # substitutes after a Feature Abort - would satisfy a non-empty check while proving that
        # the DeviceVendorID payload never reached deviceList[la]. Comparison is on the three
        # bytes rather than on the rendered string, because how the middleware renders them
        # (case, separators, an "0x" prefix) is its business and is not what this asserts.
        if not vendor_id:
            mismatches.append(f"LA={la} ({expected_name}): vendorID is empty")
        elif _normalised_vendor_id(vendor_id) != _normalised_vendor_id(expected_vendor):
            mismatches.append(
                f"LA={la} ({expected_name}): vendorID='{vendor_id}', expected "
                f"'{expected_vendor}' (the DeviceVendorID payload's three operand bytes)"
            )

        if mismatches:
            if strict_multi:
                failures.extend(mismatches)
            else:
                warnings.extend(mismatches)
            # Recorded as information rather than as a success: the entry is present but its
            # details did not verify, and the shortfall is already queued above.
            log_info(f"    LA={la:2d}  osdName='{safe_osd_name}'  vendorID='{safe_vendor_id}'")
        else:
            log_success(
                f"  ✓ LA={la:2d}  osdName='{safe_osd_name}'  vendorID='{safe_vendor_id}'"
            )

    # The bootstrap audio system is named explicitly as well, because it is the one peer whose
    # absence invalidates every ARC, audio-routing and active-source flow downstream, and saying
    # so by name is worth more in a run log than deducing it from the loop above. The loop
    # already fails on it, so this only adds the reason.
    if BOOTSTRAP_LOGICAL_ADDRESS not in la_map:
        failures.append(
            f"LA={BOOTSTRAP_LOGICAL_ADDRESS} "
            f"({expected_devices[BOOTSTRAP_LOGICAL_ADDRESS][0]}): bootstrap device missing"
        )

    for w in warnings:
        log_warning(f"Init_Devicelist_Populate Note ⚠: {w}")

    if failures:
        for f in failures:
            log_error(f"Init_Devicelist_Populate Failed ❌: {f}")
        return False

    # FINAL POSTCONDITION: HDMI-CEC IS STILL ENABLED AT THE MOMENT SUCCESS IS DECLARED.
    #
    # Step 1 gated on the enable, but that was twenty seconds and a seed ladder ago. What the
    # 33 cases downstream rely on is the state as it stands when this function returns True,
    # not the state it once had, and this module's own recovery path drives setEnabled(false)
    # on the way to the toggle. Re-reading here costs one JSON-RPC round trip and makes the
    # returned True mean exactly what every case assumes it means. The comparison is against
    # the boolean True by identity for the same reason as in Step 1: a missing member, a
    # transport sentinel or the string "true" must not be mistaken for an enabled plugin.
    final_enabled_state = _get_enabled_state()
    if final_enabled_state is not True:
        log_error(
            "Init_Devicelist_Populate Failed ❌: the device list verified, but HDMI-CEC no "
            f"longer reads back as enabled (getEnabled returned {final_enabled_state!r}). "
            "Reporting success here would hand every test case in this suite a precondition "
            "that does not hold"
        )
        return False

    elapsed_time = time.perf_counter() - start_time
    if strict_multi:
        msg = (
            "Init_Devicelist_Populate Passed ✅  Strict mode: all "
            f"{len(expected_devices)} devices verified"
        )
    else:
        msg = (
            "Init_Devicelist_Populate Passed ✅  Bootstrap mode: device list ready "
            f"(set {STRICT_MULTI_ENV}=1 for full {len(expected_devices)}-device enforcement)"
        )
    log_success(log_with_timing(msg, elapsed_time))
    return True
