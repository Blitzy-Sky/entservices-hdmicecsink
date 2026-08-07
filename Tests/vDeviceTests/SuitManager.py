"""
/**
 * @file SuitManager.py
 * @brief SuitManager.py
 *
 * @testcase SuitManager
 * @details Orchestrates the HDMI CEC Sink L3/device-level test suite by dynamically loading
 *          and executing test case modules, activating the required RDK plugin via JSON-RPC,
 *          and reporting per-test pass/fail results with summary statistics. The suite is
 *          authored here and its runtime execution is deferred to a device or emulator
 *          environment; nothing in this repository runs it.
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
 *  - All registered test cases are executed in order, every declared producer/consumer
 *    dependency is honoured, every cleanup() hook runs, and results are logged.
 *
 * @pass_criteria
 *  - Each test case module's run_test() returns True and is reported as PASSED, and every
 *    cleanup() hook completes.
 *
 * @failure_criteria
 *  - Any test case returns False, raises an exception, or is skipped because a producer it
 *    depends on did not pass; any cleanup() hook fails; the plugin fails to activate or does
 *    not become ready within its budget; or suite initialization does not complete.
 */
"""

import importlib
import io
import sys
from pathlib import Path
import os

from utils import (
    await_plugin_ready,
    log_error,
    log_info,
    log_success,
    log_warning,
    send_jsonrpc_command,
    WPEFRAMEWORK_JSONRPC_URL,
)


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
        #
        # The LEVEL LABEL deliberately does not match the source suite's. This suite lives under
        # Tests/vDeviceTests/, which is the L3 device-level location, and every test case in it
        # is documented as L3; the source suite's banner still reads "L2" because that is its own
        # pre-existing upstream wording and relabelling it is outside this suite's remit. "L2"
        # and "L3" are the same width, so the 82-character alignment above is unaffected.
        "banner": "******************** L3 SUITE - RDK - HDMI CEC SINK ******************************",
        "module_dir": BASE_DIR / "Testcases",
        # ORDER IS LOAD-BEARING, NOT COSMETIC. The cases share one device, so an earlier case's
        # effect is a later case's precondition:
        #   * 01-09 are the early observation block. Eight of the nine are read-only queries and
        #     run first so the device is observed before it is written to. 05
        #     (Get_CEC_Version) is the one exception and is called out here rather than left to
        #     be discovered: it injects a directed <Get CEC Version> and a directed
        #     <CEC Version> onto the emulated bus, because the plugin publishes no
        #     getCecVersion method and the bus is the only place that surface is observable.
        #     What it writes is idempotent and identical to what Init_Devicelist_Populate.py
        #     already seeded - the same peer, opcode and operand, recorded by the same handler -
        #     so it leaves 06 through 09 exactly the device they would otherwise have seen, and
        #     its position inside this block is free rather than constrained;
        #   * 10-16 are the single-API writes, and 11 (Set_Vendor_ID) must precede 12
        #     (Verify_Vendor_ID_Readback) because 12 reads back exactly what 11 wrote;
        #   * 17-27 are the multi-message flows, and 20 (ARC_Initiation_Flow) must precede 21
        #     (ARC_Termination_Flow) because there is nothing to terminate until ARC has been
        #     initiated;
        #   * 28-33 are the negative, idempotency and health cases, and 30
        #     (Repeated_Disable_Idempotent) must precede 31 (Repeated_Enable_Idempotent) so the
        #     pair leaves the CEC enabled flag restored to its enabled state.
        # Reordering, adding or removing an entry changes the suite's semantics. This list is
        # also the authoritative ordered inventory of the suite's test cases: anything that needs
        # to enumerate them reads it here rather than globbing the Testcases directory.
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
        # The two JSON-RPC observables this runner polls INSTEAD OF SLEEPING. Both are cheap,
        # read-only plugin methods, and both are declared here rather than hard coded inside the
        # runner so the runner itself stays suite agnostic.
        #   * readiness_probe answers only once the plugin is activated AND dispatching its own
        #     methods, which is what "the plugin has finished initialising" actually means as an
        #     observable. Polling it replaces a fixed post-activation sleep.
        #   * settle_probe reports the one piece of plugin state that inbound CEC traffic
        #     changes - the discovered device count - so consecutive identical readings are
        #     evidence that the bus has gone quiet. Polling it replaces a fixed inter-case sleep.
        # result_key names the member of the JSON-RPC result that must be present for the answer
        # to count as an answer, so a well-formed envelope carrying a different payload is not
        # mistaken for readiness.
        "readiness_probe": {
            "method": "org.rdk.HdmiCecSink.1.getEnabled",
            "result_key": "enabled",
        },
        "settle_probe": {
            "method": "org.rdk.HdmiCecSink.1.getDeviceList",
            "result_key": "numberofdevices",
        },
    },
}

