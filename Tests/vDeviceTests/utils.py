"""
/**
 * @file utils.py
 * @brief Endpoint resolution, command dispatch and logging helpers shared by the
 *        HDMI-CEC Sink device-level suite.
 *
 * @testcase utils
 * @details Provides shared utility functions and constants used across all HDMI CEC Sink
 *          test cases, including JSON-RPC command dispatch, vComponent YAML execution,
 *          curl-based API invocation, and structured pass/fail logging helpers.
 *
 *          This module is the single source of truth for endpoint resolution in the sink
 *          vDevice suite: a sibling module composes its request targets from the
 *          WPEFRAMEWORK_JSONRPC_URL and VCOMPONENT_API_URL values published here instead
 *          of embedding host or port literals of its own, so a single environment
 *          variable retargets the whole suite. HdmiCECSink_Curl.py is the consumer that
 *          exists today, alongside the vcomponent_configurations/ YAML fixtures this
 *          module posts. The remaining modules of the suite - SuitManager.py,
 *          Init_Devicelist_Populate.py and the Testcases/TCID*.py cases - are planned and
 *          are not present in this directory yet; they are written against the same
 *          contract when they land.
 *
 *          Every request this module issues is executed as an argument LIST with no shell
 *          involved, through the single hardened helper _run_curl(). A request is therefore
 *          DATA - a JsonRpcRequest of method, params, id and timeout - never a command
 *          string, which is why HdmiCECSink_Curl.py publishes structured requests rather
 *          than assembled curl lines. The endpoints are validated before use, the option
 *          terminator "--" precedes every URL, every invocation is bounded in time, and a
 *          non-zero curl exit status is always reported as a failure rather than
 *          reinterpreted as a success.
 *
 * @precondition
 *  - A device under test - physical hardware or a QEMU target - is hosting the
 *    org.rdk.HdmiCecSink plugin and answering JSON-RPC at WPEFRAMEWORK_JSONRPC_URL.
 *  - The vComponent HTTP API is serving VCOMPONENT_API_URL, and the YAML command
 *    documents under HDMICEC_CMD_BASE are readable.
 *  - No continuous integration workflow in this repository executes this suite; it is
 *    authored for device-level execution.
 *
 * @dependencies
 *  - Standard Python libraries: os, json, shlex, subprocess, re, collections, pathlib,
 *    urllib.parse
 *
 * @expected_result
 *  - Helpers return the structured result documented per function: a parsed dict, an
 *    (http_code, body) tuple, a raw response string or a decorated log message.
 *
 * @pass_criteria
 *  - Every helper returns its documented type, and a transport failure is reported
 *    through the documented failure value rather than raised at the caller.
 *
 * @failure_criteria
 *  - Subprocess errors, JSON parse failures, missing YAML documents, or unreachable
 *    JSON-RPC / vComponent endpoints.
 */
"""

import os
import json
import shlex
import subprocess
import re
from collections import namedtuple
from pathlib import Path
from urllib.parse import urlsplit

# The import set above is the standard-library dependency contract this module publishes in
# its docstring, and every sibling module in the suite is written against it, so the two are
# kept in step deliberately:
#   * `shlex` splits a curl command string into an argv list WITHOUT a shell, and `re`
#     validates the environment-supplied endpoints. Both are load-bearing: together they are
#     why no configurable text in this suite can reach a shell (see _validated_endpoint and
#     send_curl_command).
#   * `collections.namedtuple` gives the request description below its shape, and
#     `urllib.parse.urlsplit` decomposes an endpoint for the per-call re-validation in
#     _validate_endpoint.
#   * `tempfile` is NOT imported, and is correspondingly absent from @dependencies. The
#     template needs it for an indicator-specific YAML-rewrite path that this suite
#     deliberately does not carry; the sink posts its vComponent payloads verbatim, so an
#     import with no call site would be the only lint finding in this module.

# HDMICEC_CMD_BASE resolution order: the environment override wins outright, then the
# suite-local vcomponent_configurations/commands directory when it exists, then the
# on-device /etc path.
_BASE_DIR = Path(__file__).resolve().parent
_LOCAL_HDMICEC_CMD_BASE = _BASE_DIR / "vcomponent_configurations" / "commands"

# The whole vComponent configuration tree, which is the containment boundary every payload
# posted by send_vcomponent_command() must fall inside. Keeping the boundary one level above
# the commands directory admits the sibling hdmicec/ subtree without admitting the rest of
# the filesystem.
_LOCAL_VCOMPONENT_BASE = _BASE_DIR / "vcomponent_configurations"


def _pick_existing_dir(primary, fallback):
    if primary.is_dir():
        return str(primary)
    return fallback


