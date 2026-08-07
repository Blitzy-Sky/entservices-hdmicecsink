"""
/**
 * @file SuitManager.py
 * @brief SuitManager.py
 *
 * @testcase SuitManager
 * @details Orchestrates the HDMI CEC Sink L2/device-level test suite by dynamically loading
 *          and executing test case modules, activating the required RDK plugin via JSON-RPC,
 *          and reporting per-test pass/fail results with summary statistics.
 *
 *          This module is the command-line entry point of the suite. It is intentionally
 *          named SuitManager.py to match the HDMI CEC Source suite's on-disk entry point
 *          exactly, so the two device-level suites stay symmetric; the spelling is the
 *          established repository convention and must not be "corrected".
 *
 *          The suite is authored for execution on a device or a vComponent emulator. This
 *          module never starts, emulates or stubs any of the services it talks to: it only
 *          dispatches requests to whatever endpoint utils.py resolves, and every request
 *          that does not reach a live target is reported as a failure.
 *
 * @precondition
 *  - WPEFramework is running and reachable at the configured JSON-RPC endpoint.
 *  - The org.rdk.HdmiCecSink plugin is available for activation.
 *  - All test case modules listed in SUITES are present under the Testcases/ directory.
 *
 * @dependencies
 *  - utils.py
 *  - HdmiCECSink_Curl.py
 *  - Testcases/*.py
 *
 * @expected_result
 *  - All registered test cases are executed in order and results are logged.
 *
 * @pass_criteria
 *  - Each test case module's run_test() returns True and is reported as PASSED.
 *
 * @failure_criteria
 *  - Any test case returns False, raises an exception, or the plugin fails to activate.
 */
"""

import importlib
import io
import sys
import time
from pathlib import Path
import os

from utils import log_error, log_info, log_success, send_jsonrpc_command, WPEFRAMEWORK_JSONRPC_URL


# Directory holding this module. Every path the suite needs is derived from it, so the suite
# runs correctly from any working directory - `cd Tests/vDeviceTests && python3 SuitManager.py
# hdmicecsink` and `python3 entservices-hdmicecsink/Tests/vDeviceTests/SuitManager.py
# hdmicecsink` resolve Testcases/ and the init module identically. resolve() collapses symlinks
# and relative segments so the value inserted into sys.path is always absolute.
BASE_DIR = Path(__file__).resolve().parent

