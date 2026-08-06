"""
/**
 * @file HdmiCECSink_Curl.py
 * @brief Provides reusable curl argv commands for HDMI-CEC Sink JSON-RPC APIs.
 *
 * @testcase HdmiCECSink_Curl
 * @details Defines deterministic argv command lists consumed by the HDMI-CEC Sink
 *          device-level test cases. Each constant is a list of already-separated
 *          arguments, executed without a shell by utils.send_curl_command, so no quote,
 *          semicolon or $(...) inside a JSON payload or an endpoint is ever interpreted
 *          (CWE-78). Where an API is exercised both with accepted and with rejected
 *          arguments, the two are separate constants - set_vendor_id beside
 *          set_vendor_id_invalid, for instance. This module authors commands for external
 *          execution and does not start or emulate their required services.
 *
 *          The constants are INERT DATA, never shell input. utils.send_curl_command
 *          splits whichever constant it is given into an argv list and executes it with
 *          subprocess.run and no shell, so the WPEFRAMEWORK_JSONRPC_URL appended below -
 *          which comes from the environment - is passed to curl as a single argument and
 *          can never be interpreted as shell syntax. utils.py additionally rejects any
 *          endpoint override carrying whitespace or a shell metacharacter before
 *          publishing it. Do not reintroduce os.popen, os.system or shell=True for these
 *          strings, and do not build a command line from them by concatenation.
 *
 * @precondition
 *  - utils.py resolves WPEFRAMEWORK_JSONRPC_URL to a reachable WPEFramework endpoint.
 *  - The device under test hosts an active org.rdk.HdmiCecSink plugin when a command is
 *    dispatched.
 *
 * @dependencies
 *  - utils.py supplies the shared WPEFRAMEWORK_JSONRPC_URL endpoint and the shell-free
 *    send_curl_command dispatcher that executes these constants as argv lists. It is the
 *    only module this one imports, and the only one it needs today.
 *  - The consumers of these constants - the planned SuitManager.py and the sink
 *    Testcases/TCID*.py modules - are not present in this directory yet; they will draw
 *    their command strings from here when they land.
 *
 * @expected_result
 *  - Importers receive well-formed curl argv lists for the sink JSON-RPC APIs.
 *
 * @pass_criteria
 *  - Each constant preserves its specified method, payload, timeout, and shared URL,
 *    with one argument per list element and the endpoint last.
 *
 * @failure_criteria
 *  - A definition names the wrong method, carries the wrong payload, or its consuming test
 *    dispatches it without the required device-level prerequisites.
 */
"""

from utils import WPEFRAMEWORK_JSONRPC_URL


get_active_route = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getActiveRoute"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


get_active_source = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getActiveSource"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


get_audio_device_connected_status = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getAudioDeviceConnectedStatus"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


get_device_list = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getDeviceList"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


get_enabled = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getEnabled"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


get_osd_name = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getOSDName"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


get_vendor_id = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getVendorId"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


