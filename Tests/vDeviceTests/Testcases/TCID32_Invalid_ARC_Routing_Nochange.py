"""
/**
 * @file TCID32_Invalid_ARC_Routing_Nochange.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID32_Invalid_ARC_Routing_Nochange
 * @details Validates that a MALFORMED org.rdk.HdmiCecSink.setupARCRouting request leaves the
 *          sink's reported audio-device connection state unchanged: read the status, dispatch
 *          the malformed request, read again, compare. The malformation - {"ennabled": true}
 *          for {"enabled": true} - lives in HdmiCECSink_Curl.setup_arc_routing_invalid alone.
 *          TCID20_ARC_Initiation_Flow and TCID21_ARC_Termination_Flow own the well-formed ARC
 *          paths; this module owns the malformed-parameter path - their negative half.
 *
 *          WHAT IS NOT CLAIMED. ARC routing state has no getter: setupARCRouting publishes
 *          only a success flag, and the state it moves is reported outward through the
 *          arcInitiationEvent / arcTerminationEvent notifications, which reach registered
 *          Thunder subscribers rather than a one-shot curl request. This module neither
 *          observes nor claims that handshake, and does not pin connected to a literal - that
 *          value follows the emulated topology. Equality of the two reads is order-tolerant.
 *
 * @precondition
 *  - The org.rdk.HdmiCecSink plugin is active and reachable over the JSON-RPC endpoint.
 *  - HDMI-CEC is enabled and Init_Devicelist_Populate has run, so the topology is seeded.
 *  - TCID21_ARC_Termination_Flow left ARC neutral; this case neither reads nor changes that.
 *  - AUTHORED, NOT EXECUTED in this repository: no workflow here runs this suite.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - SuitManager.py
 *  - vcomponent_configurations/commands/*.yaml (for emulation-based scenarios)
 *
 * @expected_result
 *  - The malformed request is rejected or ignored, and the audio-device connection status
 *    read after it equals the status read before it.
 *
 * @pass_criteria
 *  - Both reads parse, their result.connected values are present and equal, and run_test()
 *    returns True.
 *
 * @failure_criteria
 *  - Either read is not dispatched or returns the no-response sentinel, either body fails to
 *    parse, result.connected is absent, the state differs, or run_test() returns False.
 */
"""

import time
import os
import json
from utils import send_curl_command, log_success, log_error, log_warning
import HdmiCECSink_Curl as HdmiCecSinkApis


def run_test():
    start_time = time.perf_counter()

    # Legacy intent: setupARCRouting with a malformed parameter must not alter ARC state.
    before_status = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    send_curl_command(HdmiCecSinkApis.setup_arc_routing_invalid)
    after_status = send_curl_command(HdmiCecSinkApis.get_audio_device_connected_status)
    # Restoration, deliberately last: ARC is driven to the known-off state that
    # TCID21_ARC_Termination_Flow left, and every guard below is reached only AFTER it, so no
    # early return leaks into TCID33. The malformed reply is deliberately not captured, since
    # pinning a plugin's error-envelope-or-acknowledgement choice tests the reply, not this.
    send_curl_command(HdmiCecSinkApis.setup_arc_routing_false)

    # Each guard covers BOTH reads; the malformed write is guarded by neither, deliberately.
    if not before_status or not after_status:
        log_error("✖ getAudioDeviceConnectedStatus command not sent")
        return False
    # The sentinel is a non-empty string, so the falsy check above cannot catch it.
    if before_status.startswith("< No response") or after_status.startswith("< No response"):
        log_error("✖ getAudioDeviceConnectedStatus returned no response from WPEFramework")
        return False

    log_warning(f"ARC status before: {before_status}")
    log_warning(f"ARC status after: {after_status}")
    try:
        before_connected = json.loads(before_status).get("result", {}).get("connected")
        after_connected = json.loads(after_status).get("result", {}).get("connected")
        # `is not None` first: two absent keys both resolve to None and would compare equal.
        if before_connected is not None and after_connected == before_connected:
            elapsed_time = time.perf_counter() - start_time
            msg = "TCID32_Invalid_ARC_Routing_Nochange Passed"
            if os.environ.get("HDMICEC_TIMING_ENABLED"):
                log_success(f"{msg} time consumed: {elapsed_time:.3f}s")
            else:
                log_success(msg)
            return True
    except Exception:
        # Broader than JSONDecodeError on purpose: a non-object result raises on .get(), and
        # either shape failure leaves the invariant unconfirmed - as a mismatch does.
        pass

    log_error("TCID32_Invalid_ARC_Routing_Nochange Failed")
    return False