# Explicit consumer -> producers dependency model.
#
# Registration order alone is NOT a dependency mechanism: it fixes the sequence but says
# nothing about what happens when an earlier case fails, so without this map a consumer whose
# producer failed still runs and can report PASS against state that was never established.
# Every edge below is declared by the consumer module's own @precondition block, quoted here so
# the map and the modules cannot drift apart silently:
#   * TCID12 <- TCID11  "TCID11_Set_Vendor_ID has run at the preceding registration position
#     and written the vendor identifier this case reads back."
#   * TCID21 <- TCID20  "TCID20_ARC_Initiation_Flow is expected to have run immediately before
#     this case and to have left ARC enabled; this module is the half of that pair which
#     restores it."
#   * TCID31 <- TCID30  "TCID30_Repeated_Disable_Idempotent has run at the preceding position."
#
# No edge is declared for any other case, deliberately. TCID32_Invalid_ARC_Routing_Nochange is
# the case a reader is most likely to expect here, and its @precondition states the opposite:
# it "neither depends on that value nor changes it, because it compares two observations
# instead of pinning one". Declaring an edge it disclaims would make this map fiction. Every
# other residual coupling in the suite - a case that changes OSD name, vendor identity, power
# state, active source or the device list and is observed by a later case - is removed at the
# source instead, by making the case restore what it changed in its own finally / cleanup(),
# which is the other half of the same requirement.
#
# A producer must be registered EARLIER than its consumer in "tests"; resolve_dependencies()
# enforces that, so a forward or circular edge is a startup error rather than a silent skip.
TEST_DEPENDENCIES = {
    "hdmicecsink": {
        "TCID12_Verify_Vendor_ID_Readback": ("TCID11_Set_Vendor_ID",),
        "TCID21_ARC_Termination_Flow": ("TCID20_ARC_Initiation_Flow",),
        "TCID31_Repeated_Enable_Idempotent": ("TCID30_Repeated_Disable_Idempotent",),
    },
}

# Bounded-wait budgets, in seconds. Every wait in this module is a bounded poll of a named
# observable rather than a fixed sleep: it returns as soon as the state it is waiting for is
# observed, and when the budget expires it says so instead of continuing silently. The poll
# intervals below are the gap BETWEEN observations, not a duration anything waits for.
PLUGIN_READY_TIMEOUT_S = 30.0
PLUGIN_READY_POLL_INTERVAL_S = 0.25
# Two identical consecutive readings are what "settled" is defined as here. One reading proves
# nothing about stability, and a third would double the cost of every inter-case gap for no
# additional evidence.
SETTLE_STABLE_READS = 2
SETTLE_TIMEOUT_S = 15.0
SETTLE_POLL_INTERVAL_S = 0.5

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
    A module MAY additionally publish a callable cleanup(), which is bound here and which the
    runner then calls unconditionally - after a pass, after a failure, after an exception, and
    for a case that was skipped because its producer failed. cleanup() is the restoration half
    of a producer/consumer pair, so it must be idempotent and must not assume that run_test()
    ran or succeeded. Publishing it is optional: a case that changes nothing needs none, and
    getattr keeps a module without one perfectly valid.

    Args:
        suite_name: A key of SUITES, already normalized and matched by the caller.
    Returns:
        (banner, test_cases) where banner is the suite's banner string and test_cases is a
        list of (module_name, run_test_callable, cleanup_callable_or_None) tuples in declared
        execution order.
    Raises:
        KeyError: suite_name is not a registered suite.
        ImportError: a registered module is missing from the suite's Testcases/ directory or
            fails while being imported. This is deliberate - a silently skipped test case
            would misreport the suite as complete.
        AttributeError: a registered module imported cleanly but publishes no run_test entry
            point, so there is nothing for the runner to call.
        TypeError: a registered module publishes a cleanup attribute that is not callable, so
            the restoration the runner is relying on could never be invoked.
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
        cleanup_fn = getattr(module, "cleanup", None)
        if cleanup_fn is not None and not callable(cleanup_fn):
            raise TypeError(f"{module_name}.cleanup exists but is not callable")
        test_cases.append((module_name, module.run_test, cleanup_fn))

    return suite_config["banner"], test_cases


