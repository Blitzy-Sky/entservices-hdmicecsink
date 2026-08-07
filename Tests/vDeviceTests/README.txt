To execute the cases inside qemu

cd /tmp

git clone git@github.com:rdkcentral/entservices-hdmicecsink.git

cd entservices-hdmicecsink/Tests/vDeviceTests

EXECUTION:
with time : python3 SuitManager.py -t hdmicecsink
without time: python3 SuitManager.py hdmicecsink

Default Actions:
Plugin activation is now done by default before suite execution:
- hdmicecsink -> Controller.1.activate(callsign=org.rdk.HdmiCecSink)
- Init_Devicelist_Populate will be done.

Disable default activation only if needed:
- export AUTO_ACTIVATE_PLUGINS=0


If the testcases fail with "connection refused", configure endpoint host/ports before running.

Defaults used by the tests:
- MW JSON-RPC: http://127.0.0.1:9998/jsonrpc
- vComponent API: http://127.0.0.1:8080/api/postKVP

Useful overrides:
- TARGET_HOST (applies to both endpoints)
- JSONRPC_PORT
- VCOMPONENT_PORT
- WPEFRAMEWORK_JSONRPC_URL (full URL, highest priority)
- VCOMPONENT_API_URL (full URL, highest priority)

Examples:

# when running directly inside QEMU guest (services on localhost)
python3 SuitManager.py hdmicecsink

# when running from host against QEMU target IP
export TARGET_HOST=192.168.1.50
export JSONRPC_PORT=9998
export VCOMPONENT_PORT=8080
python3 SuitManager.py hdmicecsink

# full URL override form
export WPEFRAMEWORK_JSONRPC_URL=http://192.168.1.50:9998/jsonrpc
export VCOMPONENT_API_URL=http://192.168.1.50:8080/api/postKVP
python3 SuitManager.py hdmicecsink


Troubleshooting:
- If you see connection errors, verify WPEFramework JSON-RPC and the vComponent API are reachable using the endpoint overrides above.

Initialization gates:
Init_Devicelist_Populate runs once, before the first test case, and reads two environment
variables. Both are optional, and no other module in this suite reads either of them.
- Init_Devicelist_Populate_STRICT_MULTI=1
  Require every seeded peer to be present in the device list with its expected OSD name and
  a non-empty vendor ID, and require the device count to reach the number of seeded peers.
  Any shortfall fails initialization, and a failed initialization aborts the suite before
  the first test case runs.
- Init_Devicelist_Populate_MIN_DEVICES
  Minimum device count accepted in the default bootstrap mode. Default 1. In bootstrap mode
  only the bootstrap peer is mandatory and every other shortfall is reported as a note
  rather than a failure. Not consulted when Init_Devicelist_Populate_STRICT_MULTI=1.

Prerequisites:
- A QEMU target, or a real sink device, running WPEFramework with the org.rdk.HdmiCecSink
  plugin available for activation.
- A reachable WPEFramework JSON-RPC endpoint on port 9998.
- A reachable vComponent HTTP API on port 8080, with this suite's vcomponent_configurations/
  tree applied.
- python3 and curl on PATH.

The device under test is the sink: the television itself, which owns CEC logical address 0.
Every virtual CEC peer this suite configures is therefore a source-role device sitting
beneath that television - an audio system, two playback devices, a tuner and a recording
device. No virtual television is created, and the device under test keeps its sink role
throughout; it is never reconfigured to stand in for a peer of its own.

Status:
AUTHORED, NOT EXECUTED.

This suite is authored to full specification. It has not been run, on a device or anywhere
else, and no result from it is reported anywhere.

Static validation applied to the suite:
- python3 -m py_compile over every module in this directory and over every module under
  Testcases/.
- A suite-manager registration check of the tests list in SuitManager.py against the test
  case modules on disk under Testcases/, applied in both directions, so that neither a
  registered module missing from disk nor an unregistered module on disk goes unnoticed.
- YAML well-formedness parsing of every document under vcomponent_configurations/.

Prerequisites that were not available, and so were not used:
- A QEMU target.
- A WPEFramework JSON-RPC endpoint on port 9998.
- A vComponent API on port 8080.
- The Python RAFT packages (python_raft, ut-raft), which are deliberately not installed.

No service was started on port 9998 or on port 8080, no QEMU target was launched, and no
transport was stubbed in order to produce a result. Runtime validation of this suite is
deferred until a proper device or emulator environment is available.
