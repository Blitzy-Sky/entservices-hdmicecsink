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
 *  - Standard Python libraries: os, json, selectors, shlex, stat, subprocess, time, re,
 *    collections, pathlib, urllib.parse
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
import selectors
import shlex
import stat
import subprocess
import time
import re
from collections import namedtuple
from pathlib import Path
from urllib.parse import urlsplit



# Sentinel returned by send_curl_command when no usable response was obtained. Callers detect
# a transport failure with response.startswith("< No response"), so this string is byte-exact.
# ── CEC bus pacing: the suite's only deliberately timed constructs ──────────────────────────────
#
# DEFERRED, and named rather than scattered as magic numbers so the deferral is auditable.
#
# Every other wait in this suite was replaced with an observable condition: JSON-RPC and vComponent
# requests are synchronous, so their reply is the completion signal (see send_curl_command and
# send_vcomponent_command below), plugin readiness is observed through Controller.1.status (see
# await_plugin_ready), and device state is observed by re-reading getDeviceList until it reports what
# the test is waiting for.
#
# These four values are what remains, and they are all the SAME thing: the gap after a CEC frame has
# been handed to the emulator. Nothing observable exists to wait on there. A vComponent POST's HTTP
# 200 confirms only that the emulator accepted the document - not that the frame was carried on the
# bus, decoded, and absorbed by the middleware - and the handlers these frames exercise frequently
# have no observable outcome at all: several are bare `return` guards that increment no counter,
# change no state and raise no notification. Where an outcome IS observable the test waits for that
# outcome instead of for one of these values.
#
# Removing them would rest on an assumption that cannot be checked from here: that frames posted
# back to back are never coalesced or dropped by the transport before the middleware sees them. So
# they are recorded as deferred rather than deleted or guessed at, and they are the specific reason
# this suite is reported as not fully wait-free. Closing them needs a per-frame acknowledgement the
# device does not expose.
CEC_SHORT_PACING_SECONDS = 0.2      # a fixture with no state-visible outcome
CEC_FRAME_PACING_SECONDS = 1.0      # one injected frame
CEC_PIPELINE_PACING_SECONDS = 1.5   # a frame whose effect must reach the device list
CEC_TOPOLOGY_PACING_SECONDS = 2.0   # a device add or remove - the longest path through the pipeline


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
#   * `stat` supplies S_ISREG for the fstat check in _read_payload, which is what makes the
#     payload decision rest on an open DESCRIPTOR rather than on a path that can be swapped
#     between the check and the open.
#   * `selectors` and `time` are what make the response reader BOUNDED: the reader multiplexes
#     curl's stdout, stderr and stdin against a monotonic deadline and stops at a byte ceiling,
#     which subprocess.run() cannot do because it accumulates whatever the child produces
#     until the child exits.  See _run_curl.
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

# Ceiling on the length of one log line. Deliberately far above anything this suite composes -
# the longest message it builds is a few hundred characters, and the widest evidence line is
# three device-list snapshots side by side - so no legitimate message is ever near it, while a
# response that arrived unbounded from a device still cannot become a screen-length log entry.
_LOG_LINE_MAX_CHARS = 16384


def _guard_log_line(msg):
    '''Neutralise control characters in a composed log line, and bound its length.

    This is the floor under every log call in the suite, and it exists because a log line is
    EVIDENCE. A device-level run leaves no artefact but its console transcript, and parts of
    almost every line in that transcript were composed from data a device sent back. A single
    ESC reaching a terminal lets that data reposition the cursor, clear the lines above it, or
    repaint a refusal as a tick; a single newline lets one response become several log records,
    one of which can be made to look exactly like this suite's own output.

    Applying the guard HERE rather than at each of the several hundred call sites is deliberate.
    Whether a particular interpolation is dangerous turns on a distinction that is easy to get
    wrong and easier to lose: a bare string from the wire prints its control bytes literally,
    while the same string inside a dict or rendered with !r is escaped already by Python's own
    repr. Enumerating the dangerous sites correctly once is possible; keeping that enumeration
    correct as the suite grows is not. At this level the property holds for every call, present
    and future, by construction.

    What it does NOT do is escape everything: printable text and non-ASCII glyphs pass through
    untouched, so this suite's own tick, warning and cross marks still render, and text that was
    already escaped by utils.sanitise_for_log is left exactly as that function rendered it. Only
    C0 controls, DEL and C1 controls are replaced, each by a visible \\xNN escape, and a
    backslash already present is NOT doubled here - doing so would double the escapes
    sanitise_for_log produced and make its output unreadable. sanitise_for_log remains the
    stronger, fully-escaping, tightly-bounded rendering for an individual value from the wire;
    this is the last line of defence for whatever reaches a log call by another route.

    Args:
        msg: The composed message. A non-str is rendered with str() first.
    Returns:
        Single-line text with no control characters, truncated to _LOG_LINE_MAX_CHARS with an
        explicit marker when it was longer.
    '''
    if not isinstance(msg, str):
        try:
            msg = str(msg)
        except Exception:  # pragma: no cover - defensive; a __str__ that raises is pathological
            return "<unprintable log message>"

    dropped = len(msg) - _LOG_LINE_MAX_CHARS
    if dropped > 0:
        msg = f"{msg[:_LOG_LINE_MAX_CHARS]}...[+{dropped} chars truncated]"

    if not any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in msg):
        # The common case by a wide margin, and identity for every message this suite composes
        # itself: scan once and return the original object rather than rebuilding it.
        return msg

    return "".join(
        f"\\x{ord(character):02x}"
        if ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
        else character
        for character in msg
    )