# NEGATIVE-PATH CONSTANT. `org.rdk.HdmiCecSink.getCecVersion` is NOT a registered JSON-RPC
# method, so this command yields a method-not-found error rather than a CEC version. The name
# says so explicitly, because a constant called `get_cec_version` sitting among the working
# constants above reads as a capability and would be picked up by the next person writing a test
# case expecting it to return something.
#
# Four independent confirmations that the method is unregistered:
#   1. absent from the generated registration list in the built
#      interfaces/json/JHdmiCecSink.h (grep for it returns 0 hits);
#   2. absent from the 24 `handler.Exists` assertions in the L1 RegisteredMethods test;
#   3. HdmiCecSinkImplementation::getCecVersion() is a private void internal helper
#      (HdmiCecSinkImplementation.h:743, inside the private block at 589-745), called only
#      from Configure() at HdmiCecSinkImplementation.cpp:824 -- it neither returns a value to
#      a caller nor is wired to the JSON-RPC surface;
#   4. a workspace-wide grep for _T("getCecVersion") finds exactly one site, inside the L1
#      test that remains disabled for precisely this reason.
# The mechanism behind all four: the method is absent from IHdmiCecSink.h's published set and
# therefore from Exchange::JHdmiCecSink::Register, which is the plugin's only JSON-RPC
# registration path.
#
# Where the CEC version IS observable: through the <Give CEC Version> exchange defined in
# vcomponent_configurations/hdmicec/hdmicec_vcomponent_cec_responses.yaml, and in a device
# record from getDeviceList -- never from a getCecVersion call.
#
# It is kept rather than deleted so that the negative case stays available and the analysis
# stays attached to it; the matching retention rationale is recorded above the disabled L1 test
# in ../L1Tests/tests/test_HdmiCecSink.cpp. Retargeting it at a registered method would be
# wrong -- getCecVersion has no registered equivalent. If the plugin ever registers it, rename
# this back and drop this comment.
#
# Argv form, like every constant above: send_curl_command executes without a shell, so the
# payload and the endpoint are passed to curl as single arguments rather than parsed by /bin/sh.
get_cec_version_unregistered = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.getCecVersion"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


print_device_list = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.printDeviceList"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


request_active_source = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.requestActiveSource"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


request_short_audio_descriptor = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.requestShortAudioDescriptor"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


request_audio_device_power_status = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.requestAudioDevicePowerStatus"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


send_audio_device_power_on_message = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.sendAudioDevicePowerOnMessage"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


send_get_audio_status_message = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.sendGetAudioStatusMessage"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


send_standby_message = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.sendStandbyMessage"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_active_source = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setActiveSource"}',
    WPEFRAMEWORK_JSONRPC_URL,
]


# The three key-control commands below address logical address 5, the Audio System.
#
# That is the address the suite's own topology allocates: both
# vcomponent_configurations/hdmicec/hdmicec_vcomponent_configuration.yaml and the device-list
# payloads describe an Audio System peer at 5, and the sink's user-control paths are the ones an
# Audio System answers. Logical address 4 has no peer in this topology, so a command aimed at it
# would be sent to nothing and could not be observed.
send_key_press_event = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.sendKeyPressEvent","params":{"logicalAddress":5,"keyCode":65}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


send_user_control_pressed = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.sendUserControlPressed","params":{"logicalAddress":5,"keyCode":65}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


send_user_control_released = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.sendUserControlReleased","params":{"logicalAddress":5}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_active_path = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setActivePath","params":{"activePath":"1.0.0.0"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_enabled_true = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setEnabled","params":{"enabled":true}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_enabled_false = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setEnabled","params":{"enabled":false}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_menu_language = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setMenuLanguage","params":{"language":"eng"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_osd_name = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setOSDName","params":{"name":"Sky TV"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_routing_change = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setRoutingChange","params":{"oldPort":"HDMI0","newPort":"HDMI1"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


setup_arc_routing_true = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setupARCRouting","params":{"enabled":true}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


setup_arc_routing_false = [
    "curl",
    "--max-time", "8",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setupARCRouting","params":{"enabled":false}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_vendor_id = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setVendorId","params":{"vendorid":"0x0019FB"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_latency_info = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setLatencyInfo","params":{"videoLatency":"2","lowLatencyMode":"1","audioOutputCompensated":"1","audioOutputDelay":"20"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_vendor_id_invalid = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setVendorId","params":{"vllendorid":"0x0019FB"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


set_osd_name_invalid = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setOSDName","params":{"nnamme":"Sky TV"}}',
    WPEFRAMEWORK_JSONRPC_URL,
]


setup_arc_routing_invalid = [
    "curl",
    "--max-time", "5",
    "--header", "Content-Type: application/json",
    "--request", "POST",
    "-d", '{"jsonrpc":"2.0","id":42,"method":"org.rdk.HdmiCecSink.setupARCRouting","params":{"ennabled":true}}',
    WPEFRAMEWORK_JSONRPC_URL,
]
