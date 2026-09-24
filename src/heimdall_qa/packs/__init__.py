import json
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal

from heimdall_qa.logs.collector import LogCollection
from heimdall_qa.logs.collector import SourceRead
from heimdall_qa.logs.collector import not_declared
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.descriptor import AuthSchemeSpec

PackStatus = Literal["pass", "fail", "warn", "skipped", "waived"]

_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
#: Leak markers that hold for any JVM behind any API: a stack trace, a JDBC
#: driver name, a raw JWT. The product's own markers — its package root and its
#: credential prefixes — are declared in the descriptor and appended at match
#: time, by `_leak_pattern`.
_LEAK_BASE = r"org\.hibernate|jdbc|sqlstate|\\tat |eyJ"
_FQCN = re.compile(r"(?:^|\s)(?:[a-z]\w*\.)+[A-Z]\w*")
_PT = re.compile(r"não|erro ao|falha na")
_PT_ENVELOPE = re.compile(
    r"tamanho deve|não deve estar|não deve|deve ser entre",
    re.IGNORECASE,
)
_ERROR_LEVEL = re.compile(r"\bERROR\b")
_WARN_LEVEL = re.compile(r"\bWARN\b")
_ISSUED_CREDENTIAL_KEYS = frozenset(
    {
        "jwt",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "raw_key",
        "rawkey",
        "prefix",
    }
)
# Packs whose failure is the point of a check, so a waive is refused instead of
# honoured. Empty as of fase 1.1: the only non-waivable pack left with its step
# kind. The mechanism stays, because the next one is not a special case.
NON_WAIVABLE_PACKS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CatalogRule:
    id: str
    status: int = 0
    code: str | None = None
    error: str | None = None
    omit: list[str] = field(default_factory=list)
    set: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PackResult:
    pack_id: str
    status: PackStatus
    detail: str = ""


@dataclass(frozen=True)
class PackContext:
    method: str
    url_path: str
    request_headers: dict[str, str]
    request_body: Any
    status_code: int
    response_headers: dict[str, str]
    response_text: str
    elapsed_ms: float
    expect_status: int | str
    trace_sent: str
    environment: str
    budget_ms: int
    fail_ms: int
    #: The header the harness stamped the trace into. Declared by the project:
    #: a second API may call it `x-request-id`, and the pack must follow.
    trace_header: str = "X-Trace-Id"
    expect_code: str | None = None
    idempotency_required: bool = False
    waives: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    #: Every declared source, read once for this step's trace. The packs read the
    #: reads, not two lists named after the ids one product happens to use.
    logs: LogCollection = field(default_factory=not_declared)
    #: Whether a consumer is expected to settle this request afterwards. An async
    #: source that stayed silent is owed a line only when this is true; otherwise
    #: nobody asked it to log one, and its silence is not a finding.
    awaits_async: bool = False
    case_kind: str = ""
    business_rules: list[Any] = field(default_factory=list)
    omit_headers: list[str] = field(default_factory=list)
    #: The project in force. `auth.surface` and `security.leak` read their rules
    #: from here instead of from literals, which is the whole of fase 1.4.
    project: ProjectView = field(default_factory=ProjectView)


def run_all(ctx: PackContext) -> list[PackResult]:
    results = [
        _http_baseline(ctx),
        _security_leak(ctx),
        _performance(ctx),
        _auth_surface(ctx),
        _mutation(ctx),
        _observability(ctx),
        _business_rule(ctx),
    ]
    origin = _http_status_origin(ctx)
    if origin is not None:
        results.append(origin)
    if _expect_is_2xx(ctx) and 200 <= ctx.status_code < 300:
        results.append(_http_success(ctx))
    if _expect_is_4xx(ctx) and 400 <= ctx.status_code < 500:
        results.append(_http_error(ctx))
    return [_apply_waive(ctx, item) for item in results]


def _apply_waive(ctx: PackContext, result: PackResult) -> PackResult:
    if result.status != "fail" or result.pack_id not in ctx.waives:
        return result
    if result.pack_id in NON_WAIVABLE_PACKS:
        # Refused, not applied: silently honouring this waive would delete the one
        # check that justifies emenda 11 (A.19: not waivable without a P-GAP).
        return PackResult(
            result.pack_id,
            "fail",
            f"waive refused: {result.pack_id} is not waivable without a registered P-GAP"
            f" ({result.detail})",
        )
    return PackResult(result.pack_id, "waived", result.detail)