# The suite registry. Test case modules are registered EXPLICITLY, one quoted name per line, in
# the order they must execute - there is no filesystem globbing and no discovery library. That
# is deliberate: the declared order is part of the contract (see the ordering notes inside the
# list) and an explicit list makes an accidentally orphaned or accidentally renamed test case a
# visible import error rather than a silently skipped case.
SUITES = {
    "hdmicecsink": {
        # Same banner shape and same total width (82 characters) as the HDMI CEC Source suite's
        # banner, so the two suites' output lines up. "SINK" is two characters shorter than
        # "SOURCE", hence two extra trailing asterisks here - the asymmetry in the asterisk runs
        # is what keeps the banners symmetric.
        "banner": "******************** L2 SUITE - RDK - HDMI CEC SINK ******************************",
        "module_dir": BASE_DIR / "Testcases",
        # ORDER IS LOAD-BEARING, NOT COSMETIC. The cases share one device, so an earlier case's
        # effect is a later case's precondition:
        #   * 01-09 are read-only queries and run first, so the device is observed before it is
        #     written to;
        #   * 10-16 are the single-API writes, and 11 (Set_Vendor_ID) must precede 12
        #     (Verify_Vendor_ID_Readback) because 12 reads back exactly what 11 wrote;
        #   * 17-27 are the multi-message flows, and 20 (ARC_Initiation_Flow) must precede 21
        #     (ARC_Termination_Flow) because there is nothing to terminate until ARC has been
        #     initiated;
        #   * 28-33 are the negative, idempotency and health cases, and 30
        #     (Repeated_Disable_Idempotent) must precede 31 (Repeated_Enable_Idempotent) so the
        #     pair leaves the CEC enabled flag restored to its enabled state.
        # Reordering, adding or removing an entry changes the suite's semantics. This list is
        # also the authoritative test case inventory the coverage traceability report draws on.
        "tests": [
            "TCID01_Get_Enabled_Status",
            "TCID02_Get_Devicelist",
            "TCID03_Get_OSD_Name",
            "TCID04_Get_Vendor_ID",
            "TCID05_Get_CEC_Version",
            "TCID06_Get_Active_Source",
            "TCID07_Get_Active_Route",
            "TCID08_Get_Audio_Device_Connected_Status",
            "TCID09_Print_Devicelist",
            "TCID10_Set_OSD_Name",
            "TCID11_Set_Vendor_ID",
            "TCID12_Verify_Vendor_ID_Readback",
            "TCID13_Set_Menu_Language",
            "TCID14_Set_Latency_Info",
            "TCID15_Send_Standby_Message",
            "TCID16_Send_Key_Press_Event",
            "TCID17_Request_Active_Source_Flow",
            "TCID18_Set_Active_Source_Flow",
            "TCID19_Active_Path_Routing_Change_Flow",
            "TCID20_ARC_Initiation_Flow",
            "TCID21_ARC_Termination_Flow",
            "TCID22_System_Audio_Mode_Flow",
            "TCID23_Short_Audio_Descriptor_Flow",
            "TCID24_Audio_Status_And_Power_Flow",
            "TCID25_Standby_Coordination_Flow",
            "TCID26_User_Control_Pressed_Released_Flow",
            "TCID27_Device_Add_Remove_Discovery_Flow",
            "TCID28_Invalid_VendorID_Nochange",
            "TCID29_Invalid_OSD_Setnochange",
            "TCID30_Repeated_Disable_Idempotent",
            "TCID31_Repeated_Enable_Idempotent",
            "TCID32_Invalid_ARC_Routing_Nochange",
            "TCID33_Process_Yaml_Health_Check",
        ],
    },
}

# Maps test suite names to their corresponding RDK plugin callsigns for activation
SUITE_PLUGIN_CALLSIGNS = {
    "hdmicecsink": "org.rdk.HdmiCecSink",
}

# Maps test suite names to the module whose run_test() must succeed BEFORE the first test case
# runs. The sink suite bootstraps its CEC device list through Init_Devicelist_Populate, and a
# False return there aborts the whole suite rather than letting every case fail on a topology
# that was never established.
SUITE_INIT_MODULES = {
    "hdmicecsink": "Init_Devicelist_Populate",
}


def normalize_suite_name(raw_name):
    '''Reduce a suite name to its comparison form so CLI spelling does not matter.
    Surrounding whitespace, underscores and hyphens are removed and the result is lower
    cased, which makes "hdmicecsink", "hdmi_cec_sink", "HDMI-CEC-SINK" and " HDMICECSink "
    all resolve to the same registry key.
    Args:
        raw_name: Suite name exactly as supplied on the command line or as a registry key.
    Returns:
        The normalized, lower-cased name with "_", "-" and outer whitespace removed.
    '''
    return raw_name.strip().replace("_", "").replace("-", "").lower()


def load_test_cases(suite_name):
    '''Import every module registered for a suite and bind its run_test entry point.
    The suite's module directory is placed on sys.path and each registered name is imported
    with importlib, in the declared order. Binding module.run_test here - rather than at call
    time - means a module that exists but does not publish run_test fails immediately and
    visibly instead of part way through a run.
    Args:
        suite_name: A key of SUITES, already normalized and matched by the caller.
    Returns:
        (banner, test_cases) where banner is the suite's banner string and test_cases is a
        list of (module_name, run_test_callable) tuples in declared execution order.
    Raises:
        KeyError: suite_name is not a registered suite.
        ImportError: a registered module is missing from the suite's Testcases/ directory or
            fails while being imported. This is deliberate - a silently skipped test case
            would misreport the suite as complete.
        AttributeError: a registered module imported cleanly but publishes no run_test entry
            point, so there is nothing for the runner to call.
    '''
    suite_config = SUITES[suite_name]
    module_dir = str(suite_config["module_dir"])

    # Putting the Testcases/ directory on sys.path is what lets the modules be imported by
    # bare name, which is why this tree needs no __init__.py and is not a package. Guarding
    # the insert keeps sys.path free of duplicates when a caller loads a suite more than once.
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)

    test_cases = []
    for module_name in suite_config["tests"]:
        module = importlib.import_module(module_name)
        test_cases.append((module_name, module.run_test))

    return suite_config["banner"], test_cases