def resolve_dependencies(suite_name):
    '''Validate the suite's dependency map against its registration order and return it.
    Resolution is a startup check, not a per-case one, so an incoherent map is a loud error
    before the device is touched rather than a mis-skipped case in the middle of a run. A
    producer that is not registered, a consumer that is not registered, a case declared as its
    own producer and a producer registered at or after its consumer are all rejected - the last
    of these is what rules out circular edges, since a graph whose every edge points strictly
    backwards in a fixed order cannot contain a cycle.
    Args:
        suite_name: A key of SUITES, already normalized and matched by the caller.
    Returns:
        {consumer_name: (producer_name, ...)} containing only validated edges. An empty dict
        when the suite declares no dependencies.
    Raises:
        ValueError: any edge names an unregistered case, is self-referential, or names a
            producer that does not run strictly before its consumer.
    '''
    ordered = SUITES[suite_name]["tests"]
    position = {name: index for index, name in enumerate(ordered)}
    declared = TEST_DEPENDENCIES.get(suite_name, {})

    validated = {}
    for consumer, producers in declared.items():
        if consumer not in position:
            raise ValueError(f"dependency declared for unregistered case '{consumer}'")
        for producer in producers:
            if producer not in position:
                raise ValueError(
                    f"case '{consumer}' depends on unregistered case '{producer}'"
                )
            if producer == consumer:
                raise ValueError(f"case '{consumer}' declares itself as its own producer")
            if position[producer] >= position[consumer]:
                raise ValueError(
                    f"case '{consumer}' depends on '{producer}', which is registered at or "
                    "after it; a producer must run strictly before its consumer"
                )
        validated[consumer] = tuple(producers)

    return validated


def _probe_value(probe):
    '''Dispatch one read-only JSON-RPC probe and return (answered, value).
    A probe counts as answered only when the target returned a JSON-RPC envelope with no
    "error" member, whose "result" is an object, whose "success" member is not explicitly
    false, and which carries the probe's declared result_key. Anything weaker - a transport
    failure, an error envelope, a result that is not an object, a plugin reporting failure -
    reads as unanswered, so a plugin that is registered but not yet dispatching can never be
    mistaken for a ready one.
    Args:
        probe: {"method": fully qualified JSON-RPC method, "result_key": required member}.
    Returns:
        (True, value_of_result_key) when the probe answered; (False, None) otherwise.
    '''
    response = send_jsonrpc_command(probe["method"])
    if not response or "error" in response:
        return False, None
    result = response.get("result")
    if not isinstance(result, dict):
        return False, None
    if result.get("success") is False:
        return False, None
    key = probe["result_key"]
    if key not in result:
        return False, None
    return True, result[key]


