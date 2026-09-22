from dataclasses import dataclass
from dataclasses import field
import json
import re
from typing import Any
from typing import Literal

PackStatus = Literal["pass", "fail", "warn", "skipped", "waived"]

_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LEAK = re.compile(
    r"org\.hibernate|jdbc|sqlstate|\\tat |at com\.nokr|nk_test_|nk_live_|eyJ",
    re.IGNORECASE,
)
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
    expect_code: str | None = None
    idempotency_required: bool = False
    waives: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    web_log_lines: list[str] = field(default_factory=list)
    worker_log_lines: list[str] = field(default_factory=list)
    logs_incomplete: bool = False
    require_worker_logs: bool = False
    case_kind: str = ""
    business_rules: list[Any] = field(default_factory=list)
    omit_headers: list[str] = field(default_factory=list)


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
    received = _header(ctx.response_headers, "X-Trace-Id")
    if not received:
        failures.append("missing X-Trace-Id response header")
    elif received != ctx.trace_sent:
        failures.append("X-Trace-Id mismatch")
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
    for text in _leak_haystacks(ctx):
        if text and _LEAK.search(text):
            return PackResult("security.leak", "fail", "response leaked infrastructure or secret")
    return PackResult("security.leak", "pass")


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
    if error.startswith("com.nokr") or _FQCN.search(error):
        return PackResult(pack_id, "fail", "error looks like a Java FQCN")
    if _PT_ENVELOPE.search(error):
        return PackResult(pack_id, "fail", "error message looks Portuguese")
    trace_header = _header(ctx.response_headers, "X-Trace-Id")
    trace_id = parsed.get("traceId")
    if not isinstance(trace_id, str) or not trace_id:
        return PackResult(pack_id, "fail", "traceId missing")
    if trace_header and trace_id != trace_header:
        return PackResult(pack_id, "fail", "traceId does not match X-Trace-Id")
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
    if not ctx.web_log_lines:
        return PackResult("http.success", "fail", "logs_incomplete: no web line for trace")
    if any(_ERROR_LEVEL.search(line) for line in ctx.web_log_lines):
        return PackResult("http.success", "fail", "web logs contain ERROR for this trace")
    if any(_WARN_LEVEL.search(line) for line in ctx.web_log_lines):
        return PackResult("http.success", "warn", "web logs contain WARN on the happy path")
    return PackResult("http.success", "pass")


def _observability(ctx: PackContext) -> PackResult:
    if not ctx.web_log_lines:
        return PackResult("observability", "fail", "missing trace_id line in web logs")
    if ctx.require_worker_logs and not ctx.worker_log_lines:
        return PackResult(
            "observability",
            "skipped",
            "worker logs incomplete (no match)",
        )
    haystack = "\n".join(ctx.web_log_lines + ctx.worker_log_lines)
    if _PT.search(haystack):
        return PackResult("observability", "fail", "log message looks Portuguese")
    return PackResult("observability", "pass")


def _auth_surface(ctx: PackContext) -> PackResult:
    pack_id = "auth.surface"
    if _skips_auth_surface(ctx):
        return PackResult(pack_id, "pass")
    path = ctx.url_path
    authorization = _header(ctx.request_headers, "Authorization") or ""
    if path.startswith("/api/"):
        expected = "nk_live_" if ctx.environment == "production" else "nk_test_"
        if not authorization.startswith("Bearer ") or expected not in authorization:
            return PackResult(pack_id, "fail", f"/api requires Bearer {expected}")
        return PackResult(pack_id, "pass")
    if path.startswith("/platform/"):
        env = _header(ctx.request_headers, "X-Nokr-Environment")
        if not authorization.startswith("Bearer ") or env not in {"sandbox", "production"}:
            return PackResult(pack_id, "fail", "/platform requires JWT and X-Nokr-Environment")
        return PackResult(pack_id, "pass")
    if path.startswith("/admin/"):
        if not _header(ctx.request_headers, "X-Nokr-Admin-Secret"):
            return PackResult(pack_id, "fail", "/admin requires X-Nokr-Admin-Secret")
        return PackResult(pack_id, "pass")
    if path.startswith("/webhooks/"):
        if "nk_test_" in authorization or "nk_live_" in authorization:
            return PackResult(pack_id, "fail", "/webhooks must not use tenant Bearer")
        return PackResult(pack_id, "pass")
    return PackResult(pack_id, "pass")


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