def log_info(msg):
    print(f"{CYAN}{_guard_log_line(msg)}{RESET}")

def log_success(msg):
    print(f"{GREEN}{BOLD}{_guard_log_line(msg)}{RESET}")

def log_warning(msg):
    print(f"{YELLOW}{_guard_log_line(msg)}{RESET}")

def log_error(msg):
    print(f"{RED}{BOLD}{_guard_log_line(msg)}{RESET}")


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


# Default character budget applied to a remote-derived string before it is logged. The
# vComponent's diagnostics and this module's own refusal explanations are a line or two; the
# budget keeps a hostile or malfunctioning endpoint from filling the run log with one response.
LOGGED_VALUE_MAX_CHARS = 512


def sanitise_for_log(value, max_chars=LOGGED_VALUE_MAX_CHARS):
    '''Render a remote-derived value as bounded, escaped, pure-ASCII text fit for a console.

    Every string in a run log that came off the wire passes through here first. The reason is
    log integrity, not tidiness: an endpoint that answers with terminal control sequences can
    otherwise reposition the cursor, clear what a previous line said, or repaint a failure as a
    tick, and the console transcript is the only evidence a device-level run leaves behind. An
    ESC, a carriage return or a backspace reaching a terminal is what makes that possible, so
    none of them reaches one from here.

    The escaping is total rather than selective. Only the printable ASCII range 0x20-0x7E
    survives literally, and a literal backslash is doubled so the escapes it introduces cannot
    be forged by a body that contains "\\x1b" as text. Everything else - C0 and C1 controls,
    DEL, newlines, tabs, and every non-ASCII code point - is replaced by a \\xNN, \\uNNNN or
    \\UNNNNNNNN escape. The result is single-line by construction, so one response can never
    become several log lines, and a body cannot fabricate a line that looks like this suite's
    own output.

    Args:
        value: Any object. A str is used as-is; anything else is rendered with str() first, so
               an HTTP status code or a parsed fragment can be passed in without ceremony.
        max_chars: Maximum number of INPUT characters rendered. The remainder is dropped and
                   its length reported, so a truncated body is visibly truncated rather than
                   silently short. Because one input character can expand to at most ten output
                   characters, the returned text is bounded by 10*max_chars plus the marker.
    Returns:
        Escaped ASCII text, with "...[+N chars truncated]" appended when the input was longer
        than the budget. Never raises: an object whose str() raises renders as "<unprintable>".
    '''
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:  # pragma: no cover - defensive; a __str__ that raises is pathological
            return "<unprintable>"

    budget = max_chars if isinstance(max_chars, int) and max_chars > 0 else LOGGED_VALUE_MAX_CHARS
    dropped = len(value) - budget
    rendered = []
    for character in value[:budget]:
        code = ord(character)
        if character == "\\":
            rendered.append("\\\\")
        elif 0x20 <= code <= 0x7E:
            rendered.append(character)
        elif code <= 0xFF:
            rendered.append(f"\\x{code:02x}")
        elif code <= 0xFFFF:
            rendered.append(f"\\u{code:04x}")
        else:
            rendered.append(f"\\U{code:08x}")

    text = "".join(rendered)
    if dropped > 0:
        text = f"{text}...[+{dropped} chars truncated]"
    return text


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


# Upper bound on how much a single curl invocation may hand back on stdout and stderr
# together.  Every response this suite reads is a JSON-RPC envelope or a short vComponent
# acknowledgement - kilobytes - so the ceiling exists purely to bound a hostile or broken
# endpoint that streams at network speed for the whole timeout window.  It matches the payload
# cap deliberately: the same order of magnitude is generous for anything legitimate.
_MAX_RESPONSE_BYTES = 1024 * 1024

# Read granularity for the bounded reader.  One page-ish chunk per readable event keeps the
# loop responsive to the deadline without a syscall per byte.
_READ_CHUNK_BYTES = 65536