def activate_plugin_via_curl(callsign):
    '''Activate an RDK plugin through Controller.1.activate and report whether it worked.
    The controller is reached over JSON-RPC at whatever endpoint utils.py resolved; no
    endpoint is hard coded here and no service is started on the suite's behalf. A response
    that never arrived, a response carrying an "error" member and a response with no
    "result" member are all failures, so an unreachable or unavailable plugin can never be
    mistaken for a successful activation.
    Args:
        callsign: Plugin callsign to activate, e.g. "org.rdk.HdmiCecSink".
    Returns:
        True only when the controller answered with a "result" member and no "error"
        member; False on a transport failure, an error response or a missing result.
    '''
    # The request id matches the one utils.activate_plugin sends for this same call, and the
    # one the HDMI CEC Source suite's SuitManager.py uses. Keeping the value in step across
    # all three makes activation calls easy to correlate in a WPEFramework trace no matter
    # which module issued them, so the duplication is deliberate rather than accidental.
    response = send_jsonrpc_command(
        "Controller.1.activate",
        params={"callsign": callsign},
        request_id=1234567890,
    )
    if not response:
        return False
    if "error" in response:
        return False
    return "result" in response


def run_suite_init(suite_name):
    '''Run the suite's initialization module, if one is registered, and report success.
    The init module bootstraps the CEC topology the test cases depend on, so it runs exactly
    once, before the first test case. Every failure mode - an unimportable module, a module
    without a run_test entry point, an exception raised inside it, and a plain False return -
    is reported as failure so the caller can abort instead of running 33 cases against a
    device that was never prepared.
    Args:
        suite_name: A key of SUITES, already normalized and matched by the caller.
    Returns:
        True when no init module is registered for the suite, or when the registered
        module's run_test() returned a truthy value; False on any failure mode above.
    '''
    module_name = SUITE_INIT_MODULES.get(suite_name)
    if not module_name:
        return True

    # The init module sits beside this file rather than under Testcases/, so BASE_DIR is what
    # has to be importable here. On POSIX Path.as_posix() and str() agree for an absolute
    # path, so testing one form and inserting the other still means "insert only if absent".
    if BASE_DIR.as_posix() not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    # A broad except is correct at this boundary: an init module can fail while being imported
    # for any number of reasons (a syntax error, a missing sibling module, a failed
    # module-level lookup) and every one of them means the suite cannot run. The reason is
    # logged rather than swallowed, and the traceback-free message keeps the abort readable.
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        log_error(f"Init module import failed: {module_name} ({exc})")
        return False

    run_fn = getattr(module, "run_test", None)
    if not callable(run_fn):
        log_error(f"Init module missing run_test(): {module_name}")
        return False

    log_info(f"Running suite initialization: {module_name}.run_test()")
    try:
        ok = bool(run_fn())
    except Exception as exc:
        log_error(f"Suite initialization threw exception: {exc}")
        return False

    if ok:
        log_success("Suite initialization completed successfully")
    else:
        log_error("Suite initialization failed")
    return ok