HDMICEC_CMD_BASE = os.environ.get("HDMICEC_CMD_BASE") or _pick_existing_dir(
    _LOCAL_HDMICEC_CMD_BASE,
    "/etc/hdmicec/vcomponent_configurations/commands",
)

# ---------- ENDPOINT VALIDATION ----------
# The five environment variables below are the documented override contract, which means the
# endpoints are external input. curl reads an argument that begins with "-" as an OPTION, so
# an endpoint such as "--config=/tmp/attacker.curlrc" would make curl load a caller-chosen
# configuration and retarget the request; and an endpoint carrying whitespace or shell
# metacharacters is never a legitimate URL. Both are refused here, before any request is
# built, and every argument list additionally places the "--" option terminator immediately
# before the URL so that no endpoint can ever be interpreted as an option.
_ALLOWED_URL_SCHEMES = ("http", "https")

# Characters that cannot appear in a legitimate absolute URL (they must be percent-encoded).
# Their presence means the value is being used to smuggle a second argument or a command.
_FORBIDDEN_URL_CHARACTERS = (
    " ", "\t", "\n", "\r", "\v", "\f", "\0",
    '"', "'", "`", "\\", ";", "|", "&", "$", "<", ">", "(", ")", "{", "}", "*",
)


def _validate_endpoint(url, label):
    '''Validate an endpoint URL and return it unchanged, or raise ValueError.

    Accepts only a plain http/https URL with a host: no other scheme, no embedded
    credentials, no leading dash, and no character that could split the argument or reach a
    shell. Called once per endpoint at import time so a hostile or malformed override fails
    immediately and loudly, and again inside every transport helper so that a value replaced
    at run time cannot bypass the check.
    Args:
        url: Candidate endpoint URL
        label: Name of the environment variable the value came from, used in the message
    Returns:
        The validated URL, unchanged.
    Raises:
        ValueError: when the value is not a plain http/https endpoint.
    '''
    if not isinstance(url, str) or not url:
        raise ValueError(f"{label} must be a non-empty string, got {url!r}")

    if url[0] == "-":
        raise ValueError(
            f"{label} starts with '-' and would be read by curl as an option: {url!r}"
        )

    for character in _FORBIDDEN_URL_CHARACTERS:
        if character in url:
            raise ValueError(
                f"{label} contains the illegal character {character!r}: {url!r}"
            )

    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"{label} must use one of {_ALLOWED_URL_SCHEMES}, got {parts.scheme!r}: {url!r}"
        )
    if not parts.hostname:
        raise ValueError(f"{label} carries no host: {url!r}")
    if parts.username or parts.password:
        raise ValueError(f"{label} must not embed credentials: {url!r}")

    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{label} carries an invalid port: {url!r}") from exc
    if port is not None and not 0 < port < 65536:
        raise ValueError(f"{label} carries an out-of-range port {port}: {url!r}")

    return url


# Endpoint selection for local/QEMU execution.
#
# THE ENDPOINT CONTRACT - SIX ENVIRONMENT KEYS, IN THIS PRECEDENCE
# ---------------------------------------------------------------
# This module is the single source of truth for endpoint resolution in the sink vDevice
# suite and the only place in it that carries a host or port literal. Sibling modules import
# WPEFRAMEWORK_JSONRPC_URL / VCOMPONENT_API_URL from here, so retargeting the whole suite at
# a different device never means editing test code. Six keys are read, and the precedence
# below is exact - the first source set to a non-empty value wins, and the sources after it
# are then never consulted:
#
#   WPEFRAMEWORK_JSONRPC_URL  (1st for the JSON-RPC endpoint) complete URL, used verbatim.
#   JSONRPC_URL               (2nd for the JSON-RPC endpoint) LEGACY ALIAS of the above,
#                             kept so command lines written for the older suites keep
#                             working. It is consulted ONLY when WPEFRAMEWORK_JSONRPC_URL is
#                             unset or empty, and it is not mentioned anywhere else in this
#                             suite; prefer the explicit name in new work.
#   VCOMPONENT_API_URL        (1st for the vComponent endpoint) complete URL, used verbatim.
#   TARGET_HOST               (fallback host for BOTH endpoints; default 127.0.0.1) - the
#                             one place to change when a device moves.
#   JSONRPC_PORT              (fallback port for the JSON-RPC endpoint; default 9998).
#   VCOMPONENT_PORT           (fallback port for the vComponent endpoint; default 8080).
#
# So: an explicit URL beats TARGET_HOST plus a port, and for JSON-RPC the explicit
# WPEFRAMEWORK_JSONRPC_URL beats the legacy JSONRPC_URL alias. A seventh key,
# HDMICEC_CMD_BASE (above), overrides the vComponent YAML command directory, and an eighth,
# HDMICEC_TIMING_ENABLED (below), only decorates log messages; neither takes part in
# endpoint resolution.
#
# A variable that is unset OR empty falls through to the next source, so an accidentally
# blank override behaves as if it were absent rather than blanking the endpoint.
#
# HOW EACH VALUE IS CHECKED, AND WHY IT IS CHECKED HERE
# -----------------------------------------------------
# Every value is validated at DEFINITION time, once, in this module - the only place in the
# suite that carries a host or port literal - so every constant in every sibling module
# inherits the guarantee and no call site has to remember anything. Validation is by
# ALLOWLIST (a full-match pattern per value kind) with a metacharacter denylist as an
# independent second gate: TARGET_HOST must be a hostname, IPv4 address or bracketed IPv6
# literal; the two ports must be decimal and inside 1-65535, so a shape-valid 99999 is still
# refused; and a complete URL override must match a plain http(s)://host[:port][/path].
#
# The transport is argv-based (see send_curl_command and _run_curl: no shell is ever
# involved), so this is fail-closed input hygiene rather than the primary injection control -
# and it is deliberately fail-closed: a malformed override raises at import instead of being
# silently replaced by the default, because a suite that quietly retargets itself after a typo
# would report results for a device nobody asked about.