def _terminate_child(proc):
    '''Stop a curl that is still running, and reap it, without ever blocking indefinitely.

    SIGTERM first, because curl exits promptly on it and a terminated child yields a
    diagnosable status; SIGKILL only if it is still there after a short grace period.  The
    process is always waited for: an unreaped child would otherwise stay a zombie for the rest
    of the suite run, and its pipe file descriptors would stay open in this process.
    '''
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        # Nothing further is available at this level; the finally block in _run_curl closes
        # the pipes either way, so no descriptor is leaked even in this case.
        pass


def _pump_child(proc, deadline, input_bytes, max_bytes):
    '''Move bytes to and from a running curl under a byte ceiling and a wall-clock deadline.

    WHY THIS EXISTS, rather than subprocess.run(..., capture_output=True): run() reads until
    the child closes its pipes and accumulates every byte in this process, with no ceiling.  A
    malicious or merely broken endpoint can therefore stream for the whole timeout window and
    exhaust this process's memory before the timeout ever fires - the timeout bounds the
    DURATION of the read, never its SIZE.  A suite that dies of memory exhaustion reports
    nothing at all, which is a worse failure than the transport error it was trying to observe.

    So stdout and stderr are read incrementally through a selector, the running total is
    checked after every chunk, and the child is killed the moment the ceiling is crossed.
    stdin is written through the SAME selector rather than up front: a server that never reads
    the request body would otherwise block this process in write() once the pipe buffer filled,
    which is the same denial of service arriving from the other direction.

    Returns:
        (stdout_bytes, stderr_bytes, overflow, timed_out)
    '''
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    overflow = False
    timed_out = False
    pending = memoryview(input_bytes) if input_bytes else None

    selector = selectors.DefaultSelector()
    try:
        for name in ("stdout", "stderr"):
            stream = getattr(proc, name)
            if stream is not None:
                selector.register(stream.fileno(), selectors.EVENT_READ, name)
        if pending is not None and proc.stdin is not None:
            selector.register(proc.stdin.fileno(), selectors.EVENT_WRITE, "stdin")
        elif proc.stdin is not None:
            proc.stdin.close()

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            # Capped so the deadline is re-evaluated regularly even on a silent connection.
            for key, mask in selector.select(timeout=min(remaining, 0.5)):
                which = key.data
                if which == "stdin":
                    try:
                        written = os.write(key.fd, pending[:_READ_CHUNK_BYTES])
                    except BrokenPipeError:
                        # curl exited or stopped reading; nothing more can be delivered.
                        selector.unregister(key.fd)
                        _close_quietly(proc.stdin)
                        continue
                    except OSError:
                        selector.unregister(key.fd)
                        _close_quietly(proc.stdin)
                        continue
                    pending = pending[written:]
                    if not pending:
                        selector.unregister(key.fd)
                        # EOF on the request body is what tells curl the POST is complete.
                        _close_quietly(proc.stdin)
                    continue

                try:
                    chunk = os.read(key.fd, _READ_CHUNK_BYTES)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                captured[which].extend(chunk)
                total += len(chunk)
                if total > max_bytes:
                    overflow = True
                    break
            if overflow:
                break
    finally:
        selector.close()

    return bytes(captured["stdout"]), bytes(captured["stderr"]), overflow, timed_out


def _close_quietly(stream):
    '''Close a pipe end, tolerating one that is already closed or already broken.'''
    if stream is None:
        return
    try:
        stream.close()
    except (OSError, ValueError):
        pass