def _header(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _parsed_json(ctx: PackContext) -> Any | None:
    text = ctx.response_text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _Sentinel


class _Sentinel:
    pass


def _is_json_content(ctx: PackContext) -> bool:
    content_type = _header(ctx.response_headers, "Content-Type") or ""
    return "json" in content_type.lower()


def _as_status(value: int | str) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _expect_is_2xx(ctx: PackContext) -> bool:
    expect = _as_status(ctx.expect_status)
    return expect is not None and 200 <= expect < 300


def _expect_is_4xx(ctx: PackContext) -> bool:
    expect = _as_status(ctx.expect_status)
    return expect is not None and 400 <= expect < 500


def _http_baseline(ctx: PackContext) -> PackResult:
    pack_id = "http.baseline"
    failures: list[str] = []
    warnings: list[str] = []
    expect = _as_status(ctx.expect_status)
    if ctx.status_code >= 500:
        expect_5xx = expect is not None and expect >= 500
        if not expect_5xx:
            failures.append(f"hard fail: HTTP {ctx.status_code}")
        else:
            failures.append(f"HTTP {ctx.status_code} requires a chaos waive")
    elif expect is not None and ctx.status_code != expect:
        failures.append(f"HTTP {ctx.status_code}, expected {expect}")
    received = _header(ctx.response_headers, ctx.trace_header)
    if not received:
        failures.append(f"missing {ctx.trace_header} response header")
    elif received != ctx.trace_sent:
        failures.append(f"{ctx.trace_header} mismatch")
    if ctx.status_code != 204 and _is_json_content(ctx):
        parsed = _parsed_json(ctx)
        if parsed is _Sentinel:
            failures.append("response body is not parseable JSON")
    if ctx.elapsed_ms > ctx.fail_ms:
        failures.append(f"elapsed_ms {ctx.elapsed_ms} exceeds fail_ms {ctx.fail_ms}")
    elif ctx.elapsed_ms > ctx.budget_ms:
        warnings.append(f"elapsed_ms {ctx.elapsed_ms} exceeds budget_ms {ctx.budget_ms}")
        if "performance" in ctx.dimensions:
            failures.append("performance dimension promotes budget warn to fail")
    if failures:
        return PackResult(pack_id, "fail", "; ".join(failures))
    if warnings:
        return PackResult(pack_id, "warn", "; ".join(warnings))
    return PackResult(pack_id, "pass")


def _security_leak(ctx: PackContext) -> PackResult:
    pattern = _leak_pattern(ctx)
    for text in _leak_haystacks(ctx):
        if text and pattern.search(text):
            return PackResult("security.leak", "fail", "response leaked infrastructure or secret")
    return PackResult("security.leak", "pass")


def _leak_pattern(ctx: PackContext) -> re.Pattern[str]:
    """The base markers plus whatever the project says is its own.

    A product's package root and its credential prefixes used to be literals
    here; they are now `errors.product_packages` and `errors.redact`, so a second
    product does not have to patch this file to be checked the same way.
    """
    declared = [f"at {package}" for package in ctx.project.product_packages()]
    declared.extend(ctx.project.redact_patterns())
    if not declared:
        return re.compile(_LEAK_BASE, re.IGNORECASE)
    return re.compile(_LEAK_BASE + "|" + "|".join(declared), re.IGNORECASE)


def _leak_haystacks(ctx: PackContext) -> list[str]:
    haystacks = [value for value in ctx.response_headers.values() if value]
    parsed = _parsed_json(ctx)
    if 200 <= ctx.status_code < 300 and parsed is not _Sentinel:
        haystacks.append(json.dumps(_blank_issued_credentials(parsed)))
        return haystacks
    haystacks.append(ctx.response_text)
    return haystacks


def _blank_issued_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "" if key.lower() in _ISSUED_CREDENTIAL_KEYS else _blank_issued_credentials(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_blank_issued_credentials(item) for item in value]
    return value


def _http_error(ctx: PackContext) -> PackResult:
    pack_id = "http.error"
    parsed = _parsed_json(ctx)
    if parsed is _Sentinel or not isinstance(parsed, dict):
        return PackResult(pack_id, "fail", "4xx body is not a JSON object")
    error = parsed.get("error")
    status_name = parsed.get("status")
    if not isinstance(error, str) or not error.strip():
        if isinstance(status_name, str) and status_name.strip():
            if ctx.expect_code and status_name != ctx.expect_code:
                return PackResult(pack_id, "fail", "error code mismatch")
            return PackResult(pack_id, "pass")
        return PackResult(pack_id, "fail", "error must be a non-empty string")
    if error.startswith(tuple(ctx.project.product_packages())) or _FQCN.search(error):
        return PackResult(pack_id, "fail", "error looks like a Java FQCN")
    if _PT_ENVELOPE.search(error):
        return PackResult(pack_id, "fail", "error message looks Portuguese")
    trace_header = _header(ctx.response_headers, ctx.trace_header)
    trace_id = parsed.get("traceId")
    if not isinstance(trace_id, str) or not trace_id:
        return PackResult(pack_id, "fail", "traceId missing")
    if trace_header and trace_id != trace_header:
        return PackResult(
            pack_id, "fail", f"traceId does not match {ctx.trace_header}"
        )
    if ctx.expect_code:
        code = parsed.get("code")
        if code != ctx.expect_code:
            return PackResult(pack_id, "fail", "error code mismatch")
    return PackResult(pack_id, "pass")


def _http_success(ctx: PackContext) -> PackResult:
    parsed = _parsed_json(ctx)
    if isinstance(parsed, dict) and "error" in parsed and "traceId" in parsed:
        return PackResult(
            "http.success",
            "fail",
            "2xx body must not be an {error, traceId} envelope",
        )
    if not ctx.logs.measured:
        return PackResult(
            "http.success", "skipped", "no log source declared, nothing to read"
        )
    missing = _missing_trace(_owed_reads(ctx))
    if missing:
        return PackResult("http.success", "fail", "; ".join(missing))
    lines = [entry.text for read in ctx.logs.reads for entry in read.entries]
    if any(_ERROR_LEVEL.search(line) for line in lines):
        return PackResult("http.success", "fail", "logs contain ERROR for this trace")
    if any(_WARN_LEVEL.search(line) for line in lines):
        return PackResult("http.success", "warn", "logs contain WARN on the happy path")
    return PackResult("http.success", "pass")


def _owes(ctx: PackContext, read: SourceRead) -> bool:
    """Whether the project says this trace reaches this source on this step.

    Two declarations can say it does not: `propagate: false` (the service was never
    asked to log it), and `role: async` on a step that settles nothing afterwards
    (nobody asked the consumer to do anything). Neither silence is a finding.
    """
    if not read.propagate:
        return False
    return read.role == "sync" or ctx.awaits_async


def _owed_reads(ctx: PackContext) -> list[SourceRead]:
    """The reads this step's evidence depends on — the declaration said so."""
    return [read for read in ctx.logs.reads if _owes(ctx, read)]


def _missing_trace(reads: list[SourceRead]) -> list[str]:
    """The owed sources that had no line and were readable enough to know it."""
    return [
        f"missing trace_id line in {read.id} logs ({read.reason})"
        for read in reads
        if not read.found and not read.truncated
    ]


def _truncated_reads(reads: list[SourceRead]) -> list[str]:
    """The owed sources whose silence is the harness's own `max_tail_bytes`."""
    return [
        f"{read.id} exceeded max_tail_bytes, so its silence cannot be read as absence"
        for read in reads
        if not read.found and read.truncated
    ]


def _observability(ctx: PackContext) -> PackResult:
    """Whether the trace reached every service the project says it reaches.

    The distinction this pack exists to make (F5 §5) is between a **product**
    failure and a **declared** skip — and it is made from the declaration, never
    from the harness's own patience:

    - `propagate: true` and no line ⇒ fail: the service was supposed to log it;
    - `propagate: false` ⇒ skipped, explained: nobody asked it to;
    - an async source on a step that expects no async effect ⇒ not owed either;
    - the read was cut by `max_tail_bytes` ⇒ warn: the harness cannot say, and
      neither claiming a product defect nor a pass is honest.

    Before this, every one of those was the same `skipped`, which is how 208 steps
    reported "worker logs incomplete" about the very endpoints where correlation
    between services is the point.
    """
    if not ctx.logs.measured:
        return PackResult(
            "observability", "skipped", "no log source declared, nothing to read"
        )
    owed = _owed_reads(ctx)
    missing = _missing_trace(owed)
    if missing:
        return PackResult("observability", "fail", "; ".join(missing))
    truncated = _truncated_reads(owed)
    if truncated:
        return PackResult("observability", "warn", "; ".join(truncated))
    if not any(read.found for read in ctx.logs.reads):
        return PackResult("observability", "skipped", "; ".join(_not_owed(ctx)) or "no line")
    haystack = "\n".join(entry.text for read in ctx.logs.reads for entry in read.entries)
    if _PT.search(haystack):
        return PackResult("observability", "fail", "log message looks Portuguese")
    return PackResult("observability", "pass")


def _not_owed(ctx: PackContext) -> list[str]:
    """Why a silent source is not a finding, one reason per declaration."""
    reasons: list[str] = []
    for read in ctx.logs.reads:
        if read.found or _owes(ctx, read):
            continue
        if not read.propagate:
            reasons.append(f"{read.id} declares propagate: false (not instrumented)")
        else:
            reasons.append(f"{read.id} is async and this step expects no async effect")
    return reasons


def _auth_surface(ctx: PackContext) -> PackResult:
    """Checks that the request carried the credential its route requires.

    The rules are the descriptor's: the scheme named by `routes[].auth`, its
    header, the prefix the environment's credential must carry, and whether the
    route resolves its environment from a header. Before 1.4 this was four
    `startswith` comparisons on `/api/`, `/platform/`, `/admin/` and `/webhooks/`
    plus two header literals and two credential prefixes.
    """
    pack_id = "auth.surface"
    if _skips_auth_surface(ctx):
        return PackResult(pack_id, "pass")
    scheme = ctx.project.auth_for(ctx.url_path)
    if scheme is None:
        return PackResult(pack_id, "pass")
    failures = _credential_failures(ctx, scheme)
    failures.extend(_environment_header_failures(ctx))
    failures.extend(_foreign_credential_failures(ctx))
    if failures:
        return PackResult(pack_id, "fail", "; ".join(failures))
    return PackResult(pack_id, "pass")


def _credential_failures(ctx: PackContext, scheme: AuthSchemeSpec) -> list[str]:
    value = _header(ctx.request_headers, scheme.header) or ""
    if not value:
        return [f"{ctx.url_path} requires the {scheme.header} header"]
    failures: list[str] = []
    expected = scheme.prefixes.get(ctx.environment)
    if expected is not None and expected not in value:
        failures.append(
            f"{ctx.url_path} requires {scheme.header} with the {expected} prefix"
        )
    if scheme.scheme == "bearer" and not value.startswith("Bearer "):
        failures.append(f"{ctx.url_path} requires a Bearer {scheme.header}")
    return failures


def _environment_header_failures(ctx: PackContext) -> list[str]:
    """A route that resolves its environment from a header must receive one."""
    name = ctx.project.environment_header_name()
    if name is None or not ctx.project.requires_environment_header(ctx.url_path):
        return []
    value = _header(ctx.request_headers, name) or ""
    if value in ctx.project.environment_values():
        return []
    return [f"{ctx.url_path} requires the {name} header"]


def _foreign_credential_failures(ctx: PackContext) -> list[str]:
    """No route may carry another route's credential.

    This is the declarative form of "an inbound callback must not use a tenant
    Bearer": the check is not `/webhooks/`, it is *every prefix declared by a
    scheme other than this one, in that scheme's own header*.
    """
    scheme = ctx.project.auth_for(ctx.url_path)
    if scheme is None:
        return []
    for other_name in ctx.project.other_auth_names(scheme):
        other = ctx.project.auth_named(other_name)
        if other is None or not other.prefixes:
            continue
        value = _header(ctx.request_headers, other.header) or ""
        if any(prefix in value for prefix in other.prefixes.values()):
            return [
                f"{ctx.url_path} must not carry the {other_name} credential"
            ]
    return []


def _skips_auth_surface(ctx: PackContext) -> bool:
    if ctx.case_kind == "N-auth":
        return True
    omitted = {name.lower() for name in ctx.omit_headers}
    return "authorization" in omitted


def _mutation(ctx: PackContext) -> PackResult:
    pack_id = "mutation"
    if ctx.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return PackResult(pack_id, "pass")
    if ctx.case_kind in {"I-missing", "I-format"}:
        return PackResult(pack_id, "pass")
    if not ctx.idempotency_required:
        return PackResult(pack_id, "pass")
    key = _header(ctx.request_headers, "X-Idempotency-Key") or ""
    if not _UUID_V4.match(key):
        return PackResult(pack_id, "fail", "X-Idempotency-Key must be UUID v4")
    return PackResult(pack_id, "pass")


def _performance(ctx: PackContext) -> PackResult:
    pack_id = "performance"
    if ctx.elapsed_ms > ctx.fail_ms:
        return PackResult(
            pack_id,
            "fail",
            f"elapsed_ms {ctx.elapsed_ms} exceeds fail_ms {ctx.fail_ms}",
        )
    if ctx.elapsed_ms > ctx.budget_ms:
        status: PackStatus = "fail" if "performance" in ctx.dimensions else "warn"
        return PackResult(
            pack_id,
            status,
            f"elapsed_ms {ctx.elapsed_ms} exceeds budget_ms {ctx.budget_ms}",
        )
    return PackResult(pack_id, "pass")


def _business_rule(ctx: PackContext) -> PackResult:
    pack_id = "business.rule"
    classified = _classify_business_rule(ctx)
    expected = _expected_rule_id(ctx.case_kind)
    kind = ctx.case_kind or "H01"
    if expected:
        if classified is None:
            if 400 <= ctx.status_code < 500:
                return PackResult(pack_id, "fail", f"unclassified 4xx, expected {expected}")
            return PackResult(pack_id, "pass")
        if classified != expected:
            return PackResult(pack_id, "fail", f"got {classified}, expected {expected}")
        return PackResult(pack_id, "pass")
    if _expect_is_2xx(ctx) and classified:
        return PackResult(
            pack_id,
            "fail",
            f"unexpected business error {classified} on {kind}",
        )
    if _is_bean_kind(ctx.case_kind) and classified:
        if _bean_rule_overlaps_kind(ctx.case_kind, classified, ctx.business_rules):
            return PackResult(pack_id, "pass")
        return PackResult(
            pack_id,
            "fail",
            f"business error {classified} on {ctx.case_kind}",
        )
    return PackResult(pack_id, "pass")


def _http_status_origin(ctx: PackContext) -> PackResult | None:
    if not _is_bean_kind(ctx.case_kind):
        return None
    if _classify_business_rule(ctx):
        return None
    expect = _as_status(ctx.expect_status)
    if expect is None:
        return None
    actual = ctx.status_code
    swapped = (expect == 400 and actual == 422) or (expect == 422 and actual == 400)
    if not swapped:
        return None
    return PackResult("http.status_origin", "warn", f"HTTP {actual}, expected {expect}")


def _expected_rule_id(kind: str) -> str | None:
    if kind.startswith("N-rule-"):
        return kind.removeprefix("N-rule-")
    return None


def _bean_kind_field(kind: str) -> str | None:
    for prefix in ("N-omit-", "N-pattern-", "N-over-"):
        if kind.startswith(prefix):
            field = kind.removeprefix(prefix)
            if field.endswith("-keys"):
                return field[: -len("-keys")]
            if field.endswith("-future") or field.endswith("-past"):
                return field.rsplit("-", 1)[0]
            return field
    return None


def _rule_touches_field(rule: Any, field: str) -> bool:
    omitted = [str(item) for item in (getattr(rule, "omit", None) or [])]
    if field in omitted:
        return True
    assigned = getattr(rule, "set", None) or {}
    if not isinstance(assigned, dict):
        return False
    if field in assigned:
        return True
    return any(str(key).endswith(f".{field}") or str(key) == field for key in assigned)


def _bean_rule_overlaps_kind(kind: str, classified: str, rules: list[Any]) -> bool:
    field = _bean_kind_field(kind)
    if field is None:
        return False
    for rule in rules:
        if str(getattr(rule, "id", "") or "") != classified:
            continue
        return _rule_touches_field(rule, field)
    return False


def _is_bean_kind(kind: str) -> bool:
    if kind in {"I-missing", "I-format"}:
        return True
    return (
        kind.startswith("N-omit-")
        or kind.startswith("N-pattern-")
        or kind.startswith("N-over-")
    )


def _classify_business_rule(ctx: PackContext) -> str | None:
    if not (400 <= ctx.status_code < 500):
        return None
    parsed = _parsed_json(ctx)
    if not isinstance(parsed, dict):
        return None
    code = parsed.get("code")
    error = parsed.get("error")
    code_text = code if isinstance(code, str) else ""
    error_text = error if isinstance(error, str) else ""
    status_text = parsed.get("status") if isinstance(parsed.get("status"), str) else ""
    for rule in ctx.business_rules:
        rule_id = str(getattr(rule, "id", "") or "")
        rule_code = getattr(rule, "code", None)
        if code_text and (code_text == rule_code or code_text == rule_id):
            return rule_id
        if status_text and (status_text == rule_code or status_text == rule_id):
            return rule_id
        needle = getattr(rule, "error", None)
        if isinstance(needle, str) and needle and needle in error_text:
            return rule_id
    return None