def run_suite(suite_name):
    '''Activate the plugin, initialise the suite, then run every registered case in order.
    Each case's own output is captured and replayed underneath its banner so the log reads
    one case at a time. A case that returns a falsy value AND a case that raises are both
    counted as failures - the exception text is printed into that case's captured output and
    the run continues, so one broken case cannot hide the verdict of the remaining ones.
    Args:
        suite_name: A key of SUITES, already normalized and matched by the caller.
    Returns:
        True only when every registered case passed; False when any case failed, when the
        plugin could not be activated, or when suite initialization did not complete.
    '''
    banner, test_cases = load_test_cases(suite_name)
    print(banner)

    # Activation is ON by default; export AUTO_ACTIVATE_PLUGINS=0 (or "false"/"no") to skip it
    # when the plugin is already activated by other means.
    auto_activate = os.environ.get("AUTO_ACTIVATE_PLUGINS", "1").lower() not in ("0", "false", "no")
    callsign = SUITE_PLUGIN_CALLSIGNS.get(suite_name)
    if auto_activate and callsign:
        log_info(f"Auto-activating plugin '{callsign}' via curl JSON-RPC at {WPEFRAMEWORK_JSONRPC_URL}")
        if activate_plugin_via_curl(callsign):
            log_success(f"Plugin activated: {callsign}")
            log_info("Waiting 6s for plugin to fully initialise...")
            time.sleep(6)
        else:
            # Abort rather than run: every case would fail against a plugin that is not up,
            # and 33 misleading failures are worth less than one accurate one.
            log_error(f"Plugin activation failed: {callsign}")
            log_error("Check JSON-RPC endpoint reachability and plugin availability before running tests.")
            return False

    if not run_suite_init(suite_name):
        log_error("Aborting suite because initialization did not complete successfully.")
        return False

    passed = 0
    failed = 0
    failed_cases = []
    # Captured BEFORE the loop, and restored in the finally of every iteration. If the real
    # stream were only recoverable from inside the try, an exception raised by a case would
    # leave sys.stdout pointing at a dead buffer and silently swallow every later message.
    original_stdout = sys.stdout

    for tc_name, tc_fn in test_cases:
        log_info(f"\n{'='*60}")
        log_info(f"Running: {tc_name}")
        log_info(f"{'='*60}")
        captured = io.StringIO()
        sys.stdout = captured
        try:
            result = tc_fn()
        except Exception as exc:
            # An exception is a FAILURE, never a skip and never a pass. The text goes into the
            # case's own captured output so it is replayed in place, under that case's banner.
            result = False
            print(f"EXCEPTION in {tc_name}: {exc}")
        finally:
            sys.stdout = original_stdout

        output = captured.getvalue()
        print(output, end="")

        if result:
            passed += 1
            log_success(f"[PASS] {tc_name}")
        else:
            failed += 1
            failed_cases.append(tc_name)
            log_error(f"[FAIL] {tc_name}")

        # Deliberate pacing between cases: consecutive CEC transactions on a real bus need a
        # moment to settle before the next case observes the device.
        time.sleep(1)

    log_info(f"\n{'='*60}")
    log_info(f"Suite Summary: {passed} passed, {failed} failed")
    if failed_cases:
        log_error(f"Failed cases: {failed_cases}")
    log_info(f"{'='*60}")
    return failed == 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run HDMI CEC test suites")
    parser.add_argument("suite", help=f"Test suite name. Available: {list(SUITES.keys())}")
    parser.add_argument("-t", "--timing", action="store_true", help="Enable timing output for passed test cases")

    args = parser.parse_args()

    # Set environment variable for timing mode
    if args.timing:
        os.environ["HDMICEC_TIMING_ENABLED"] = "1"

    # Suite names are matched in normalized form, so "hdmicecsink", "hdmi_cec_sink" and
    # "HDMICECSINK" all select the same suite while an unknown name still fails loudly.
    suite_arg = normalize_suite_name(args.suite)
    matching = [k for k in SUITES if normalize_suite_name(k) == suite_arg]
    if not matching:
        log_error(f"Unknown suite '{args.suite}'. Available: {list(SUITES.keys())}")
        sys.exit(1)

    # The process exit code is the suite's only machine-readable signal: 0 only when every
    # registered case passed, 1 on any failure, on a failed activation, or on a failed
    # initialization. CI must be able to trust it, so nothing else may be returned here.
    ok = run_suite(matching[0])
    sys.exit(0 if ok else 1)