def _run_curl(argv, timeout, input_bytes=None):
    '''Run curl as an argument list with no shell, bounded in time AND in bytes.

    This is the ONLY place in the suite that starts a process. shell=False means the argument
    list is passed to execve untouched, so no element of it - endpoint, payload or parameter
    value - can be interpreted as a command, a redirection or a second argument.

    The response is read through _pump_child, which stops at _MAX_RESPONSE_BYTES and kills the
    child rather than accumulating whatever an endpoint chooses to send.  An overflow is
    reported as a FAILURE with its own diagnostic: a truncated body is not an answer, and
    handing back the first megabyte of a stream as though it were a response would let a
    hostile endpoint decide what this suite believes.
    Args:
        argv: Complete argument list, already carrying "--" before its URL where applicable
        timeout: curl --max-time budget in seconds, also used to bound the read loop
        input_bytes: Optional request body delivered on curl's stdin
    Returns:
        (ok, returncode, stdout, stderr) where ok is True only when curl exited 0 within its
        budget and produced no more than the byte ceiling.  returncode is None when curl could
        not be run, exceeded its bound, or was killed for overflowing.
    '''
    deadline = time.monotonic() + timeout + _SUBPROCESS_TIMEOUT_MARGIN_SECONDS
    try:
        proc = subprocess.Popen(
            argv,
            shell=False,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        return False, None, "", f"curl could not be executed: {exc}"

    try:
        stdout_bytes, stderr_bytes, overflow, timed_out = _pump_child(
            proc, deadline, input_bytes, _MAX_RESPONSE_BYTES
        )
        if overflow:
            _terminate_child(proc)
            return (
                False,
                None,
                "",
                f"the endpoint sent more than the {_MAX_RESPONSE_BYTES} byte response ceiling; "
                "curl was terminated and the partial body discarded",
            )
        if timed_out:
            _terminate_child(proc)
            return False, None, "", f"curl exceeded its {timeout}s budget and was terminated"

        # Both pipes reached EOF, so the child is finishing; still bounded, because a curl that
        # closed its outputs and then hung would otherwise wait here for ever.
        remaining = max(deadline - time.monotonic(), 0.1)
        try:
            returncode = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate_child(proc)
            return False, None, "", f"curl exceeded its {timeout}s budget and was terminated"
    finally:
        # Always: reap the child if it is somehow still running, and close every pipe this
        # process holds.  subprocess.run() did both implicitly; Popen does not.
        _terminate_child(proc)
        _close_quietly(proc.stdin)
        _close_quietly(proc.stdout)
        _close_quietly(proc.stderr)

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return returncode == 0, returncode, stdout, stderr


# Written by curl at the very end of its output because of "-w".  The body is everything
# before it, so the marker is a newline plus the numeric status and nothing else; splitting on
# the LAST newline keeps a body that itself contains newlines intact.
_HTTP_CODE_WRITE_OUT = "\n%{http_code}"


def _split_http_status(stdout):
    '''Split curl output produced with _HTTP_CODE_WRITE_OUT into (body, status).

    Returns status None when no numeric status is present, which is itself a failure: it means
    curl did not complete a request/response exchange, so there is nothing to believe about
    whatever text did arrive.
    '''
    if not stdout:
        return "", None
    body, _, tail = stdout.rpartition("\n")
    candidate = tail.strip()
    if candidate.isdigit():
        return body, int(candidate)
    # No trailing status: the whole output is unattributed text.
    return stdout, None


def _jsonrpc_argv(payload, timeout):
    '''Build the argument list for one JSON-RPC POST.

    The endpoint is re-validated here rather than trusted from module scope, and "--" is
    placed immediately before it so curl cannot read it as an option.

    "-w" is included so the HTTP STATUS comes back alongside the body.  Without it curl exits 0
    for any completed exchange, including a 500 or a 404 that carries a body - and a body is
    exactly what a server returns with an error status, so "curl exited 0 and something came
    back" is not evidence that the request was served.
    '''
    url = _validate_endpoint(WPEFRAMEWORK_JSONRPC_URL, "WPEFRAMEWORK_JSONRPC_URL")
    return [
        "curl", "-sS",
        "-w", _HTTP_CODE_WRITE_OUT,
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

    THREE independent conditions must all hold before a body is handed back, because each one
    fails in a way the others cannot see:

      * curl exited 0.  A transport failure never yields a body.
      * the HTTP status is 2xx.  curl exits 0 for a COMPLETED exchange whatever the status, so
        a 500 or a 404 carrying a JSON body used to be returned as an answer - and an error
        status is precisely when a server sends a body.  A 401 page or a proxy's 502 document
        is not a JSON-RPC response.
      * the body is non-empty.

    The remaining two conditions - that the body is exactly one valid JSON-RPC envelope, and
    that its id matches the one sent - are enforced by the caller against the parsed envelope,
    where the request id is known.
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

    body_text, status = _split_http_status(stdout)
    if status is None:
        return False, "", (
            "curl exited 0 but reported no HTTP status, so no request/response exchange "
            "completed"
        )
    if not 200 <= status < 300:
        detail = body_text.strip()
        if len(detail) > 200:
            detail = detail[:200] + "..."
        diagnostic = f"the endpoint answered HTTP {status}, which is not a success status"
        if detail:
            diagnostic = f"{diagnostic}; body: {detail!r}"
        return False, "", diagnostic

    body = body_text.strip()
    if not body:
        return False, "", f"the endpoint answered HTTP {status} with an empty body"

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
    # A transport failure, a non-2xx status, an empty body, a body that is not a JSON-RPC
    # envelope, or an envelope answering a DIFFERENT request all yield None. The caller is never
    # handed a synthesised success object, so an unreachable or misbehaving device reads as a
    # failure rather than as a pass.
    ok, body, diagnostic = _dispatch_jsonrpc(method, params, request_id, budget)
    if not ok:
        log_warning(f"Inside Utils.py : {method} not dispatched - {diagnostic}")
        return None

    envelope = _parse_jsonrpc_envelope(body)
    if envelope is None:
        log_warning(
            f"Inside Utils.py : {method} answered with something that is not a single valid "
            "JSON-RPC 2.0 envelope"
        )
        return None

    # THE ID MUST MATCH.  JSON-RPC pairs a response to its request by id, and this suite issues
    # one request per exchange, so an envelope carrying a different id is not this call's
    # answer - it is a stale, cached or fabricated response, and treating it as this call's
    # result would attribute somebody else's outcome to this test.  Compared loosely on the
    # string form because a JSON id may legitimately arrive as 42 or "42".
    if "id" not in envelope or str(envelope.get("id")) != str(request_id):
        log_warning(
            f"Inside Utils.py : {method} answered with id {envelope.get('id')!r} but the "
            f"request carried id {request_id!r}; the response does not belong to this request"
        )
        return None

    return envelope


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


def _request_id_from_argv(argv):
    '''Return the JSON-RPC id carried by a curl argv's request body, or None.

    The suite's command definitions all pass their body with -d/--data, so the id the target is
    expected to echo is recoverable from the command itself.  None means "no id could be
    established", which is not treated as an id mismatch - it is simply one check that cannot be
    applied to that command.
    '''
    data_flags = ("-d", "--data", "--data-raw", "--data-ascii", "--data-binary")
    for index, token in enumerate(argv):
        if token in data_flags and index + 1 < len(argv):
            candidate = argv[index + 1]
        elif token.startswith("--data=") or token.startswith("--data-raw="):
            candidate = token.split("=", 1)[1]
        else:
            continue
        try:
            decoded = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict) and "id" in decoded:
            return decoded["id"]
    return None


def send_jsonrpc_envelope(curl_command, label):
    '''Dispatch a suite command definition and return its reply envelope, or None.

    The shared front half of every assertion a test case makes about a JSON-RPC call. It returns
    a value ONLY when all five of these hold, and logs the specific reason for the failure
    otherwise, naming `label` so a run log says which of a case's several calls went wrong:

      * the command was dispatched and a reply came back - the NO_RESPONSE_SENTINEL is refused
        here, and refused by prefix, because it is a truthy string and an emptiness test alone
        would read a dead endpoint as a healthy one;
      * the reply is syntactically valid JSON;
      * the reply is a JSON object rather than an array or a scalar;
      * it declares jsonrpc 2.0;
      * its id is the id the command sent, so the envelope describes THIS call.

    send_curl_command already enforces the transport half of that - curl's exit status, a 2xx
    HTTP status, exactly one envelope, and the matching id - so a caller only needing a reply is
    safe without this. What this adds is the same guarantee re-established where a VERDICT is
    formed: a test whose result is "the plugin acknowledged this write" or "the plugin refused
    this call" cannot rest that result on a helper's internal behaviour, because an envelope
    belonging to another request carries another request's answer.

    Args:
        curl_command: A command definition from HdmiCECSink_Curl.py, argv or string form.
        label: How this call should be named in a diagnostic, e.g. "baseline setVendorId".
    Returns:
        The parsed envelope as a dict, or None when any condition above failed.
    '''
    response = send_curl_command(curl_command)
    if not response:
        log_error(f"✖ {label}: command not sent")
        return None
    if response.startswith("< No response"):
        log_error(f"✖ {label}: no usable response from WPEFramework")
        return None

    try:
        envelope = json.loads(response)
    except ValueError:
        log_error(
            f"✖ {label}: reply is not valid JSON "
            f"({sanitise_for_log(response, max_chars=256)})"
        )
        return None

    if not isinstance(envelope, dict):
        log_error(
            f"✖ {label}: reply is valid JSON but not a JSON-RPC envelope "
            f"({type(envelope).__name__})"
        )
        return None
    if envelope.get("jsonrpc") != "2.0":
        log_error(
            f"✖ {label}: reply does not declare jsonrpc 2.0 "
            f"(jsonrpc={sanitise_for_log(envelope.get('jsonrpc'), max_chars=32)})"
        )
        return None

    sent_id = expected_request_id(curl_command)
    if sent_id is None:
        log_error(
            f"✖ {label}: the command definition carries no readable JSON-RPC id, so the reply "
            "cannot be correlated to it"
        )
        return None
    if str(envelope.get("id")) != str(sent_id):
        log_error(
            f"✖ {label}: reply answers request id "
            f"{sanitise_for_log(envelope.get('id'), max_chars=32)}, not the {sent_id} that was "
            "sent, so it describes a different call"
        )
        return None

    return envelope


def envelope_result(envelope):
    '''Return an envelope's "result" mapping, or None when there is not one.

    A JSON-RPC 2.0 reply carries result or error and never both, so None here means "this reply
    is not an answer" - either it is a refusal, or its result is an off-contract type. Returning
    None for a non-mapping result rather than raising is what lets a caller state the shape
    requirement as one condition instead of guarding every field read.
    '''
    if not isinstance(envelope, dict):
        return None
    result = envelope.get("result")
    return result if isinstance(result, dict) else None


def envelope_error(envelope):
    '''Return an envelope's "error" mapping, or None when there is not one.'''
    if not isinstance(envelope, dict):
        return None
    error = envelope.get("error")
    return error if isinstance(error, dict) else None


def require_ack(curl_command, label):
    '''True only when a write was dispatched AND the plugin acknowledged it.

    "Acknowledged" is the sink's published success shape: a result member carrying
    success == True, tested identically rather than truthily so that a 1, a "true" or a missing
    member is not read as agreement.

    This exists because the alternative - dispatching a write and not looking at the reply - is
    indistinguishable from not dispatching it at all. A case that writes, reads back, and finds
    the value it expected proves nothing if the write never left the host: the value it found is
    simply the value that was already there.

    Args:
        curl_command: The write command definition.
        label: How the write should be named in a diagnostic.
    Returns:
        True on an acknowledged write; False otherwise, with the reason already logged.
    '''
    envelope = send_jsonrpc_envelope(curl_command, label)
    if envelope is None:
        return False

    error = envelope_error(envelope)
    if error is not None:
        log_error(
            f"✖ {label}: refused by the plugin "
            f"(code={sanitise_for_log(error.get('code'), max_chars=32)}, "
            f"message={sanitise_for_log(error.get('message'), max_chars=192)})"
        )
        return False

    result = envelope_result(envelope)
    if result is None:
        log_error(
            f"✖ {label}: reply carries neither an error nor a result object, so the write was "
            "not acknowledged"
        )
        return False
    if result.get("success") is not True:
        log_error(
            f"✖ {label}: not acknowledged - success="
            f"{sanitise_for_log(result.get('success'), max_chars=32)}"
        )
        return False

    log_success(f"✔ {label}: acknowledged")
    return True


def expected_request_id(curl_command):
    '''Return the JSON-RPC id a suite command definition sends, or None when there is none.

    send_curl_command already refuses a reply whose id does not match the one sent, so a caller
    never has to check correlation to be safe. This exists for the caller that has to check it
    ANYWAY - a test whose verdict is "the dispatcher rejected this specific call" cannot rest
    that verdict on a helper's internal behaviour, because a reply correlated to some other
    request would carry some other request's error. Reading the id from the command definition,
    rather than repeating the literal in the test, is what keeps the two from drifting when a
    command's id changes.

    Args:
        curl_command: Either a curl argv sequence or the command STRING form, in the same two
                      shapes send_curl_command accepts.
    Returns:
        The id value as it appears in the request body, or None when the command carries no
        parseable JSON body with an id member.
    '''
    if isinstance(curl_command, str):
        try:
            argv = shlex.split(curl_command)
        except ValueError:
            return None
    else:
        argv = list(curl_command)
    return _request_id_from_argv(argv)


def _with_http_status_write_out(argv):
    '''Return argv with "-w <status marker>" inserted, unless it already carries a -w.

    Inserted immediately after the curl binary, so it lands before any "--" and before the URL
    and can never be mistaken for the URL's option terminator.
    '''
    if any(token == "-w" or token.startswith("--write-out") for token in argv):
        return list(argv)
    return [argv[0], "-w", _HTTP_CODE_WRITE_OUT] + list(argv[1:])


def send_curl_command(curl_command, timeout=None):
    '''Run a curl command and return its JSON-RPC response line as a string.

    Accepts either the complete curl command STRING that HdmiCECSink_Curl.py exports, or an
    already-built argv sequence. Either way the command is executed WITHOUT A SHELL: a
    string is split into an argv list with shlex and handed straight to a bounded subprocess, so
    no part of it - least of all the environment-derived endpoint URL appended to every
    constant in HdmiCECSink_Curl.py - can ever be interpreted as shell syntax. A URL
    carrying `;`, `&&` or `$(...)` therefore becomes inert argv text that curl rejects,
    rather than a second command that runs. Shell features (pipes, redirection, command
    substitution, globbing) are consequently not supported here, by design; nothing in this
    suite uses them.

    WHAT COUNTS AS A RESPONSE, and why the bar is where it is.  This helper used to return the
    first line of curl's output that happened to parse as JSON, having consulted neither curl's
    exit status nor the HTTP status.  Two very ordinary server behaviours defeated that:

      * a server that answers 500 (or 401, or a proxy's 502) WITH a JSON body - which is
        exactly when a server sends a body - was reported as a successful response;
      * a server that sends success-looking JSON and then stalls until curl gives up at exit 28
        was reported as a successful response, because the JSON had already been printed.

    All four conditions below must therefore hold, and each is checked because the others
    cannot see its failure:

      1. curl exited 0 - so the exchange completed rather than timing out or being refused;
      2. the HTTP status is 2xx - so the request was actually served;
      3. exactly ONE line of the body is a complete JSON-RPC 2.0 envelope - so a document with
         several JSON fragments, or none, is not silently reduced to whichever line came first;
      4. the envelope's id matches the id the command sent, when the command carried one - so a
         stale or fabricated response cannot be attributed to this request.

    The response is returned as a raw string, not a parsed object: callers run their own
    json.loads on it so that they can distinguish a malformed payload from a missing one.
    Any failure - a command that cannot be tokenised, a curl binary that is absent, a
    transport error, a non-success status, an unparsable body, an id mismatch, a response over
    the byte ceiling, or no body at all - yields the "< No response from WPEFramework >"
    sentinel, which callers detect with response.startswith("< No response").
    Args:
        curl_command: Complete curl command string, or a sequence of argv tokens
        timeout: Optional whole-second bound for this one invocation, so a caller polling
                 against its own deadline spends only the time it still has.  Omitted, or set
                 to anything that is not a positive integer, falls back to
                 CURL_TIMEOUT_SECONDS.
    Returns:
        The single JSON-RPC envelope line, otherwise the sentinel string.
    '''
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

        expected_id = _request_id_from_argv(argv)
        argv = _with_http_status_write_out(argv)

        # Bounded in time AND in bytes by _run_curl, which reads incrementally and kills the
        # child at the response ceiling rather than accumulating whatever the endpoint chooses
        # to send.  It also reaps the child and closes every pipe on every path.
        #
        # The budget is the caller's when it supplies one, and CURL_TIMEOUT_SECONDS otherwise.
        # A caller polling against its own deadline - Init_Devicelist_Populate's discovery and
        # seed loops are the ones that do - must be able to hand each request only the time it
        # still has, or a single request can outlive the deadline the loop is enforcing and the
        # bound becomes advisory.  _normalise_timeout is what makes an unusable value fall back
        # rather than raise, so a caller cannot shorten the budget to zero by accident.
        ok, returncode, stdout, stderr = _run_curl(
            argv, _normalise_timeout(timeout, CURL_TIMEOUT_SECONDS)
        )

        if not ok:
            detail = stderr.strip() or stdout.strip()
            print(
                "Inside Utils.py : send_curl_command got no usable response - curl exited "
                f"{returncode}" + (f": {detail}" if detail else "")
            )
            return NO_RESPONSE_SENTINEL

        body, status = _split_http_status(stdout)
        if status is None:
            print(
                "Inside Utils.py : send_curl_command got no HTTP status back, so no "
                "request/response exchange completed"
            )
            return NO_RESPONSE_SENTINEL
        if not 200 <= status < 300:
            snippet = body.strip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            print(
                f"Inside Utils.py : the endpoint answered HTTP {status}, which is not a success "
                f"status" + (f"; body: {snippet!r}" if snippet else "")
            )
            return NO_RESPONSE_SENTINEL

        # EXACTLY ONE envelope.  Collecting every match rather than breaking at the first one is
        # the point: a body carrying two envelopes is not a response this suite can attribute,
        # and quietly taking the first would hide that.
        envelope_lines = []
        for line in body.splitlines(keepends=True):
            if _parse_jsonrpc_envelope(line) is not None:
                envelope_lines.append(line)

        if not envelope_lines:
            snippet = body.strip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            print(
                "Inside Utils.py : the response carried no JSON-RPC 2.0 envelope"
                + (f"; body: {snippet!r}" if snippet else "")
            )
            return NO_RESPONSE_SENTINEL
        if len(envelope_lines) > 1:
            print(
                f"Inside Utils.py : the response carried {len(envelope_lines)} JSON-RPC "
                "envelopes; exactly one is expected, so none of them is attributable to this "
                "request"
            )
            return NO_RESPONSE_SENTINEL

        response_line = envelope_lines[0]
        envelope = _parse_jsonrpc_envelope(response_line)
        if expected_id is not None:
            if "id" not in envelope or str(envelope.get("id")) != str(expected_id):
                print(
                    f"Inside Utils.py : the response carried id {envelope.get('id')!r} but the "
                    f"request sent id {expected_id!r}; it does not answer this request"
                )
                return NO_RESPONSE_SENTINEL

        return response_line
    except ValueError as exc:
        # An untokenisable command, or an endpoint this module refuses.  The sentinel is
        # returned rather than an empty string, because a caller testing
        # response.startswith("< No response") must see a failure as exactly that.
        print(f"Inside Utils.py : Exception in send_curl_command function: {exc}")
        return NO_RESPONSE_SENTINEL
    except OSError as exc:
        print(f"Inside Utils.py : send_curl_command could not run curl: {exc}")
        return NO_RESPONSE_SENTINEL


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

    These are NAME checks, and a name check is only true at the moment it runs.  What makes the
    decision hold at the moment of the read is _read_payload, which opens the returned path once
    with O_NOFOLLOW and re-validates the resulting DESCRIPTOR with fstat: a file replaced by a
    symbolic link after this function returns is refused there rather than followed.  Neither
    half is sufficient alone - this one bounds WHERE a payload may come from, that one bounds
    WHAT is actually read.
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
    '''Read an approved YAML payload through ONE descriptor, validated after it is opened.

    THE RACE THIS CLOSES.  _approved_payload_path decides that a path is acceptable by
    inspecting the NAME - is it a symlink, does it resolve inside an approved root, is it a
    regular file.  Opening the same name afterwards is a SECOND resolution of it, and between
    the two anything that can write the containing directory can replace the file with a
    symbolic link to somewhere else.  The checks would all have passed on the file that was
    there; the bytes posted to the vComponent endpoint would come from the file that is there
    now - any file the test identity can read, /etc/shadow included on a suite running as root.

    So the file is opened ONCE with O_NOFOLLOW, and every remaining decision is made on that
    DESCRIPTOR rather than on the path:

      * O_NOFOLLOW makes the open itself fail with ELOOP if the final component is a symbolic
        link, so a link swapped in after the name checks is refused rather than followed.
      * O_CLOEXEC keeps the descriptor out of the curl this module is about to spawn; the
        payload is delivered on curl's stdin, so curl has no business holding the file too.
      * fstat on the open descriptor - not stat on the path - confirms it is a regular file and
        is within the cap.  A descriptor cannot be substituted once it is open, so what is
        measured here and what is read below are the same object by construction.
      * the read is bounded, and re-checked as it goes, because st_size is a snapshot that a
        concurrent writer can grow underneath the loop.

    Raises ValueError with the reason on any refusal, which send_vcomponent_command turns into
    a (0, diagnostic) result rather than a post.
    '''
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        # ELOOP here is the interesting one: it means a symbolic link now stands at a path this
        # module had already accepted as a regular file.
        raise ValueError(
            f"refused to post {path}: could not open it safely ({exc.strerror}); ELOOP means a "
            "symbolic link now stands at that path"
        ) from exc

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(
                f"refused to post {path}: the open descriptor is not a regular file"
            )
        if info.st_size > _VCOMPONENT_MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"refused to post {path}: {info.st_size} bytes, larger than the "
                f"{_VCOMPONENT_MAX_PAYLOAD_BYTES} byte cap"
            )

        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _VCOMPONENT_MAX_PAYLOAD_BYTES:
                raise ValueError(
                    f"refused to post {path}: it grew past the "
                    f"{_VCOMPONENT_MAX_PAYLOAD_BYTES} byte cap while being read"
                )
            chunks.append(chunk)
    finally:
        os.close(fd)

    return b"".join(chunks)


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