_HOST_RE = re.compile(r"^[A-Za-z0-9._%\[\]:-]+$")
_PORT_RE = re.compile(r"^[0-9]{1,5}$")
# The path charset here is deliberately NARROWER than RFC 3986 permits, and that is the whole
# point rather than an oversight. RFC 3986 allows sub-delims -- ";", "&", "$", "'", "(", ")",
# "*", "+", "," and "!" -- inside a path, and several of those are shell metacharacters. A URL
# of `http://h:1/x;id` is a perfectly legal URL and also a shell injection once concatenated
# into a command string: the shell runs curl, then runs `id`. This was caught by testing the
# pattern rather than by reading it. Since the only endpoints this suite ever needs are
# `/jsonrpc` and `/api/postKVP`, the charset is restricted to what those require plus
# percent-encoding; query strings are not supported, and a URL needing one would have to come
# through a code change that re-examines the shell-splicing question.
_URL_RE = re.compile(
    r"^https?://[A-Za-z0-9._\[\]:-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/%+@=-]*)?$"
)
# An independent second gate, checked for every value regardless of which pattern applies.
# The regexes above already exclude all of these, so this is unreachable today -- it exists so
# that loosening a pattern later cannot silently admit shell-active text, which is exactly how
# the `;` case above arose in the first place.
_SHELL_METACHARACTERS = set(";&|`$()<>\n\r\t\\\"' *?![]{}#")


def _validated(name, value, pattern, expectation):
    '''Return value if it matches pattern and carries no shell metacharacter.

    Two gates rather than one: `pattern` is the allowlist for this kind of value, and the
    metacharacter set is an independent check applied to every value regardless of pattern.
    The patterns already exclude everything in that set, so the second gate is unreachable
    today - it exists so that loosening a pattern later cannot silently admit shell-active
    text into a value the whole suite is composed from.

    Args:
        name: the environment variable name, so the message says which one to fix
        value: the value as read from the environment
        pattern: compiled allowlist regex the value must match in full
        expectation: human-readable description of what is allowed
    Returns:
        The value unchanged, once it is known to be safe to place in a shell string.
    Raises:
        ValueError: the value does not match, with the name, the value and the expectation.
    '''
    if not isinstance(value, str) or not pattern.match(value):
        raise ValueError(
            f"{name}={value!r} is not an accepted value for the HDMI-CEC sink vDevice suite. "
            f"Expected {expectation}. These values are spliced into the endpoints and argv lists "
            f"every curl invocation in this suite is built from, so they are allowlist-validated "
            f"once here rather than checked at each of the call sites that consume them."
        )
    # Bracketed IPv6 literals are the one accepted form that legitimately contains characters
    # from the metacharacter set, so square brackets are tolerated for the host specifically.
    offending = sorted(set(value) & (_SHELL_METACHARACTERS - set("[]")))
    if offending:
        raise ValueError(
            f"{name}={value!r} contains shell metacharacter(s) {offending!r}. "
            f"Nothing in this suite reaches a shell, but a device endpoint never legitimately "
            f"contains these characters, so the value is refused rather than carried further."
        )
    return value


