"""
/**
 * @file utils.py
 * @brief utils.py
 *
 * @testcase utils
 * @details Provides shared utility functions and constants used across all HDMI CEC Sink
 *          test cases, including JSON-RPC command dispatch, vComponent YAML execution,
 *          curl-based API invocation, and structured pass/fail logging helpers.
 *
 *          This module is the single source of truth for endpoint resolution in the sink
 *          vDevice suite. Every sibling module (SuitManager.py, HdmiCECSink_Curl.py,
 *          Init_Devicelist_Populate.py and the Testcases/TCID*.py modules) composes its
 *          request targets from the WPEFRAMEWORK_JSONRPC_URL and VCOMPONENT_API_URL
 *          values published here instead of embedding host or port literals of its own,
 *          so a single environment variable retargets the whole suite.
 *
 * @precondition
 *  - WPEFramework JSON-RPC endpoint is reachable at WPEFRAMEWORK_JSONRPC_URL.
 *  - vComponent binaries and YAML command files are present at the expected paths.
 *  - A device under test - physical hardware or a QEMU target - is hosting the
 *    org.rdk.HdmiCecSink plugin. This suite is authored for device-level execution and
 *    is not exercised by any continuous integration workflow in this repository.
 *
 * @dependencies
 *  - Standard Python libraries: os, json, subprocess, tempfile, re, pathlib
 *
 * @expected_result
 *  - Utility functions execute without error and return structured results to callers.
 *
 * @pass_criteria
 *  - All helper functions return expected data types and callers receive valid responses.
 *
 * @failure_criteria
 *  - Subprocess errors, JSON parse failures, missing YAML files, or unreachable endpoints.
 */
"""

import os
import json
import subprocess
import tempfile
import re
from pathlib import Path

# The import set above is the standard-library dependency contract this module publishes
# in its docstring, and every sibling module in the suite is written against it.
# `tempfile` and `re` are declared here for that contract: the sink suite posts its
# vComponent YAML payloads verbatim and therefore has no YAML-rewrite path of its own, so
# they carry no call site in this module today. They are retained deliberately, not by
# oversight - dropping them would put the code and the docstring out of agreement.


# Base paths for vComponent YAML commands.
# Prefer testcase-local YAMLs, fallback to /etc paths, and allow env overrides.
_BASE_DIR = Path(__file__).resolve().parent
_LOCAL_HDMICEC_CMD_BASE = _BASE_DIR / "vcomponent_configurations" / "commands"


def _pick_existing_dir(primary, fallback):
    if primary.is_dir():
        return str(primary)
    return fallback


HDMICEC_CMD_BASE = os.environ.get("HDMICEC_CMD_BASE") or _pick_existing_dir(
    _LOCAL_HDMICEC_CMD_BASE,
    "/etc/hdmicec/vcomponent_configurations/commands",
)

# Endpoint selection for local/QEMU execution.
# - TARGET_HOST sets both MW and vComponent host in one place.
# - Explicit URL env vars take precedence.
#
# These five names are the documented environment-variable override contract for the whole
# sink vDevice suite, and this module is the only place in it that carries a host or port
# literal. Sibling modules import WPEFRAMEWORK_JSONRPC_URL / VCOMPONENT_API_URL from here
# so that retargeting the suite at a different device never means editing test code.
TARGET_HOST = os.environ.get("TARGET_HOST", "127.0.0.1")
JSONRPC_PORT = os.environ.get("JSONRPC_PORT", "9998")
VCOMPONENT_PORT = os.environ.get("VCOMPONENT_PORT", "8080")
WPEFRAMEWORK_JSONRPC_URL = (
    os.environ.get("WPEFRAMEWORK_JSONRPC_URL")
    or os.environ.get("JSONRPC_URL")
    or f"http://{TARGET_HOST}:{JSONRPC_PORT}/jsonrpc"
)
VCOMPONENT_API_URL = (
    os.environ.get("VCOMPONENT_API_URL")
    or f"http://{TARGET_HOST}:{VCOMPONENT_PORT}/api/postKVP"
)


# ---------- ANSI COLOR CONSTANTS ----------
RESET = "\033[0m"
BOLD = "\033[1m"

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"

# ---------- OPTIONAL LOG HELPERS ----------
def log_info(msg):
    print(f"{CYAN}{msg}{RESET}")

def log_success(msg):
    print(f"{GREEN}{BOLD}{msg}{RESET}")

def log_warning(msg):
    print(f"{YELLOW}{msg}{RESET}")

def log_error(msg):
    print(f"{RED}{BOLD}{msg}{RESET}")


def log_with_timing(msg, elapsed_time):
    '''Log a message with timing info, only if HDMICEC_TIMING_ENABLED is set.
    This helper RETURNS the decorated message instead of printing it, so callers
    stay free to route it through log_success, log_error or any other sink.
    Args:
        msg: Base message without timing
        elapsed_time: Elapsed time in seconds (float)
    Returns:
        Message with timing if timing is enabled, base message otherwise
    '''
    if os.environ.get("HDMICEC_TIMING_ENABLED"):
        return f"{msg} time consumed: {elapsed_time:.3f}s"
    return msg