def await_plugin_ready(callsign, timeout=30.0, recheck_interval=0.5):
    '''Block until the controller reports the plugin activated, or the deadline expires.

    Activation is asynchronous with respect to Controller.1.activate: the call returns once the
    request is accepted, while Initialize() and the plugin's own worker threads come up afterwards.
    The state that matters is therefore an observable one - Controller.1.status@<callsign> reports
    it - so this waits for that state instead of guessing how long it takes.

    The status is read BEFORE any wait, so a plugin that is already up costs nothing, and expiry is
    returned rather than swallowed: recheck_interval is the interval between two readings of an
    observable state, and timeout is a failure deadline.

    Args:
        callsign: Plugin callsign, e.g. "org.rdk.HdmiCecSink"
        timeout: Failure deadline in seconds. Returning False means the plugin never reported
            itself activated within it, which is a condition the caller must handle.
        recheck_interval: Seconds between two readings of Controller.1.status.
    Returns:
        True once the controller reports the plugin activated; False on expiry, or when the
        controller could not be reached or answered without a usable state.
    '''
    deadline = time.time() + max(0.0, float(timeout))
    interval = max(0.05, float(recheck_interval))
    while True:
        response = send_jsonrpc_command(f"Controller.1.status@{callsign}")
        if response and "error" not in response:
            if _reports_activated(response.get("result")):
                return True
        if time.time() >= deadline:
            return False
        time.sleep(interval)


def _reports_activated(result):
    '''True when a Controller.1.status result says the plugin is activated.

    Thunder answers with a list of service descriptors, but a single object is accepted too so a
    framework revision that returns one is not misread as "not ready". Any other shape, and any
    state other than "activated", reads as not ready rather than as an error - the caller's
    deadline is what turns a persistent not-ready into a failure.
    '''
    if isinstance(result, dict):
        entries = [result]
    elif isinstance(result, list):
        entries = [entry for entry in result if isinstance(entry, dict)]
    else:
        return False
    for entry in entries:
        state = entry.get("state")
        if isinstance(state, str) and state.strip().lower() == "activated":
            return True
    return False