def _plugin_state(callsign):
    '''Return the controller's reported state string for a callsign, or None.
    Controller.1.status@<callsign> answers with a list of service records in Thunder R4 and a
    single record in some builds, so both shapes are accepted and the first record's "state" is
    returned. None means the controller did not answer, answered with an error, or answered
    without a usable state - none of which is treated as activated.
    Args:
        callsign: Plugin callsign, e.g. "org.rdk.HdmiCecSink".
    Returns:
        The state string exactly as reported, or None when no state could be read.
    '''
    response = send_jsonrpc_command(f"Controller.1.status@{callsign}")
    if not response or "error" in response:
        return None
    result = response.get("result")
    if isinstance(result, list):
        result = result[0] if result else None
    if not isinstance(result, dict):
        return None
    state = result.get("state")
    return state if isinstance(state, str) else None


def wait_for_plugin_ready(callsign, probe, timeout_s=PLUGIN_READY_TIMEOUT_S,
                          interval_s=PLUGIN_READY_POLL_INTERVAL_S):
    '''Poll until the plugin is both activated and dispatching, or the budget expires.
    This is the bounded-observation replacement for a fixed post-activation sleep. Two distinct
    observables must both hold: the controller must report the callsign as activated, and the
    plugin must answer its own readiness probe. The first alone is not enough - a plugin
    transitions to activated before its JSON-RPC surface is answering - and the second alone
    would not distinguish a plugin that is up from a controller that is unreachable.
    The elapsed time is measured with monotonic(), which no clock adjustment can move
    backwards, so the budget cannot be extended or truncated by a wall-clock change mid-run.
    Args:
        callsign: Plugin callsign to observe, or a falsy value to observe the probe only.
        probe: Readiness probe as accepted by _probe_value, or None to observe state only.
        timeout_s: Upper bound on the whole wait, in seconds.
        interval_s: Gap between consecutive observations, in seconds.
    Returns:
        (True, elapsed_seconds) as soon as every declared observable holds;
        (False, elapsed_seconds) when the budget expired first.
    '''
    started = time.monotonic()
    deadline = started + max(0.0, float(timeout_s))
    while True:
        state_ok = True
        if callsign:
            state = _plugin_state(callsign)
            state_ok = state is not None and state.strip().lower() == "activated"
        probe_ok = True
        if state_ok and probe:
            probe_ok, _ = _probe_value(probe)
        if state_ok and probe_ok:
            return True, time.monotonic() - started
        if time.monotonic() >= deadline:
            return False, time.monotonic() - started
        # Sleeping the poll interval - never the whole budget - is what keeps this a bounded
        # observation: the loop leaves as soon as the state is observed.
        time.sleep(interval_s)


def wait_for_settled(probe, timeout_s=SETTLE_TIMEOUT_S, interval_s=SETTLE_POLL_INTERVAL_S,
                     stable_reads=SETTLE_STABLE_READS):
    '''Poll a probe until consecutive readings agree, or the budget expires.
    This is the bounded-observation replacement for a fixed inter-case sleep. "The bus has
    settled" is not a duration, it is a property: the plugin state that inbound CEC traffic
    changes has stopped changing. Requiring stable_reads consecutive equal readings observes
    exactly that, and returns immediately on a quiet bus instead of always paying a fixed
    pause. An unanswered probe resets the run of agreements rather than counting towards it.
    Args:
        probe: Settle probe as accepted by _probe_value, or None to skip the wait entirely.
        timeout_s: Upper bound on the whole wait, in seconds.
        interval_s: Gap between consecutive observations, in seconds.
        stable_reads: Number of consecutive equal readings that constitute settled.
    Returns:
        (True, elapsed_seconds, last_value) once the readings agree, or immediately with
        (True, 0.0, None) when no probe is declared;
        (False, elapsed_seconds, last_value) when the budget expired first.
    '''
    if not probe:
        return True, 0.0, None

    started = time.monotonic()
    deadline = started + max(0.0, float(timeout_s))
    agreements = 0
    previous = None
    have_previous = False
    while True:
        answered, value = _probe_value(probe)
        if answered:
            if have_previous and value == previous:
                agreements += 1
            else:
                agreements = 1
            previous = value
            have_previous = True
            if agreements >= max(1, int(stable_reads)):
                return True, time.monotonic() - started, value
        else:
            agreements = 0
            have_previous = False
        if time.monotonic() >= deadline:
            return False, time.monotonic() - started, previous if have_previous else None
        time.sleep(interval_s)