def _validated_port(name, value):
    '''Return value if it is a decimal TCP port in 1-65535, else raise ValueError.

    The regex alone would admit 99999, which is not a port at all, so the numeric range is
    checked as well -- a stricter allowlist than the shape check on its own.
    '''
    _validated(name, value, _PORT_RE, "1 to 5 decimal digits")
    if not 1 <= int(value) <= 65535:
        raise ValueError(
            f"{name}={value!r} is outside the valid TCP port range 1-65535."
        )
    return value


TARGET_HOST = _validated(
    "TARGET_HOST",
    os.environ.get("TARGET_HOST", "127.0.0.1"),
    _HOST_RE,
    "a hostname, IPv4 address, or bracketed IPv6 address using only letters, digits, "
    "dot, underscore, hyphen, colon and square brackets",
)
JSONRPC_PORT = _validated_port("JSONRPC_PORT", os.environ.get("JSONRPC_PORT", "9998"))
VCOMPONENT_PORT = _validated_port("VCOMPONENT_PORT", os.environ.get("VCOMPONENT_PORT", "8080"))


# Characters that have no place in a plain http(s)://host:port/path endpoint and that a shell
# would treat as syntax. The endpoints published by this module end up as arguments to curl
# and are never handed to a shell (see send_curl_command), so this check is defence in depth
# rather than the primary control - it stops a mistyped or hostile override at the boundary
# instead of forwarding it into a command line. Square brackets are deliberately NOT in the
# set, so an IPv6 endpoint such as http://[::1]:9998/jsonrpc remains usable.
_UNSAFE_ENDPOINT_CHARS = re.compile(r"""[\s;&|`$<>()\\'"*?!{}]""")


def _validated_endpoint(source, value):
    '''Return an endpoint URL unchanged, or raise if it carries shell-active text.
    Args:
        source: Where the value came from, named in the error message
        value: The endpoint URL to check
    Returns:
        The value unchanged when it is a plain endpoint URL.
    Raises:
        ValueError: when the value contains whitespace or a shell metacharacter, which a
                    device endpoint never legitimately does.
    '''
    match = _UNSAFE_ENDPOINT_CHARS.search(value)
    if match:
        raise ValueError(
            f"{source} contains the character {match.group(0)!r}, which is not valid in an "
            f"endpoint URL for this suite. Endpoints must be plain "
            f"http(s)://host:port/path values: {value!r}"
        )
    # Allowlist as well as denylist: the check above says what may not appear, this one says
    # what the whole value must look like, so a value that is merely nonsense (a missing
    # scheme, a five-digit-plus port, a query string) is refused here rather than handed to
    # curl to fail on later.
    if not _URL_RE.match(value):
        raise ValueError(
            f"{source} is not an accepted endpoint for this suite: {value!r}. Expected a plain "
            f"http:// or https:// URL of the form host[:port][/path], with the path restricted to "
            f"unreserved characters - RFC 3986 permits sub-delims such as ';' and '$' inside a "
            f"path and this suite deliberately does not."
        )
    return value


def _resolve_endpoint(names, default, default_source):
    '''Resolve the first environment variable in `names` that holds a value, else the default.
    An unset OR empty variable falls through to the next source, which is the precedence the
    suite has always documented; whichever value wins is validated before it is published.
    Args:
        names: Environment variable names in precedence order, highest first
        default: URL composed from TARGET_HOST and the port default
        default_source: Names behind `default`, for the error message
    Returns:
        The validated winning URL.
    '''
    for name in names:
        raw = os.environ.get(name)
        if raw:
            return _validated_endpoint(name, raw)
    return _validated_endpoint(default_source, default)


WPEFRAMEWORK_JSONRPC_URL = _resolve_endpoint(
    ("WPEFRAMEWORK_JSONRPC_URL", "JSONRPC_URL"),
    f"http://{TARGET_HOST}:{JSONRPC_PORT}/jsonrpc",
    "TARGET_HOST/JSONRPC_PORT",
)
VCOMPONENT_API_URL = _resolve_endpoint(
    ("VCOMPONENT_API_URL",),
    f"http://{TARGET_HOST}:{VCOMPONENT_PORT}/api/postKVP",
    "TARGET_HOST/VCOMPONENT_PORT",
)


RESET = "\033[0m"
BOLD = "\033[1m"

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"

def log_info(msg):
    print(f"{CYAN}{msg}{RESET}")

def log_success(msg):
    print(f"{GREEN}{BOLD}{msg}{RESET}")

def log_warning(msg):
    print(f"{YELLOW}{msg}{RESET}")

def log_error(msg):
    print(f"{RED}{BOLD}{msg}{RESET}")


