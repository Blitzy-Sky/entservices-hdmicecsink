"""
/**
 * @file HdmiCECSink_Curl.py
 * @brief Provides reusable curl commands for HDMI-CEC Sink JSON-RPC APIs.
 *
 * @testcase HdmiCECSink_Curl
 * @details Defines deterministic command strings consumed by the HDMI-CEC Sink
 *          device-level test cases. This module authors commands for external
 *          execution and does not start or emulate their required services.
 *
 * @precondition
 *  - A reachable WPEFramework JSON-RPC endpoint is configured through utils.py.
 *  - The device under test hosts an active org.rdk.HdmiCecSink plugin.
 *  - The device and CEC topology required by each consuming test case are available.
 *
 * @dependencies
 *  - utils.py supplies the shared WPEFRAMEWORK_JSONRPC_URL endpoint.
 *  - SuitManager.py and the sink test cases consume the constants defined here.
 *
 * @expected_result
 *  - Importers receive well-formed curl command strings for the sink JSON-RPC APIs.
 *
 * @pass_criteria
 *  - Each constant preserves its specified method, payload, timeout, and shared URL.
 *
 * @failure_criteria
 *  - A command has malformed JSON, an incorrect API contract, or unavailable
 *    device-level prerequisites when a consuming test dispatches it.
 */
"""

from utils import WPEFRAMEWORK_JSONRPC_URL


get_active_route = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getActiveRoute"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


get_active_source = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getActiveSource"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


get_audio_device_connected_status = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


get_device_list = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getDeviceList"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


get_enabled = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getEnabled"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


get_osd_name = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getOSDName"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


get_vendor_id = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getVendorId"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


get_cec_version = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getCecVersion"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


print_device_list = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.printDeviceList"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


request_active_source = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.requestActiveSource"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


request_short_audio_descriptor = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.requestShortAudioDescriptor"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


request_audio_device_power_status = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.requestAudioDevicePowerStatus"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


send_audio_device_power_on_message = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.sendAudioDevicePowerOnMessage"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


send_get_audio_status_message = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.sendGetAudioStatusMessage"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


send_standby_message = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.sendStandbyMessage"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_active_source = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setActiveSource"}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


send_key_press_event = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.sendKeyPressEvent",'
    '"params":{"logicalAddress":4,"keyCode":65}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


send_user_control_pressed = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.sendUserControlPressed",'
    '"params":{"logicalAddress":4,"keyCode":65}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


send_user_control_released = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.sendUserControlReleased",'
    '"params":{"logicalAddress":4}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_active_path = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setActivePath",'
    '"params":{"activePath":"1.0.0.0"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_enabled_true = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setEnabled",'
    '"params":{"enabled":true}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_enabled_false = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setEnabled",'
    '"params":{"enabled":false}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_menu_language = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setMenuLanguage",'
    '"params":{"language":"eng"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_osd_name = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setOSDName",'
    '"params":{"name":"Sky TV"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_routing_change = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setRoutingChange",'
    '"params":{"oldPort":"HDMI0","newPort":"HDMI1"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


setup_arc_routing_true = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setupARCRouting",'
    '"params":{"enabled":true}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


setup_arc_routing_false = (
    'curl --max-time 8 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setupARCRouting",'
    '"params":{"enabled":false}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_vendor_id = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setVendorId",'
    '"params":{"vendorid":"0x0019FB"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_latency_info = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setLatencyInfo",'
    '"params":{"videoLatency":"2","lowLatencyMode":"1",'
    '"audioOutputCompensated":"1","audioOutputDelay":"20"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_vendor_id_invalid = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setVendorId",'
    '"params":{"vllendorid":"0x0019FB"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


set_osd_name_invalid = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setOSDName",'
    '"params":{"nnamme":"Sky TV"}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)


setup_arc_routing_invalid = (
    'curl --max-time 5 '
    '--header "Content-Type: application/json" '
    '--request POST '
    '-d \'{"jsonrpc":"2.0","id":42,'
    '"method":"org.rdk.HdmiCecSink.setupARCRouting",'
    '"params":{"ennabled":true}}\' '
    + WPEFRAMEWORK_JSONRPC_URL
)