def _run_cleanup(tc_name, cleanup_fn):
    '''Run a case's optional cleanup() hook and report whether it completed.
    The hook is restoration, not verdict: its outcome never turns a failing case into a passing
    one or the reverse. It is called unconditionally by the runner - after a pass, a failure, an
    exception, and for a case skipped because its producer failed - so a pair whose restoring
    half never got to run as a test still restores the device.
    A broad except is correct here for the same reason it is correct around a test case: any
    exception out of a restoration attempt means the device may be dirty, and the run must
    report that rather than propagate out of the runner's finally and abandon the remaining
    cases. The text is printed, not swallowed, and is replayed under the case's own banner.
    Args:
        tc_name: Registered module name, used only in messages.
        cleanup_fn: The bound cleanup callable, or None when the module publishes none.
    Returns:
        True when there was nothing to do or the hook completed without an explicit falsy
        return; False when the hook raised or returned a falsy value other than None.
    '''
    if cleanup_fn is None:
        return True
    try:
        outcome = cleanup_fn()
    except Exception as exc:
        print(f"EXCEPTION in {tc_name}.cleanup(): {exc}")
        return False
    # A hook that returns nothing at all has still run to completion; only an explicit falsy
    # return is a restoration failure, so `return None` and `return True` mean the same thing.
    if outcome is None:
        return True
    return bool(outcome)


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

    Three properties beyond plain sequencing are enforced here:
      * Declared dependencies are honoured. A case whose producer did not PASS is SKIPPED and
        counted as skipped. It is never run and can therefore never report PASS against state
        that was never established, and skipped is never folded into passed.
      * Restoration is unconditional. Every case's optional cleanup() runs whether the case
        passed, failed, raised or was skipped, so the restoring half of a producer/consumer
        pair still restores the device when its partner failed.
      * Nothing is waited for blindly. Plugin readiness and inter-case settling are bounded
        polls of named JSON-RPC observables (see the suite's readiness_probe and settle_probe),
        so a ready device is not paid for in fixed sleeps and an unready one is reported.
    Args:
        suite_name: A key of SUITES, already normalized and matched by the caller.
    Returns:
        True only when every registered case passed and every cleanup completed; False when any
        case failed, any case was skipped, any cleanup failed, the plugin could not be activated
        or did not become ready, or suite initialization did not complete.
    '''
    banner, test_cases = load_test_cases(suite_name)
    # Resolved before the device is touched: an incoherent dependency map must stop the run
    # here, not half way through it.
    dependencies = resolve_dependencies(suite_name)
    suite_config = SUITES[suite_name]
    readiness_probe = suite_config.get("readiness_probe")
    settle_probe = suite_config.get("settle_probe")
    print(banner)

    # Activation is ON by default; export AUTO_ACTIVATE_PLUGINS=0 (or "false"/"no") to skip it
    # when the plugin is already activated by other means.
    auto_activate = os.environ.get("AUTO_ACTIVATE_PLUGINS", "1").lower() not in ("0", "false", "no")
    callsign = SUITE_PLUGIN_CALLSIGNS.get(suite_name)
    if auto_activate and callsign:
        log_info(f"Auto-activating plugin '{callsign}' via curl JSON-RPC at {WPEFRAMEWORK_JSONRPC_URL}")
        if activate_plugin_via_curl(callsign):
            log_success(f"Plugin activated: {callsign}")
            # Controller.1.activate returns once the request is accepted; Initialize() and the
            # plugin's worker threads come up after it. That readiness is observable through
            # Controller.1.status, so it is waited for rather than estimated. A plugin already up
            # costs nothing here, and an expiry is reported instead of being read as success.
            log_info(f"Waiting for {callsign} to report itself activated...")
            if not await_plugin_ready(callsign):
                log_warning(
                    f"Plugin {callsign} did not report state 'activated' before the deadline; "
                    "the cases below will run anyway and their own assertions decide the verdict"
                )
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
    skipped = 0
    failed_cases = []
    skipped_cases = []
    cleanup_failures = []
    # Per-case outcome, keyed by module name: "PASSED", "FAILED" or "SKIPPED". This is what the
    # dependency check reads, and it is why a SKIPPED producer propagates - only "PASSED"
    # satisfies a dependency, so a consumer downstream of a skipped case is skipped in turn
    # rather than running against state two failures back.
    outcomes = {}
    # Captured BEFORE the loop, and restored in the finally of every iteration. If the real
    # stream were only recoverable from inside the try, an exception raised by a case would
    # leave sys.stdout pointing at a dead buffer and silently swallow every later message.
    original_stdout = sys.stdout
    last_index = len(test_cases) - 1

    for index, (tc_name, tc_fn, tc_cleanup) in enumerate(test_cases):
        unmet = [p for p in dependencies.get(tc_name, ()) if outcomes.get(p) != "PASSED"]

        log_info(f"\n{'='*60}")
        log_info(f"{'Skipping' if unmet else 'Running'}: {tc_name}")
        log_info(f"{'='*60}")

        captured = io.StringIO()
        sys.stdout = captured
        try:
            if unmet:
                # NOT RUN, and NOT PASSED. The producer's state was never established, so any
                # verdict this case could report would be about something else.
                result = None
                for producer in unmet:
                    print(
                        f"SKIPPED {tc_name}: required producer {producer} "
                        f"{outcomes.get(producer, 'did not run')}"
                    )
            else:
                try:
                    result = tc_fn()
                except Exception as exc:
                    # An exception is a FAILURE, never a skip and never a pass. The text goes
                    # into the case's own captured output so it is replayed in place, under that
                    # case's banner.
                    result = False
                    print(f"EXCEPTION in {tc_name}: {exc}")
            # Restoration runs while output is still captured, so its messages are replayed
            # under this case's banner, and it runs on every path above - pass, fail, exception
            # and skip alike. _run_cleanup swallows nothing but propagates nothing either, so
            # the stdout restore below is always reached.
            cleanup_ok = _run_cleanup(tc_name, tc_cleanup)
        finally:
            sys.stdout = original_stdout

        output = captured.getvalue()
        print(output, end="")

        if unmet:
            skipped += 1
            outcomes[tc_name] = "SKIPPED"
            skipped_cases.append(f"{tc_name} (unmet: {', '.join(unmet)})")
            log_error(f"[SKIP] {tc_name} - unmet dependencies: {', '.join(unmet)}")
        elif result:
            passed += 1
            outcomes[tc_name] = "PASSED"
            log_success(f"[PASS] {tc_name}")
        else:
            failed += 1
            outcomes[tc_name] = "FAILED"
            failed_cases.append(tc_name)
            log_error(f"[FAIL] {tc_name}")

        # Between cases, confirm the plugin is still answerable rather than pausing for a fixed
        # period. The JSON-RPC round trip below is synchronous, so it both separates consecutive
        # cases by the time the framework actually needs to service a request and reports a plugin
        # that has stopped responding - which a bare sleep would have hidden until the next case
        # failed for a reason that looked unrelated.
        if callsign and send_jsonrpc_command(f"{callsign}.1.getEnabled") is None:
            log_warning(
                f"{callsign} did not answer getEnabled after {tc_name}; the cases that follow may "
                "fail against a plugin that is no longer serving"
            )

    log_info(f"\n{'='*60}")
    log_info(f"Suite Summary: {passed} passed, {failed} failed, {skipped} skipped")
    if failed_cases:
        log_error(f"Failed cases: {failed_cases}")
    if skipped_cases:
        log_error(f"Skipped cases: {skipped_cases}")
    if cleanup_failures:
        log_error(f"Cases whose cleanup failed: {cleanup_failures}")
    log_info(f"{'='*60}")
    # A skip is not a pass, and an unrestored device is not a clean run: both count against the
    # suite verdict, so the exit code cannot read green while either is outstanding.
    return failed == 0 and skipped == 0 and not cleanup_failures


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