def log_with_timing(msg, elapsed_time):
    '''Decorate a message with its elapsed time when HDMICEC_TIMING_ENABLED is set.
    The decorated text is returned rather than printed, so the caller keeps the choice
    of log level to route it through.
    Args:
        msg: Base message without timing
        elapsed_time: Elapsed time in seconds (float)
    Returns:
        "<msg> time consumed: <elapsed>s" when HDMICEC_TIMING_ENABLED is set in the
        environment, otherwise msg unchanged.
    '''
    if os.environ.get("HDMICEC_TIMING_ENABLED"):
        return f"{msg} time consumed: {elapsed_time:.3f}s"
    return msg


# ---------- REQUEST DESCRIPTION ----------
# A request is DATA, never a command line - which is what keeps the suite's request definitions
# free of quoting concerns: there is no command string anywhere for an endpoint or a parameter
# value to escape from.
#
# Two shapes express that, and both are supported deliberately. HdmiCECSink_Curl.py publishes
# each sink API as a ready-made ARGV LIST, which send_curl_command runs verbatim; a caller that
# would rather describe a request than build one uses this JsonRpcRequest and hands its fields
# to send_jsonrpc_command, which composes the argv itself. Neither route ever produces a command
# string for a shell to interpret.
JsonRpcRequest = namedtuple(
    "JsonRpcRequest",
    ["method", "params", "request_id", "timeout"],
)
# params defaults to None (omitted from the payload), the id to the value the suite's request
# definitions use, and the budget to five seconds.
JsonRpcRequest.__new__.__defaults__ = (None, 42, 5)

# Sentinel returned by send_curl_command when no usable response was obtained. Callers detect
# a transport failure with response.startswith("< No response"), so this string is byte-exact.
NO_RESPONSE_SENTINEL = "< No response from WPEFramework >"

# How long a single legacy-string exchange may take before it is abandoned. The command
# constants also pass curl's own --max-time; this is the outer bound that still applies if a
# command omits it, so a hung endpoint cannot stall a suite run indefinitely.
CURL_TIMEOUT_SECONDS = 15

# Connection budget applied to every request in addition to the caller's overall budget, and
# the extra grace given to subprocess.run so that curl's own timeout fires first and yields a
# diagnosable exit status rather than an opaque kill.
_CONNECT_TIMEOUT_SECONDS = 5
_SUBPROCESS_TIMEOUT_MARGIN_SECONDS = 5

# Upper bound on a vComponent payload this module will read and post. The suite's own YAML
# documents are a few kilobytes; the cap turns "an unexpected file was approved" into a clean
# refusal instead of an unbounded read.
_VCOMPONENT_MAX_PAYLOAD_BYTES = 1024 * 1024


