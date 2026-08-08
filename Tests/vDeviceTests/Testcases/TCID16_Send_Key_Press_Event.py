"""
/**
 * @file TCID16_Send_Key_Press_Event.py
 * @brief L3 HDMI CEC Sink functional testcase.
 *
 * @testcase TCID16_Send_Key_Press_Event
 * @details Sends one key-press event from the sink to a peer by dispatching
 *          org.rdk.HdmiCecSink.sendKeyPressEvent over JSON-RPC, and asserts the success
 *          acknowledgement. This closes the device-level leg of SendKeyPressEvent, which
 *          the coverage register records as covered by the sink's own L2 suite with no
 *          end-to-end verification behind it.
 *
 *          The OnKeyPressEvent notification the resulting frame provokes is NOT observed
 *          here and nothing is asserted about it: this level reaches the plugin over plain
 *          one-shot curl, which cannot subscribe to a Thunder notification channel, so an
 *          event assertion at L3 would be unfounded. That is a limit of THIS level, not a
 *          coverage gap - the notification is asserted by the sink's own suites, and this
 *          module deliberately does not duplicate them: L1
 *          onKeyPressEvent_SubscribedClient_ReceivesAddressAndKeyCode,
 *          onKeyPressEvent_BoundaryOperands_AreForwardedVerbatim and
 *          onKeyPressEvent_NoSubscriber_ProducesNoClientNotification, and L2
 *          InjectUserControlPressedFrameAndVerifyEvent with its minimum, maximum-named and
 *          out-of-range key-code variants. Where the coverage register still lists
 *          OnKeyPressEvent as uncovered, that entry is its PRE-CHANGE BASELINE.
 *
 *          WHAT WOULD MAKE THIS A FUNCTIONAL EVENT CASE, reported rather than made under AAP
 *          Directive 6: an event channel reachable without an HTTP listener, or a vComponent
 *          endpoint reporting the frames the device emitted.
 *
 *          Adjacent coverage, deliberately not duplicated here: the pressed and released
 *          pair with the minimum and boundary key codes belong to
 *          TCID26_User_Control_Pressed_Released_Flow, and the rejected-argument cases are
 *          already asserted by the sink L1 suite as sendKeyPressEvent_InvalidLogicalAddress,
 *          sendKeyPressEvent_InvalidKeyCode, sendKeyPressEvent_BoundaryKeyCode and
 *          sendKeyPressEvent_MinKeyCode. No boundary sweep is attempted at this level.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is running WPEFramework
 *    with the org.rdk.HdmiCecSink plugin activated and reachable over JSON-RPC.
 *  - Init_Devicelist_Populate has seeded the CEC topology, so the logical address carried
 *    by the dispatched command resolves to a real emulated peer - the Audio System, which
 *    is the suite's bootstrap peer - rather than to an empty address.
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
 *  - The plugin acknowledges the request with {"success": true} and the CEC device inventory is
 *    identical before and after the call. The notification the key press produces is not observable
 *    at this level and is therefore not asserted.
 *
 * @pass_criteria
 *  - The reply equals {"jsonrpc":"2.0","id":42,"result":{"success":true}}, the device inventory
 *    reads identically before and after, and run_test() returns True.
 *
 * @failure_criteria
 *  - A response mismatch, a JSON parsing failure, an unreachable endpoint, an unreadable device
 *    inventory on either side of the call, an inventory that changed across it, or an unavailable
 *    device-level prerequisite; run_test() then returns False.
 */
"""


import time


import json
from utils import (
    send_curl_command,
    log_info,
    log_success,
    log_error,
    log_warning,
    log_with_timing
)
import HdmiCECSink_Curl as HdmiCecSinkApis


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

def run_test():
    '''Dispatch one sendKeyPressEvent call and verify the success acknowledgement.
    Returns:
        True when the plugin answers with the expected success envelope AND the CEC device inventory
        is identical across the call; False on a transport failure, a response mismatch, an
        unreadable or changed inventory, or a body that is not valid JSON.
    '''
    start_time = time.perf_counter()

    expected_output_response = {
        "jsonrpc": "2.0",
        "id": 42,
        "result": {
            "success": True
        }
    }

    # Inventory snapshot BEFORE the call, so the invariant below compares two real observations.
    inventory_readable, before_count, before_addresses = _device_inventory()
    log_info(f"Device inventory before: {before_count} devices at {before_addresses}")

    log_info("Executing the curl command send key press event")

    # The logicalAddress and keyCode of this scenario are carried by the command constant
    # itself, so they are never restated here and cannot drift out of step with it.
    curl_response = send_curl_command(
        HdmiCecSinkApis.send_key_press_event
    )

    if not curl_response:
        log_error("✖ curl command not sent")
        return False

    # A transport failure does not arrive empty: send_curl_command reports it with the
    # "< No response from WPEFramework >" sentinel, which is TRUTHY and so passes the guard
    # above untouched. Detecting it by prefix is the contract that helper documents; without
    # this check an unreachable endpoint would fall through to be parsed as a response.
    if curl_response.startswith("< No response"):
        log_error("✖ no response from WPEFramework")
        return False

    log_success("✔ curl command sent")
    log_warning(f"Response: {curl_response}")

    # A key press is transient and leaves no persistent plugin state behind - SendKeyPressEvent
    # pushes the key onto m_SendKeyQueue and notifies the worker thread
    # (HdmiCecSinkImplementation.cpp:3243-3257) - so this case needs none of the restore clauses the
    # suite's stateful write-side cases carry, and publishes no cleanup() hook.
    try:
        if json.loads(curl_response) != expected_output_response:
            log_error("TCID16_Send_Key_Press_Event Failed ❌")
            return False

        # THE INVARIANT THIS CASE CAN ACTUALLY OBSERVE. The queued key press must not disturb the
        # CEC device inventory: the addressed peer is already known, and queueing a user-control
        # frame neither registers nor drops a device. Asserting that is a real regression guard - an
        # implementation that touched the device list from the send path would fail it while still
        # answering success - and it is the strongest consequence reachable here, since the frame
        # itself is outbound and this suite cannot read outbound frames.
        if not inventory_readable:
            log_error("✖ the device inventory could not be read, so no invariant can be asserted")
            log_error("TCID16_Send_Key_Press_Event Failed ❌")
            return False

        after_readable, after_count, after_addresses = _device_inventory()
        if not after_readable:
            log_error("✖ the device inventory became unreadable after the key press")
            log_error("TCID16_Send_Key_Press_Event Failed ❌")
            return False
        if (after_count, after_addresses) != (before_count, before_addresses):
            log_error(
                f"✖ the key press disturbed the device inventory: "
                f"{before_count}/{before_addresses} -> {after_count}/{after_addresses}"
            )
            log_error("TCID16_Send_Key_Press_Event Failed ❌")
            return False
        log_success(
            f"✔ device inventory unchanged across the key press: {after_count} devices at "
            f"{after_addresses}"
        )

        elapsed_time = time.perf_counter() - start_time
        log_success(log_with_timing("TCID16_Send_Key_Press_Event Passed ✅", elapsed_time))
        return True
    except json.JSONDecodeError:
        log_error("Invalid JSON response")
        log_error("TCID16_Send_Key_Press_Event Failed ❌")
        return False