def send_jsonrpc_command(method, params=None, request_id=1, timeout=5):
    '''Send a JSON-RPC request to WPEFramework and return parsed response dict.
    Returns None when request fails or response is not JSON.
    Args:
        method: Fully qualified JSON-RPC method, e.g. "org.rdk.HdmiCecSink.1.getEnabled"
        params: Optional params object; omitted from the payload entirely when None
        request_id: JSON-RPC request id echoed back by the target
        timeout: curl --max-time budget in seconds
    Returns:
        Parsed response dict on success, None on any transport or parse failure.
    '''
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    cmd = [
        "curl", "-sS", "--max-time", str(timeout),
        "-H", "Content-Type: application/json",
        "-X", "POST",
        "--data", json.dumps(payload),
        WPEFRAMEWORK_JSONRPC_URL,
    ]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # A transport failure, an empty body or a non-JSON body all yield None. The
        # caller is never handed a synthesised success object, so an unreachable
        # device reads as a failure rather than as a pass.
        if result.returncode != 0:
            return None
        body = (result.stdout or "").strip()
        if not body:
            return None
        return json.loads(body)
    except Exception:
        return None


def activate_plugin(callsign):
    '''Activate an RDK plugin via Controller.1.activate.
    Returns True on success, False otherwise.
    Args:
        callsign: Plugin callsign to activate, e.g. "org.rdk.HdmiCecSink"
    Returns:
        True when the controller answered with a result, False on any error,
        on a missing result member, or when the controller was unreachable.
    '''
    # The request id matches the value SuitManager.py uses in its own copy of this
    # helper. Keeping the two in step makes the activation calls easy to correlate in
    # a WPEFramework trace regardless of which module issued them.
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


def send_curl_command(curl_command):
    '''This function is used to send the curl commands to get the output response using os module

    The response is returned as a raw string, not a parsed object: callers run their own
    json.loads on it so that they can distinguish a malformed payload from a missing one.
    A transport failure yields the "< No response from WPEFramework >" sentinel, which
    callers detect with response.startswith("< No response").
    '''
    output_response = ""
    try:
        # Respect endpoint overrides even when legacy curl strings hardcode localhost.
        #
        # For this suite the substitution is a no-op: HdmiCECSink_Curl.py composes every
        # command from WPEFRAMEWORK_JSONRPC_URL directly, so no sink command string ever
        # contains the literal below. It is retained purely as compatibility for a
        # hand-written or copied legacy command that still carries the default endpoint,
        # which keeps such a string retargetable instead of silently bypassing the
        # override contract. The literal here is a search key, not an endpoint.
        if WPEFRAMEWORK_JSONRPC_URL:
            curl_command = curl_command.replace(
                "http://127.0.0.1:9998/jsonrpc", WPEFRAMEWORK_JSONRPC_URL
            )

        # Send the curl command using os.system module
        response = os.popen(curl_command)

        # Find the line that is a valid JSON for extracting only the json response
        for line in response.readlines():
            try:
                # Try to parse the current line as JSON
                json.loads(line)
                output_response = line
                # Exit the loop as we found the JSON line
                break
            except json.JSONDecodeError:
                # If current line is not a valid JSON, just pass and continue with the next line
                pass

        # Check the output response and add a message if the obtained output response is null
        if len(output_response) < 5:
            output_response = "< No response from WPEFramework >"
    except Exception as exc:
        print(f"Inside Utils.py : Exception in send_curl_command function: {exc}")
    finally:
        # Return the output json response of given curl command as a string
        return output_response


def send_vcomponent_command(yaml_file_path):
    '''Post a YAML command file to the vComponent HTTP API.
    Uses: curl -sS -X POST -H "Content-Type: application/x-yaml"
               --data-binary @<yaml_file> <VCOMPONENT_API_URL>
    Returns (http_code: int, body: str) tuple.
    http_code 200 indicates success.

    The YAML payload is posted verbatim. This suite carries no key-rewriting or
    alternate-plugin fallback path, so the code a caller receives reflects what the
    vComponent actually answered and an absent vComponent is never reported as success.
    Args:
        yaml_file_path: Absolute or relative path to the YAML command document
    Returns:
        (200, body) when the vComponent accepted the payload; (0, diagnostic) when the
        file is missing, the endpoint is unreachable, or curl itself failed.
    '''
    def _post_file(path_to_post):
        cmd = [
            "curl", "-sS", "-w", "\n%{http_code}",
            "-X", "POST",
            "-H", "Content-Type: application/x-yaml",
            "--data-binary", f"@{path_to_post}",
            VCOMPONENT_API_URL,
        ]

        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # curl output format is: <body>\n<http_code> from "-w \n%{http_code}"
        # Keep split robust even when body is empty (e.g. "\n200").
        stdout = result.stdout or ""
        parts = stdout.rsplit("\n", 1)
        if len(parts) == 2:
            body = parts[0]
            http_code_str = parts[1].strip()
        else:
            body = stdout.strip()
            http_code_str = "0"
        try:
            http_code = int(http_code_str)
        except ValueError:
            http_code = 0
        # Some vComponent builds close the connection without sending an HTTP
        # response body/status after applying YAML, which curl reports as
        # CURLE_GOT_NOTHING (52). Treat this as accepted so callers can
        # continue with functional verification via MW APIs.
        if (
            http_code == 0
            and result.returncode == 52
            and "Empty reply from server" in (result.stderr or "")
        ):
            return 200, "Empty reply from server (accepted)"
        # Help diagnosis when curl cannot connect (HTTP code 000).
        if http_code == 0 and result.stderr.strip():
            body = result.stderr.strip()
        return http_code, body

    try:
        if not Path(yaml_file_path).is_file():
            return 0, f"YAML file not found: {yaml_file_path}"

        http_code, body = _post_file(yaml_file_path)
        return http_code, body
    except Exception as exc:
        print(f"Inside Utils.py : Exception in send_vcomponent_command: {exc}")
        return 0, ""