def _normalise_timeout(timeout, default=5):
    '''Return a positive integer second budget, falling back to default.'''
    try:
        value = int(timeout)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _run_curl(argv, timeout, input_bytes=None):
    '''Run curl as an argument list with no shell, bounded in time.

    This is the ONLY place in the suite that starts a process. shell=False means the argument
    list is passed to execve untouched, so no element of it - endpoint, payload or parameter
    value - can be interpreted as a command, a redirection or a second argument.
    Args:
        argv: Complete argument list, already carrying "--" before its URL
        timeout: curl --max-time budget in seconds, also used to bound subprocess.run
        input_bytes: Optional request body delivered on curl's stdin
    Returns:
        (ok, returncode, stdout, stderr) where ok is True only when curl exited 0.
        returncode is None when curl could not be run or exceeded its bound.
    '''
    try:
        completed = subprocess.run(
            argv,
            check=False,
            shell=False,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + _SUBPROCESS_TIMEOUT_MARGIN_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, None, "", f"curl exceeded its {timeout}s budget and was terminated"
    except (OSError, ValueError) as exc:
        return False, None, "", f"curl could not be executed: {exc}"

    stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
    return completed.returncode == 0, completed.returncode, stdout, stderr


def _jsonrpc_argv(payload, timeout):
    '''Build the argument list for one JSON-RPC POST.

    The endpoint is re-validated here rather than trusted from module scope, and "--" is
    placed immediately before it so curl cannot read it as an option.
    '''
    url = _validate_endpoint(WPEFRAMEWORK_JSONRPC_URL, "WPEFRAMEWORK_JSONRPC_URL")
    return [
        "curl", "-sS",
        "--connect-timeout", str(min(_CONNECT_TIMEOUT_SECONDS, timeout)),
        "--max-time", str(timeout),
        "-H", "Content-Type: application/json",
        "-X", "POST",
        "--data", json.dumps(payload),
        "--",
        url,
    ]


def _dispatch_jsonrpc(method, params, request_id, timeout):
    '''Post one JSON-RPC request and return (ok, body, diagnostic).

    ok is True only when curl exited 0 AND a non-empty body came back. A transport failure
    never yields a body, so no caller can mistake an unreachable device for an answer.
    '''
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    ok, returncode, stdout, stderr = _run_curl(
        _jsonrpc_argv(payload, timeout), timeout
    )
    if not ok:
        detail = stderr.strip() or stdout.strip()
        diagnostic = f"curl exited {returncode}"
        if detail:
            diagnostic = f"{diagnostic}: {detail}"
        return False, "", diagnostic

    body = stdout.strip()
    if not body:
        return False, "", "curl exited 0 but the response body was empty"

    return True, body, ""


def _parse_jsonrpc_envelope(body):
    '''Return the decoded JSON-RPC 2.0 envelope, or None when the body is not one.

    A complete envelope is a JSON object carrying "jsonrpc": "2.0" together with either a
    result or an error member. An error envelope IS a valid response - the device answered -
    so it is returned to the caller rather than suppressed.
    '''
    try:
        decoded = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    if decoded.get("jsonrpc") != "2.0":
        return None
    if "result" not in decoded and "error" not in decoded:
        return None
    return decoded


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
    budget = _normalise_timeout(timeout)
    # A transport failure, an empty body or a body that is not a JSON-RPC envelope all yield
    # None. The caller is never handed a synthesised success object, so an unreachable device
    # reads as a failure rather than as a pass.
    ok, body, diagnostic = _dispatch_jsonrpc(method, params, request_id, budget)
    if not ok:
        log_warning(f"Inside Utils.py : {method} not dispatched - {diagnostic}")
        return None
    return _parse_jsonrpc_envelope(body)


def activate_plugin(callsign):
    '''Activate an RDK plugin via Controller.1.activate.
    Returns True on success, False otherwise.
    Args:
        callsign: Plugin callsign to activate, e.g. "org.rdk.HdmiCecSink"
    Returns:
        True when the controller answered with a result, False on any error,
        on a missing result member, or when the controller was unreachable.
    '''
    # The request id is the one the source plugin's vDevice suite already uses for this
    # call - entservices-hdmicecsource/Tests/vDeviceTests/SuitManager.py and its utils.py
    # both send 1234567890 - and the planned sink SuitManager.py will carry the same value
    # in its own copy of this helper. Keeping them in step makes activation calls easy to
    # correlate in a WPEFramework trace regardless of which module issued them.
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
    '''Run a curl command and return its JSON response line as a string.

    Accepts either the complete curl command STRING that HdmiCECSink_Curl.py exports, or an
    already-built argv sequence. Either way the command is executed WITHOUT A SHELL: a
    string is split into an argv list with shlex and handed straight to subprocess.run, so
    no part of it - least of all the environment-derived endpoint URL appended to every
    constant in HdmiCECSink_Curl.py - can ever be interpreted as shell syntax. A URL
    carrying `;`, `&&` or `$(...)` therefore becomes inert argv text that curl rejects,
    rather than a second command that runs. Shell features (pipes, redirection, command
    substitution, globbing) are consequently not supported here, by design; nothing in this
    suite uses them.

    The response is returned as a raw string, not a parsed object: callers run their own
    json.loads on it so that they can distinguish a malformed payload from a missing one.
    Any failure - a command that cannot be tokenised, a curl binary that is absent, a
    transport error, an unparsable body, or no body at all - yields the
    "< No response from WPEFramework >" sentinel, which callers detect with
    response.startswith("< No response").
    Args:
        curl_command: Complete curl command string, or a sequence of argv tokens
    Returns:
        The first response line that parses as JSON, otherwise the sentinel string.
    '''
    output_response = NO_RESPONSE_SENTINEL
    try:
        if isinstance(curl_command, (list, tuple)):
            # Already structured: use it as argv verbatim, which is the shape a caller
            # should prefer when it is building a command itself.
            argv = [str(token) for token in curl_command]
        else:
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

            # shlex.split applies the shell's QUOTING rules without a shell being involved,
            # so the argv list matches what /bin/sh would have passed to curl while nothing
            # is left to interpret operators or expansions.
            argv = shlex.split(curl_command)

        if not argv:
            raise ValueError("empty curl command")

        # shell=False (the default) is the whole point.  stdout is captured because the JSON
        # line is parsed out of it, and stderr is captured so that curl's own explanation can be
        # printed with the exit status when nothing usable came back - a failure that is reported
        # rather than silently flattened into the sentinel.  The call is bounded by
        # CURL_TIMEOUT_SECONDS so a hung endpoint cannot stall the run, and subprocess.run waits
        # for the child and closes its pipes before returning, so no process handle survives this
        # call even when the command fails - the os.popen form it replaces left the handle to the
        # garbage collector.
        completed = subprocess.run(
            argv,
            check=False,
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CURL_TIMEOUT_SECONDS,
        )

        # Find the line that is a valid JSON for extracting only the json response.
        # keepends=True preserves the trailing newline the previous readlines() form
        # returned, so callers see byte-identical strings.
        for line in (completed.stdout or "").splitlines(keepends=True):
            try:
                json.loads(line)
            except json.JSONDecodeError:
                # Not JSON - keep looking at the remaining lines.
                continue
            output_response = line
            break

        # Report the documented sentinel when nothing usable came back, including the case
        # where curl itself failed and said why on stderr.
        if len(output_response) < 5:
            output_response = NO_RESPONSE_SENTINEL
            stderr = (completed.stderr or "").strip()
            if completed.returncode != 0 and stderr:
                print(
                    "Inside Utils.py : curl exited "
                    f"{completed.returncode}: {stderr}"
                )
    except subprocess.TimeoutExpired:
        output_response = NO_RESPONSE_SENTINEL
        print(
            "Inside Utils.py : send_curl_command timed out after "
            f"{CURL_TIMEOUT_SECONDS}s"
        )
    except Exception as exc:
        # The sentinel is assigned HERE rather than returned from a finally block. Returning
        # from finally discards whatever the except branch decided and would hand the caller
        # an empty string on failure - indistinguishable from a successful empty reply.
        # The docstring promises the sentinel on any failure - a caller testing
        # response.startswith("< No response") must see a transport or tokenisation failure as
        # exactly that, not as an empty string.
        output_response = NO_RESPONSE_SENTINEL
        print(f"Inside Utils.py : Exception in send_curl_command function: {exc}")

    # Returned after the try/except rather than from a finally block: a `return` inside
    # `finally` discards whatever the except branch assigned, which is what previously made
    # the documented sentinel unreachable.
    return output_response


def _approved_configuration_roots():
    '''Return the resolved directories a vComponent payload may be read from.

    Two roots: the suite's own vcomponent_configurations tree, and the configured command
    base (HDMICEC_CMD_BASE, which a deployment may point at /etc/hdmicec/...). Both are
    resolved, so containment is decided on canonical paths.
    '''
    roots = []
    for candidate in (_LOCAL_VCOMPONENT_BASE, Path(HDMICEC_CMD_BASE)):
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return roots


def _approved_payload_path(yaml_file_path):
    '''Resolve a YAML command path and return it, or raise ValueError explaining the refusal.

    curl's "@path" form would happily upload any file the test user can read, so the path is
    treated as untrusted input and three independent checks are applied: the supplied path
    itself must not be a symbolic link, its canonical form must sit inside an approved
    configuration root and be a regular file, and no directory between that root and the file
    may be a symbolic link. Together they close arbitrary-file disclosure (a path such as
    /etc/shadow, or a link pointing at one) and the "link planted inside the suite tree"
    variant, whichever component the link occupies.
    '''
    roots = _approved_configuration_roots()
    if not roots:
        raise ValueError(
            "no vComponent configuration root is available; expected "
            f"{_LOCAL_VCOMPONENT_BASE} or {HDMICEC_CMD_BASE}"
        )

    # The supplied path is examined BEFORE resolution: resolve() follows links, so a check
    # made afterwards could never see one.
    supplied = Path(os.path.abspath(yaml_file_path))
    if supplied.is_symlink():
        raise ValueError(
            f"refused to post {yaml_file_path}: {supplied} is a symbolic link"
        )

    try:
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"YAML file not found: {yaml_file_path}") from exc

    container = None
    for root in roots:
        if resolved == root or root in resolved.parents:
            container = root
            break
    if container is None:
        raise ValueError(
            f"refused to post {yaml_file_path}: it resolves to {resolved}, outside the "
            f"approved vComponent configuration roots {[str(root) for root in roots]}"
        )

    # A symbolic link standing in for one of the directories between the root and the file
    # would otherwise smuggle in a path the containment check above cannot see, because by
    # then it has already been followed.
    try:
        relative = supplied.relative_to(container)
    except ValueError:
        relative = None
    if relative is not None:
        walked = container
        for part in relative.parts:
            walked = walked / part
            if walked.is_symlink():
                raise ValueError(
                    f"refused to post {yaml_file_path}: {walked} is a symbolic link"
                )

    if not resolved.is_file():
        raise ValueError(f"refused to post {yaml_file_path}: {resolved} is not a regular file")

    return resolved


