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
 *          ROLE INVERSION - the single thing most easily got wrong in this directory. The
 *          device under test is the SINK: the television itself, which owns CEC logical
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
 *  - getDeviceList reports success, a device count at or above the minimum the active mode
 *    requires, and the seeded source-role peers carrying their OSD names and vendor
 *    identifiers. HDMI-CEC is left ENABLED, which is the post-condition every following test
 *    case is written against.
 *
 * @pass_criteria
 *  - run_test() returns True after the bootstrap audio-system peer at logical address 5 has
 *    been observed in getDeviceList and every device the active mode requires has verified.
 *
 * @failure_criteria
 *  - Plugin activation fails, a vComponent command is rejected, the middleware never learns
 *    the bootstrap peer, getDeviceList is unreachable or unparsable, or a strict-mode device
 *    is absent, reports the wrong OSD name, or reports an empty vendor identifier.
 */
"""

import json
import os
import time

from utils import (
    send_curl_command,
    send_vcomponent_command,
    HDMICEC_CMD_BASE,
    activate_plugin,
    WPEFRAMEWORK_JSONRPC_URL,
    log_info,
    log_success,
    log_warning,
    log_error,
    log_with_timing,
)
import HdmiCECSink_Curl as HdmiCecSinkApis


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
# (logical address, expected osdName, ReportPhysicalAddress payload, SetOSDName payload,
#  DeviceVendorID payload). The bootstrap peer is listed first for readability only; it is
# selected below by address, so the order carries no meaning.
#
# The addresses follow the CEC device-type map, so each peer occupies an address its role can
# actually hold: 5 is the audio system, 4 and 8 are Playback Device 1 and 2, 3 is Tuner 1, and
# 1 is Recording Device 1. Address 0 is the television - the device under test - and is
# deliberately absent.
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
        BOOTSTRAP_RPA_YAML,
        "DeviceListConfig/Payload_Set_OSD_Name_YAMAHA.yaml",
        "DeviceListConfig/Payload_Vendor_ID_YAMAHA.yaml",
    ),
    # Playback Device 1 - the active-source candidate the routing flows switch to.
    (
        4,
        "SONY",
        "DeviceListConfig/Payload_Report_Physical_Address_SONY.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_SONY.yaml",
        "DeviceListConfig/Payload_Vendor_ID_SONY.yaml",
    ),
    # Playback Device 2 - second playback peer, so a route change has somewhere to go.
    (
        8,
        "PANASONIC",
        "DeviceListConfig/Payload_Report_Physical_Address_PANASONIC.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_PANASONIC.yaml",
        "DeviceListConfig/Payload_Vendor_ID_PANASONIC.yaml",
    ),
    # Tuner 1.
    (
        3,
        "SAMSUNG",
        "DeviceListConfig/Payload_Report_Physical_Address_SAMSUNG.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_SAMSUNG.yaml",
        "DeviceListConfig/Payload_Vendor_ID_SAMSUNG.yaml",
    ),
    # Recording Device 1.
    (
        1,
        "DENON",
        "DeviceListConfig/Payload_Report_Physical_Address_DENON.yaml",
        "DeviceListConfig/Payload_Set_OSD_Name_DENON.yaml",
        "DeviceListConfig/Payload_Vendor_ID_DENON.yaml",
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

# Secondary seed path. Instead of injecting the peer's announcements, these ask the configured
# peer to answer for itself, so the middleware learns it from a genuine reply. Used only when
# the injected triplet has not produced the address.
GIVE_COMMAND_YAMLS = (
    "Device_Give_Physical_Address.yaml",
    "Device_Give_OSD_Name.yaml",
    "Device_Give_Device_Vendor_ID.yaml",
)

# How long the middleware's own poll-based discovery is given before seeding begins, and how
# often it is sampled while waiting.
DISCOVERY_TIMEOUT_SECONDS = 20.0
DISCOVERY_POLL_SECONDS = 1.0

# Attempts allowed for the bootstrap seed ladder and for each secondary peer.
MAX_SEED_ATTEMPTS = 3

# Environment gates, documented in README.txt.
#
#   Init_Devicelist_Populate_STRICT_MULTI = "1"
#       every peer in PEER_SEEDS must be present with its expected osdName and a non-empty
#       vendorID, and the device count must reach len(PEER_SEEDS).
#   anything else (bootstrap mode)
#       only the bootstrap peer is mandatory; each other shortfall is reported as a note, and
#       the device count must reach Init_Devicelist_Populate_MIN_DEVICES, default 1.
STRICT_MULTI_ENV = "Init_Devicelist_Populate_STRICT_MULTI"
MIN_DEVICES_ENV = "Init_Devicelist_Populate_MIN_DEVICES"


# ── helpers ──────────────────────────────────────────────────────────────────

def _post(yaml_name):
    """POST a vcomponent command YAML; returns True on HTTP 200.

    The name is joined onto HDMICEC_CMD_BASE so that a deployment can retarget the whole
    fixture set with one environment variable. Anything other than 200 - including the 0 that
    utils reports for a missing document, a refused path or a curl failure - is a rejection,
    and the body is logged verbatim so the reason is visible rather than inferred.
    """
    http_code, body = send_vcomponent_command(f"{HDMICEC_CMD_BASE}/{yaml_name}")
    log_info(f"  POST {yaml_name}: HTTP {http_code}  {body}")
    return http_code == 200


def _get_device_list():
    """Call getDeviceList and return the parsed result dict, or None on error.

    The "< No response" prefix is the byte-exact sentinel utils.send_curl_command returns for
    a transport failure; testing for it here is what keeps an unreachable device a clean None
    instead of a JSON decode error further up.
    """
    response = send_curl_command(HdmiCecSinkApis.get_device_list)
    if not response or response.startswith("< No response"):
        return None
    try:
        body = json.loads(response)
        return body.get("result")
    except json.JSONDecodeError:
        return None


def _set_enabled_true():
    """Enable HdmiCecSink plugin; returns True when API reports success."""
    response = send_curl_command(HdmiCecSinkApis.set_enabled_true)
    if not response or response.startswith("< No response"):
        return False
    try:
        body = json.loads(response)
        result = body.get("result", {})
        return result.get("success") is True
    except json.JSONDecodeError:
        return False


def _set_enabled_false():
    """Disable HdmiCecSink plugin; returns True when API reports success.

    Used only as the first half of the enable toggle below. This module's post-condition is an
    ENABLED plugin, so a call here is always followed by a call to _set_enabled_true().
    """
    response = send_curl_command(HdmiCecSinkApis.set_enabled_false)
    if not response or response.startswith("< No response"):
        return False
    try:
        body = json.loads(response)
        result = body.get("result", {})
        return result.get("success") is True
    except json.JSONDecodeError:
        return False


def _get_enabled_state():
    """Return plugin enabled state as bool, or None on parse/transport error.

    A non-boolean or absent member yields None rather than a coerced False, so "the device did
    not tell us" stays distinguishable from "the device said no".
    """
    response = send_curl_command(HdmiCecSinkApis.get_enabled)
    if not response or response.startswith("< No response"):
        return None
    try:
        body = json.loads(response)
        result = body.get("result", {})
        enabled = result.get("enabled")
        return enabled if isinstance(enabled, bool) else None
    except json.JSONDecodeError:
        return None


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


def _inject_triplet(rpa_yaml, osd_yaml, vid_yaml, inter_cmd_delay=0.35):
    """Inject ReportPhysicalAddress + SetOSDName + DeviceVendorID for one device.

    Posted in that order because the first frame is what registers the device; the two that
    follow fill in fields on an entry that already exists. The first rejection stops the
    sequence, so a missing fixture is reported against the document that is actually absent
    rather than against whichever frame happened to be posted last.
    """
    for yaml_name in (rpa_yaml, osd_yaml, vid_yaml):
        if not _post(yaml_name):
            return False
        time.sleep(inter_cmd_delay)
    return True


def _wait_for_device(la, expected_name, timeout_s=3.0, poll_s=0.4):
    """Wait until getDeviceList shows expected LA with expected OSD name.

    Returns (found, entry, last_result). Stricter than _wait_for_la: the address alone is not
    enough, the OSD name must have arrived too, which is the condition a caller needs when it
    is about to assert on the name.
    """
    deadline = time.time() + timeout_s
    last_result = None
    while time.time() < deadline:
        result = _get_device_list()
        last_result = result
        if result and result.get("success") is True:
            la_map = _build_la_map(result.get("deviceList", []))
            entry = la_map.get(la)
            if entry and entry.get("osdName") == expected_name:
                return True, entry, result
        time.sleep(poll_s)
    return False, None, last_result


def _wait_for_la(la, timeout_s=3.0, poll_s=0.4):
    """Wait until getDeviceList includes LA; returns (found, entry, result).

    Presence of the address is the registration signal - addDevice() has run - and is
    deliberately weaker than _wait_for_device: the OSD name and vendor identifier are filled
    in by later frames and may still be empty at this point.
    """
    deadline = time.time() + timeout_s
    last_result = None
    while time.time() < deadline:
        result = _get_device_list()
        last_result = result
        if result and result.get("success") is True:
            la_map = _build_la_map(result.get("deviceList", []))
            entry = la_map.get(la)
            if entry is not None:
                return True, entry, result
        time.sleep(poll_s)
    return False, None, last_result


def _seed_device(la, expected_name, rpa_yaml, osd_yaml, vid_yaml, attempts=3):
    """Seed one device into deviceList with retries; returns True when LA appears.

    A rejected injection is retried rather than treated as fatal, because a secondary peer
    that never appears degrades the device list without invalidating the suite - only the
    bootstrap peer is mandatory. The caller decides what a False means for it.
    """
    for attempt in range(1, attempts + 1):
        log_info(f"  Seed LA={la} ({expected_name}) attempt {attempt}/{attempts}")
        if not _inject_triplet(rpa_yaml, osd_yaml, vid_yaml):
            log_warning(f"  Seed warning: injection rejected for LA={la} ({expected_name})")
            continue

        found, entry, _ = _wait_for_la(la, timeout_s=3.0, poll_s=0.4)
        if found:
            log_success(
                f"  ✓ Seed learned LA={la:2d}  osdName='{entry.get('osdName', '')}'"
                f"  vendorID='{entry.get('vendorID', '')}'"
            )
            return True

    log_warning(
        f"  Seed warning: LA={la} ({expected_name}) did not appear after {attempts} attempts"
    )
    return False



# ── test ─────────────────────────────────────────────────────────────────────

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
    # Align with SuitManager startup guard to let CEC threads fully initialize.
    time.sleep(6)

    # ── Step 1: configure ────────────────────────────────────────────────────
    log_info(
        "Init_Devicelist_Populate Step 1: configure vcomponent network of source-role peers"
    )
    if not _post(NETWORK_CONFIG_YAML):
        log_error("Init_Devicelist_Populate Failed ❌: configure command rejected")
        return False

    # Ensure middleware poll/discovery threads are enabled in this runtime.
    if not _set_enabled_true():
        log_warning(
            "Init_Devicelist_Populate Note ⚠: setEnabled(true) did not report success; "
            "attempting toggle"
        )
        _set_enabled_false()
        if not _set_enabled_true():
            log_warning(
                "Init_Devicelist_Populate Note ⚠: toggle enable sequence did not report success"
            )

    enabled_state = _get_enabled_state()
    log_info(f"  HdmiCecSink enabled={enabled_state}")
    if enabled_state is not True:
        # Reported, not repaired: the toggle above is the only recovery this module owns, and
        # its post-condition is an ENABLED plugin. Saying so here means the Step 2 or Step 3
        # failure that follows is diagnosable instead of surprising.
        log_warning(
            "Init_Devicelist_Populate Note ⚠: HDMI-CEC did not read back as enabled, so the "
            "middleware's discovery threads will not populate the device list and the seed "
            "ladder below is the only remaining path"
        )

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
    deadline = time.time() + DISCOVERY_TIMEOUT_SECONDS
    last_la_map = {}
    while time.time() < deadline:
        result = _get_device_list()
        if result and result.get("success") is True:
            last_la_map = _build_la_map(result.get("deviceList", []))
            found_las = sorted(last_la_map.keys())
            log_info(f"  Polling: discovered LAs={found_las}")
            # Step 2 is considered ready once the bootstrap audio system is present.
            if BOOTSTRAP_LOGICAL_ADDRESS in last_la_map:
                break
        time.sleep(DISCOVERY_POLL_SECONDS)

    bootstrap_la, bootstrap_name, bootstrap_rpa, bootstrap_osd, bootstrap_vid = BOOTSTRAP_PEER

    # Fallback path: some builds do not auto-discover from poll within timeout. Seed the
    # bootstrap peer explicitly so a usable non-empty list is still guaranteed.
    if bootstrap_la not in last_la_map:
        log_warning(
            "Init_Devicelist_Populate Step 2 fallback: auto-discovery incomplete, "
            f"injecting LA={bootstrap_la} seed frames"
        )
        for attempt in range(1, MAX_SEED_ATTEMPTS + 1):
            log_info(f"  Seed LA={bootstrap_la} attempt {attempt}/{MAX_SEED_ATTEMPTS}")
            ok_triplet = _inject_triplet(bootstrap_rpa, bootstrap_osd, bootstrap_vid)
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
                    f"  osdName='{entry.get('osdName', '')}'"
                    f"  vendorID='{entry.get('vendorID', '')}'"
                )
                break

            # Secondary seed path: ask the configured peer to answer for itself, so the
            # middleware learns it from a genuine reply rather than an injected announcement.
            log_info(f"  Seed fallback: send Give* commands to LA={bootstrap_la}")
            for yaml_name in GIVE_COMMAND_YAMLS:
                if not _post(yaml_name):
                    log_warning(f"  Seed fallback warning: {yaml_name} rejected")
                time.sleep(0.25)

            found, entry, _ = _wait_for_la(bootstrap_la, timeout_s=3.0, poll_s=0.4)
            if found:
                log_success(
                    f"  ✓ Give* learned LA={bootstrap_la:2d}"
                    f"  osdName='{entry.get('osdName', '')}'"
                    f"  vendorID='{entry.get('vendorID', '')}'"
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
    log_info(
        "Init_Devicelist_Populate Step 2b: seed remaining configured source-role peers"
    )
    for la, expected_name, rpa_yaml, osd_yaml, vid_yaml in REMAINING_PEERS:
        _seed_device(
            la, expected_name, rpa_yaml, osd_yaml, vid_yaml, attempts=MAX_SEED_ATTEMPTS
        )

    # Give the middleware a short settle window before the final snapshot.
    time.sleep(1.0)

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

    log_warning(f"  success={success}  numberofdevices={num_devices}  devices={device_list}")

    if success is not True:
        log_error("Init_Devicelist_Populate Failed ❌: getDeviceList success != true")
        return False

    strict_multi = os.environ.get(STRICT_MULTI_ENV, "0") == "1"
    if strict_multi:
        min_devices_required = len(PEER_SEEDS)
    else:
        # Read only in bootstrap mode; strict mode's minimum is the full peer count and is not
        # overridable. A non-numeric override is reported and replaced by the documented
        # default rather than raised, because run_test() owes its caller a bool on every path.
        raw_minimum = os.environ.get(MIN_DEVICES_ENV, "1")
        try:
            min_devices_required = int(raw_minimum)
        except ValueError:
            log_warning(
                f"Init_Devicelist_Populate Note ⚠: {MIN_DEVICES_ENV}='{raw_minimum}' is not "
                "an integer; using the default of 1"
            )
            min_devices_required = 1

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
    expected_devices = {la: expected_name for la, expected_name, _, _, _ in PEER_SEEDS}

    # Build lookup: logicalAddress -> entry
    la_map = _build_la_map(device_list)

    failures = []
    warnings = []

    for la, expected_name in expected_devices.items():
        entry = la_map.get(la)
        if entry is None:
            if strict_multi:
                failures.append(f"LA={la} ({expected_name}): not in deviceList")
            else:
                warnings.append(f"LA={la} ({expected_name}): not in deviceList")
            continue

        osd_name = entry.get("osdName", "")
        vendor_id = entry.get("vendorID", "")

        mismatches = []
        if osd_name != expected_name:
            mismatches.append(f"LA={la}: osdName='{osd_name}', expected '{expected_name}'")
        if not vendor_id:
            mismatches.append(f"LA={la} ({expected_name}): vendorID is empty")

        if mismatches:
            if strict_multi:
                failures.extend(mismatches)
            else:
                warnings.extend(mismatches)
            # Recorded as information rather than as a success: the entry is present but its
            # details did not verify, and the shortfall is already queued above.
            log_info(f"    LA={la:2d}  osdName='{osd_name}'  vendorID='{vendor_id}'")
        else:
            log_success(f"  ✓ LA={la:2d}  osdName='{osd_name}'  vendorID='{vendor_id}'")

    # The bootstrap audio system is mandatory in BOTH modes: without it nothing downstream
    # that exercises ARC, audio routing or the active source has a counterpart to talk to.
    if BOOTSTRAP_LOGICAL_ADDRESS not in la_map:
        failures.append(
            f"LA={BOOTSTRAP_LOGICAL_ADDRESS} "
            f"({expected_devices[BOOTSTRAP_LOGICAL_ADDRESS]}): bootstrap device missing"
        )

    for w in warnings:
        log_warning(f"Init_Devicelist_Populate Note ⚠: {w}")

    if failures:
        for f in failures:
            log_error(f"Init_Devicelist_Populate Failed ❌: {f}")
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