def _read_payload(path):
    '''Read an approved YAML payload, refusing anything larger than the documented cap.'''
    with open(path, "rb") as handle:
        payload = handle.read(_VCOMPONENT_MAX_PAYLOAD_BYTES + 1)
    if len(payload) > _VCOMPONENT_MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"refused to post {path}: larger than the {_VCOMPONENT_MAX_PAYLOAD_BYTES} byte cap"
        )
    return payload


def send_vcomponent_command(yaml_file_path, timeout=10):
    '''Post a YAML command file to the vComponent HTTP API.
    Uses: curl -sS -X POST -H "Content-Type: application/x-yaml"
               --data-binary @- <VCOMPONENT_API_URL>   (payload on stdin)
    Returns (http_code: int, body: str) tuple.
    http_code 200 indicates success.

    The YAML payload is posted verbatim, read by this module from a path it has approved
    rather than handed to curl as a filename, so no caller can turn the helper into a
    file-disclosure primitive. This suite carries no key-rewriting or alternate-plugin
    fallback path, and no curl exit status is reinterpreted, so the code a caller receives
    reflects what the vComponent actually answered: an absent, silent or failing vComponent
    is never reported as success.
    Args:
        yaml_file_path: Path to a YAML command document inside the suite's
                        vcomponent_configurations tree (or the configured HDMICEC_CMD_BASE)
        timeout: curl --max-time budget in seconds
    Returns:
        (code, body) where code is the HTTP status the vComponent actually returned - 200
        when it accepted the payload - and 0 when the request never completed, the file is
        missing, the path was refused, or curl itself failed. body carries the response or
        the diagnostic explaining the refusal.
    '''
    budget = _normalise_timeout(timeout, default=10)

    def _post_payload(payload):
        url = _validate_endpoint(VCOMPONENT_API_URL, "VCOMPONENT_API_URL")
        cmd = [
            "curl", "-sS", "-w", "\n%{http_code}",
            "--connect-timeout", str(min(_CONNECT_TIMEOUT_SECONDS, budget)),
            "--max-time", str(budget),
            "-X", "POST",
            "-H", "Content-Type: application/x-yaml",
            "--data-binary", "@-",
            "--",
            url,
        ]

        ok, returncode, stdout, stderr = _run_curl(cmd, budget, input_bytes=payload)
        if not ok:
            # Every curl transport failure - refused connection, empty reply, timeout - is a
            # failure. Reporting one as an HTTP 200 would manufacture a pass out of a server
            # that never answered.
            #
            # The one case worth naming, because it is the reason a synthetic 200 is tempting:
            # some vComponent builds apply the posted YAML and then close the connection
            # without answering, which curl reports as CURLE_GOT_NOTHING (exit 52, "Empty
            # reply from server").  Such a run really may have taken effect - but the server
            # said nothing, so this returns 0 with curl's own diagnosis and lets the caller
            # decide, rather than inventing a status the vComponent never sent.  A caller that
            # wants to proceed on that specific case can test for `curl exited 52` in the body
            # and then verify the effect through the middleware APIs.
            detail = stderr.strip() or stdout.strip() or "no diagnostic"
            return 0, f"curl exited {returncode}: {detail}"

        # curl output format is: <body>\n<http_code> from "-w \n%{http_code}"
        # Keep split robust even when body is empty (e.g. "\n200").
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
        if http_code == 0:
            detail = stderr.strip()
            if detail:
                body = detail
        return http_code, body

    try:
        # Containment before the file is read or posted anywhere.
        #
        # The path a caller passes is normally HDMICEC_CMD_BASE joined with a fixed filename from
        # a test-case module, but HDMICEC_CMD_BASE is environment-overridable and the filename
        # arrives as a plain string, so `../` segments can reach outside the suite. The exposure is
        # already narrow - the argv form below means no shell is involved and the payload is only
        # ever POSTed, never executed - but "narrow" is not "bounded". _approved_payload_path makes
        # it bounded: it resolves the path first, so symlinks and `..` are collapsed before any
        # comparison, then requires the result to sit under this suite's own configuration tree or
        # under the configured command base, and to be a regular file reached through no symbolic
        # link at any component.
        approved = _approved_payload_path(yaml_file_path)
        payload = _read_payload(approved)
    except ValueError as exc:
        return 0, str(exc)
    except OSError as exc:
        return 0, f"could not read {yaml_file_path}: {exc}"

    try:
        return _post_payload(payload)
    except ValueError as exc:
        # A rejected endpoint is a configuration defect, reported rather than dispatched.
        print(f"Inside Utils.py : Exception in send_vcomponent_command: {exc}")
        return 0, str(exc)
