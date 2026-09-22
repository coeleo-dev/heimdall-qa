from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
from pathlib import Path
import re
import shutil
from time import monotonic
from time import perf_counter
from time import sleep
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pydantic import ValidationError
from yaml import YAMLError

from heimdall_qa.bru_parser import parse_bru
from heimdall_qa.config import BudgetPair
from heimdall_qa.config import HarnessConfig
from heimdall_qa.coverage import expand
from heimdall_qa.diff import apply_diff
from heimdall_qa.diff import get_path
from heimdall_qa.diff import set_path
from heimdall_qa.errors import HarnessError
from heimdall_qa.fixtures import SCALAR_KINDS
from heimdall_qa.fixtures import build_value
from heimdall_qa.http_client import HttpExchange
from heimdall_qa.http_client import send
from heimdall_qa.logs.collector import LogCollection
from heimdall_qa.logs.collector import collect
from heimdall_qa.logs.collector import file_size
from heimdall_qa.packs import PackContext
from heimdall_qa.packs import PackResult
from heimdall_qa.packs import run_all
from heimdall_qa.run_store import create_run
from heimdall_qa.run_store import link_latest
from heimdall_qa.run_store import read_shared_captures
from heimdall_qa.run_store import write_captures
from heimdall_qa.run_store import write_evidence
from heimdall_qa.run_store import write_step
from heimdall_qa.run_store import write_summary
from heimdall_qa.run_store import write_verdict
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.load import load_suite
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import RoundFile
from heimdall_qa.schema.models import RuleSpec
from heimdall_qa.schema.models import SuiteFile
from heimdall_qa.validate import resolve_path


_TRANSPORT_CODES = frozenset({"HTTP_UNREACHABLE", "HTTP_TIMEOUT"})
_PATH_GHOST_KINDS = frozenset({"N-notfound", "S-bola"})
_PATH_GHOST_SKIP = frozenset({"base_url"})
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_UUID_VALUE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_AUTH_SECRET_KEYS = {
    "api_key": "api_key",
    "jwt": "jwt",
    "admin": "admin_secret",
    "hmac": "hmac_secret",
}
_LAB_AUTH = frozenset({"admin", "hmac"})
_FROZEN_CAPTURES = frozenset({"frozen_user_id", "frozen_external_user_id"})
_AUTH_HINTS = {
    "admin": (
        "add admin_secret to secrets.local.yaml and start NokrAPI profile admin "
        "on :9090 — the API key in runs/shared-captures.json is not the admin secret"
    ),
    "admin_secret": (
        "add admin_secret to secrets.local.yaml and start NokrAPI profile admin "
        "on :9090 — the API key in runs/shared-captures.json is not the admin secret"
    ),
    "api_key": (
        "run register-H01 or api-keys-post-H01 (api_key persists in "
        "runs/shared-captures.json), or set api_key in secrets.local.yaml"
    ),
    "jwt": (
        "run register-H01 (jwt persists in runs/shared-captures.json), "
        "or set jwt in secrets.local.yaml"
    ),
    "hmac": "add hmac_secret to secrets.local.yaml",
    "hmac_secret": "add hmac_secret to secrets.local.yaml",
}
_DEFAULT_SECRET_HINT = (
    "run register-H01 first (email/password/jwt/refresh_token/api_key persist in "
    "runs/shared-captures.json), or add them to secrets.local.yaml"
)
_LOAD_ERRORS = (ValidationError, ValueError, OSError, YAMLError)
_MISSING = object()


@dataclass(frozen=True)
class StepResult:
    step_dir: Path
    status_code: int
    packs: list[PackResult]
    trace_id: str
    error: str | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass(frozen=True)
class RoundStep:
    case_id: str
    status_code: int
    verdict: str
    pack_fails: tuple[str, ...]
    pack_warns: tuple[str, ...]
    elapsed_ms: float
    logs_incomplete: bool
    cause: str | None = None


def execute_step(
    *,
    case: CaseFile,
    contract: Contract,
    baseline: dict[str, Any],
    config: HarnessConfig,
    client: httpx.Client,
    run_dir: Path,
    step_index: int,
    run_id: str,
    secrets: dict[str, str] | None = None,
    dimensions: list[str] | None = None,
    environment: str = "sandbox",
    captures: dict[str, str] | None = None,
    pacer: dict[str, float] | None = None,
) -> StepResult:
    secrets = secrets or {}
    dimensions = dimensions or []
    captures = captures if captures is not None else {}
    pacer = pacer if pacer is not None else {}
    _hydrate_captures(run_dir, captures)
    skip_reason = _lab_skip_reason(case, contract, secrets, captures)
    if skip_reason:
        return _write_skipped_step(run_dir, step_index, case.id, skip_reason)
    mapping = _request_mapping(case, captures, secrets)
    path_mapping = _path_mapping(case.kind, contract.endpoint, mapping, secrets)
    body = apply_diff(baseline, case.diff)
    method, url, headers = _resolve_target(case, contract, config, path_mapping)
    headers.update(case.headers)
    headers = interpolate(headers, mapping)
    body, headers = _apply_generate(body, headers, case.generate, captures, secrets)
    mapping = _request_mapping(case, captures, secrets)
    path_mapping = _path_mapping(case.kind, contract.endpoint, mapping, secrets)
    url = interpolate(url, path_mapping)
    headers = interpolate(headers, mapping)
    body = interpolate(body, mapping)
    body = _fill_sentinels(body, mapping)
    body = _apply_session_unique(body, contract, case, captures)
    _reject_unresolved_placeholders(body, where="request body")
    _reject_unresolved_placeholders(url, where="request url")
    _store_captures(body, case.capture, captures)
    write_captures(run_dir, captures)
    omit = {name.lower() for name in case.omit_headers}
    if "authorization" not in omit:
        _ensure_auth(headers, contract, secrets, environment, captures)
    if "x-idempotency-key" not in omit:
        _ensure_idempotency(headers, contract, case, captures)
    _drop_omitted_headers(headers, omit)
    trace_id = f"nokrqa-{run_id}-{step_index}"
    headers["X-Trace-Id"] = trace_id
    json_body = body if method not in {"GET", "HEAD"} else None
    web_log = _resolve_log(config.log_files.web)
    worker_log = _resolve_log(config.log_files.worker)
    web_start = file_size(web_log)
    worker_start = file_size(worker_log)
    _pace_register(url, config, pacer)
    exchange, transport_error = _send_attempts(
        client, method, url, headers, json_body, case
    )
    _remember_session(case, contract, headers, json_body, exchange, captures)
    _store_response_captures(exchange.response_text, case.capture_response, captures)
    write_captures(run_dir, captures)
    snapshot = collect(
        trace_id=trace_id,
        web_log=web_log,
        worker_log=worker_log,
        wait_logs_ms=case.wait_logs_ms or 0,
        request_at=datetime.now(timezone.utc),
        web_start_offset=web_start,
        worker_start_offset=worker_start,
    )
    packs = run_all(
        _pack_context(
            case,
            contract,
            headers,
            json_body,
            exchange,
            trace_id,
            environment,
            dimensions,
            config,
            snapshot,
        )
    )
    step_dir = write_step(
        run_dir,
        step_index,
        case.id,
        request={"method": method, "url": url, "headers": headers, "body": json_body},
        response={
            "status": exchange.status_code,
            "headers": exchange.response_headers,
            "body": _response_body(exchange.response_text),
        },
        timing={"elapsed_ms": exchange.elapsed_ms},
        packs=packs,
        logs_web=snapshot.web_lines,
        logs_worker=snapshot.worker_lines,
        logs_incomplete=snapshot.logs_incomplete,
        log_fallback=snapshot.fallback,
    )
    return StepResult(
        step_dir,
        exchange.status_code,
        packs,
        trace_id,
        error=transport_error,
    )


def _request_mapping(
    case: CaseFile,
    captures: dict[str, str],
    secrets: dict[str, str],
) -> dict[str, str]:
    mapping = {**captures, **secrets}
    if case.path_values:
        mapping = {**mapping, **interpolate(dict(case.path_values), mapping)}
    return mapping


def _path_mapping(
    kind: str,
    endpoint: str,
    mapping: dict[str, str],
    secrets: dict[str, str],
) -> dict[str, str]:
    if kind not in _PATH_GHOST_KINDS:
        return mapping
    ghosts = _ghost_path_ids(endpoint, mapping, secrets)
    return {**mapping, **ghosts}


def _ghost_path_ids(
    endpoint: str,
    mapping: dict[str, str],
    secrets: dict[str, str],
) -> dict[str, str]:
    ghosts: dict[str, str] = {}
    for name in _PLACEHOLDER.findall(endpoint):
        if name in _PATH_GHOST_SKIP or name in secrets:
            continue
        captured = mapping.get(name)
        if not captured:
            continue
        ghosts[name] = _ghost_resource_id(captured)
    return ghosts


def _ghost_resource_id(captured: str) -> str:
    if _UUID_VALUE.fullmatch(captured):
        return str(uuid4())
    if captured.startswith("wh_"):
        return "wh_" + uuid4().hex[:12]
    if captured.startswith("evt-"):
        return "evt-ghost-" + uuid4().hex[:8]
    return "ghost_" + uuid4().hex[:12]


def _resolve_target(
    case: CaseFile,
    contract: Contract,
    config: HarnessConfig,
    mapping: dict[str, str],
) -> tuple[str, str, dict[str, str]]:
    if case.bru:
        return _from_bru(case.bru, config, mapping)
    method, path = _split_endpoint(contract.endpoint)
    base = config.nokr_admin if path.startswith("/admin/") else config.nokr_web
    url = interpolate(f"{base.rstrip('/')}{path}", mapping)
    return method, url, {}


def _from_bru(
    bru: str,
    config: HarnessConfig,
    secrets: dict[str, str],
) -> tuple[str, str, dict[str, str]]:
    path = Path(bru)
    if not path.is_absolute():
        path = Path(config.bruno_collection) / bru
    parsed = parse_bru(path)
    url = parsed.url.replace("{{base_url}}", config.nokr_web.rstrip("/"))
    headers = {
        key: _substitute_secrets(value, secrets)
        for key, value in parsed.headers.items()
    }
    return parsed.method, url, headers


def _substitute_secrets(value: str, secrets: dict[str, str]) -> str:
    for name, secret in secrets.items():
        value = value.replace(f"{{{{{name}}}}}", secret)
    return value


def interpolate(value: Any, mapping: dict[str, str]) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _substitute_secrets(value, mapping)
    if isinstance(value, dict):
        return {key: interpolate(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [interpolate(item, mapping) for item in value]
    return value


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    method, _, path = endpoint.strip().partition(" ")
    return method.upper(), path.strip()


def _apply_generate(
    body: dict[str, Any],
    headers: dict[str, str],
    generate: dict[str, Any] | None,
    captures: dict[str, str],
    secrets: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    for key, kind in (generate or {}).items():
        value = _resolve_generate(str(kind), captures, secrets)
        if key == "idempotency_key":
            headers["X-Idempotency-Key"] = value
            continue
        set_path(body, key, value)
        if "." not in key and str(kind) in {"uuid", "uuid_v4"}:
            captures.setdefault(key, value)
    return body, headers


def _resolve_generate(
    kind: str,
    captures: dict[str, str],
    secrets: dict[str, str],
) -> str:
    if kind.startswith("captured."):
        name = kind.removeprefix("captured.")
        if name not in captures:
            raise HarnessError(
                code="CAPTURE_MISSING",
                message=f"captured.{name} missing",
                hint="run the case that sets capture: first in this round or an earlier round (runs/shared-captures.json)",
            )
        return captures[name]
    if kind.startswith("secret."):
        name = kind.removeprefix("secret.")
        value = _usable_secret(secrets, name)
        if value is not None:
            return value
        alias = _SECRET_CAPTURE_KEYS.get(name, name)
        captured = captures.get(alias) or captures.get(name)
        if captured:
            return captured
        raise HarnessError(
            code="SECRET_MISSING",
            message=f"missing secret {name}",
            hint=_AUTH_HINTS.get(name, _DEFAULT_SECRET_HINT),
        )
    return _generated(kind)


def _store_captures(
    body: dict[str, Any],
    capture: dict[str, str] | None,
    captures: dict[str, str],
) -> None:
    for dest, field in (capture or {}).items():
        value = get_path(body, field)
        if value is None:
            raise HarnessError(
                code="CAPTURE_MISSING",
                message=f"capture field {field} missing",
                hint="generate or set that body field before capture:",
            )
        captures[dest] = str(value)


def _store_response_captures(
    response_text: str,
    capture_response: dict[str, str] | None,
    captures: dict[str, str],
) -> None:
    if not capture_response:
        return
    parsed = _response_body(response_text)
    body = parsed if isinstance(parsed, dict) else {}
    for dest, field in capture_response.items():
        value = get_path(body, field)
        if value is None or str(value) == "":
            continue
        captures[dest] = str(value)


def _hydrate_captures(run_dir: Path, captures: dict[str, str]) -> None:
    for key, value in read_shared_captures(run_dir.parent).items():
        captures.setdefault(key, value)


_GENERATE_OFFSETS = {
    "now_iso": timedelta(),
    "now_iso_plus_4m": timedelta(minutes=4),
    "now_iso_minus_47h": timedelta(hours=-47),
    "now_iso_plus_10m": timedelta(minutes=10),
    "now_iso_minus_49h": timedelta(hours=-49),
}


def _generated(kind: str) -> str:
    if kind in {"uuid", "uuid_v4"}:
        return str(uuid4())
    offset = _GENERATE_OFFSETS.get(kind)
    if offset is not None:
        return (datetime.now(timezone.utc) + offset).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind in SCALAR_KINDS:
        return build_value(kind)
    raise HarnessError(
        code="GENERATE_UNKNOWN",
        message=f"unknown generate kind: {kind}",
        hint="use uuid, now_iso*, email, password, person_name, company_name, address, cpf, cnpj, secret.<name>, or captured.<name>",
    )


_UNRESOLVED_PLACEHOLDER = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")
_SECRET_CAPTURE_KEYS = {
    "email": "register_email",
    "password": "register_password",
    "jwt": "jwt",
    "refresh_token": "refresh_token",
    "api_key": "api_key",
}
_SENTINEL_FIELD_KINDS = {
    "email": "email",
    "password": "password",
    "person_name": "person_name",
    "company_name": "company_name",
    "address": "address",
    "cpf": "cpf",
    "cnpj": "cnpj",
    "document_number": "cnpj",
}


def _usable_secret(secrets: dict[str, str], name: str) -> str | None:
    value = secrets.get(name)
    if value is None:
        return None
    text = str(value)
    if not text or text.startswith("replace-with-") or _UNRESOLVED_PLACEHOLDER.search(text):
        return None
    return text


def _placeholder_name(value: str) -> str | None:
    match = _UNRESOLVED_PLACEHOLDER.fullmatch(value.strip())
    if match is None:
        return None
    return value.strip()[2:-2]


def _is_placeholder(value: str) -> bool:
    return value.startswith("replace-with-") or _placeholder_name(value) is not None


def _fill_sentinels(value: Any, secrets: dict[str, str], key: str | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {name: _fill_sentinels(item, secrets, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_fill_sentinels(item, secrets, key) for item in value]
    if not isinstance(value, str) or not _is_placeholder(value):
        return value
    secret_name = _placeholder_name(value) or key
    if secret_name:
        secret = _usable_secret(secrets, secret_name)
        if secret is not None:
            return secret
        kind = _SENTINEL_FIELD_KINDS.get(secret_name)
        if kind is not None:
            return build_value(kind)
    return value


def _reject_unresolved_placeholders(value: Any, *, where: str = "request body") -> None:
    if value is None:
        return
    if isinstance(value, str):
        if _UNRESOLVED_PLACEHOLDER.search(value) or value.startswith("replace-with-"):
            raise HarnessError(
                code="PLACEHOLDER_UNRESOLVED",
                message=f"{where} still has placeholder {value}",
                hint="capture_response on an earlier H01, generate: uuid, or secret.<name> — do not leave {{…}} unresolved",
            )
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_unresolved_placeholders(item, where=where)
        return
    if isinstance(value, list):
        for item in value:
            _reject_unresolved_placeholders(item, where=where)


def _drop_omitted_headers(headers: dict[str, str], omit: set[str]) -> None:
    for key in list(headers):
        if key.lower() in omit:
            del headers[key]


def _credential(
    secrets: dict[str, str],
    captures: dict[str, str],
    name: str,
) -> str:
    usable = _usable_secret(secrets, name)
    if usable:
        return usable
    alias = _SECRET_CAPTURE_KEYS.get(name, name)
    return captures.get(alias) or captures.get(name) or ""


def _ensure_auth(
    headers: dict[str, str],
    contract: Contract,
    secrets: dict[str, str],
    environment: str,
    captures: dict[str, str] | None = None,
) -> None:
    captures = captures or {}
    if contract.auth == "api_key" and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {_credential(secrets, captures, 'api_key')}"
    elif contract.auth == "jwt":
        if "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {_credential(secrets, captures, 'jwt')}"
        headers.setdefault("X-Nokr-Environment", environment)
    elif contract.auth == "admin":
        headers.setdefault(
            "X-Nokr-Admin-Secret",
            _credential(secrets, captures, "admin_secret"),
        )


def _ensure_idempotency(
    headers: dict[str, str],
    contract: Contract,
    case: CaseFile,
    captures: dict[str, str],
) -> None:
    if contract.idempotency != "header_uuid_v4":
        return
    if any(key.lower() == "x-idempotency-key" for key in headers):
        return
    if _is_replay_kind(case.kind):
        previous = captures.get("last_idempotency_key")
        if not previous:
            raise HarnessError(
                code="CAPTURE_MISSING",
                message="captured.last_idempotency_key missing",
                hint="run H01 (or another 2xx mutation) in this round before I-replay",
            )
        headers["X-Idempotency-Key"] = previous
        return
    headers["X-Idempotency-Key"] = str(uuid4())


def _is_replay_kind(kind: str) -> bool:
    return kind == "I-replay" or kind.startswith("I-replay-")


def _should_uniquify(
    kind: str,
    contract: Contract,
    generate: dict[str, Any] | None = None,
) -> bool:
    if not contract.unique_json:
        return False
    if _is_replay_kind(kind):
        return False
    if kind.startswith("B-max-") and kind.removeprefix("B-max-") == contract.unique_json:
        return False
    if generate and contract.unique_json in generate:
        return False
    if kind == f"O-omit-{contract.unique_json}":
        return False
    if kind.startswith("H") or kind == "I-new-key":
        return True
    if kind.startswith("N-rule-"):
        rule = _rule_for_kind(kind, contract)
        assigned = getattr(rule, "set", None) or {}
        if contract.unique_json in assigned:
            return False
        return True
    return kind.startswith("O-") or kind.startswith("B-")


def _rule_for_kind(kind: str, contract: Contract) -> RuleSpec | None:
    rule_id = kind.removeprefix("N-rule-")
    for rule in contract.rules:
        if rule.id == rule_id:
            return rule
    return None


def _apply_session_unique(
    body: dict[str, Any],
    contract: Contract,
    case: CaseFile,
    captures: dict[str, str],
) -> dict[str, Any]:
    field = contract.unique_json
    if _is_replay_kind(case.kind):
        previous = captures.get("last_unique_json")
        if field and previous:
            set_path(body, field, previous)
        return body
    if not _should_uniquify(case.kind, contract, case.generate):
        return body
    assigned = case.diff.get("set") if isinstance(case.diff.get("set"), dict) else {}
    if field and field in assigned:
        return body
    uniquified = _uniquify_json(body, field, 0)
    return uniquified if isinstance(uniquified, dict) else body


def _remember_session(
    case: CaseFile,
    contract: Contract,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    exchange: HttpExchange,
    captures: dict[str, str],
) -> None:
    if exchange.status_code < 200 or exchange.status_code >= 300:
        return
    key = _request_header(headers, "X-Idempotency-Key")
    if key:
        captures["last_idempotency_key"] = key
    field = contract.unique_json
    if field and isinstance(body, dict):
        value = get_path(body, field)
        if value is not None and str(value):
            captures["last_unique_json"] = str(value)


def _request_header(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _pack_context(
    case: CaseFile,
    contract: Contract,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    exchange: HttpExchange,
    trace_id: str,
    environment: str,
    dimensions: list[str],
    config: HarnessConfig,
    snapshot: LogCollection,
) -> PackContext:
    path = urlparse(exchange.url).path
    budget = _budget_for(path, config)
    expect_status = case.expect.status
    if isinstance(expect_status, str) and expect_status.isdigit():
        expect_status = int(expect_status)
    waives = [item.pack for item in case.waive if item.pack]
    return PackContext(
        method=exchange.method,
        url_path=path,
        request_headers=headers,
        request_body=body,
        status_code=exchange.status_code,
        response_headers=exchange.response_headers,
        response_text=exchange.response_text,
        elapsed_ms=exchange.elapsed_ms,
        expect_status=expect_status,
        expect_code=case.expect.code,
        trace_sent=trace_id,
        environment=environment,
        idempotency_required=contract.idempotency == "header_uuid_v4",
        waives=waives,
        dimensions=dimensions,
        budget_ms=budget.budget,
        fail_ms=budget.fail,
        web_log_lines=snapshot.web_lines,
        worker_log_lines=snapshot.worker_lines,
        logs_incomplete=snapshot.logs_incomplete,
        require_worker_logs=_require_worker_logs(case, contract, path),
        case_kind=case.kind,
        business_rules=list(contract.rules),
        omit_headers=list(case.omit_headers),
    )


def _require_worker_logs(case: CaseFile, contract: Contract, path: str) -> bool:
    if (case.wait_logs_ms or 0) > 0:
        return True
    if contract.async_mode == "worker":
        return True
    lowered = path.lower()
    return "/api/ingest" in lowered or "/api/ledger" in lowered


def _resolve_log(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _budget_for(path: str, config: HarnessConfig) -> BudgetPair:
    lowered = path.lower()
    if "/api/ingest" in lowered or "/api/metering" in lowered:
        return config.budgets_ms.hot_path
    if lowered.startswith("/auth/") or "kyc" in lowered or "onboarding" in lowered:
        return config.budgets_ms.kyc
    return config.budgets_ms.default


def _response_body(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return text


def _send_or_fail(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: Any,
) -> tuple[HttpExchange, str | None]:
    started = perf_counter()
    try:
        return send(client, method, url, headers=headers, json_body=json_body), None
    except HarnessError as err:
        if err.code not in _TRANSPORT_CODES:
            raise
        elapsed_ms = (perf_counter() - started) * 1000.0
        exchange = HttpExchange(
            method=method.upper(),
            url=url,
            request_headers=dict(headers),
            request_body=json_body,
            status_code=0,
            response_headers={},
            response_text="",
            elapsed_ms=elapsed_ms,
        )
        return exchange, err.message


def _send_attempts(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: Any,
    case: CaseFile,
) -> tuple[HttpExchange, str | None]:
    if case.saturate is not None:
        return _send_saturate(client, method, url, headers, json_body, case.saturate)
    return _send_burst(client, method, url, headers, json_body, case.burst)


def _send_burst(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: Any,
    burst: int | None,
) -> tuple[HttpExchange, str | None]:
    times = burst or 1
    exchange, error = _send_or_fail(client, method, url, headers, json_body)
    for _ in range(times - 1):
        exchange, error = _send_or_fail(client, method, url, headers, json_body)
    return exchange, error


def _send_saturate(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: Any,
    saturate: Any,
) -> tuple[HttpExchange, str | None]:
    last: HttpExchange | None = None
    error: str | None = None
    for index in range(saturate.max):
        body = _uniquify_json(json_body, saturate.unique_json, index)
        attempt_headers = _rotate_idempotency(headers)
        last, error = _send_or_fail(client, method, url, attempt_headers, body)
        if error or last.status_code == saturate.until_status or last.status_code >= 500:
            return last, error
    assert last is not None
    return last, error


def _uniquify_json(json_body: Any, unique_json: str | None, index: int) -> Any:
    if not unique_json or not isinstance(json_body, dict):
        return json_body
    payload = dict(json_body)
    base = payload.get(unique_json)
    if not isinstance(base, str) or not base:
        base = "qa"
    payload[unique_json] = f"{base}_{index}_{uuid4().hex[:8]}"
    return payload


def _rotate_idempotency(headers: dict[str, str]) -> dict[str, str]:
    rotated = dict(headers)
    for key in list(rotated):
        if key.lower() == "x-idempotency-key":
            rotated[key] = str(uuid4())
    return rotated


def _pace_register(
    url: str,
    config: HarnessConfig,
    pacer: dict[str, float],
    *,
    sleeper: Callable[[float], None] = sleep,
    clock: Callable[[], float] = monotonic,
) -> None:
    if config.register_gap_ms <= 0:
        return
    if urlparse(url).path != "/auth/register":
        return
    now = clock()
    last = pacer.get("register")
    if last is not None:
        wait = config.register_gap_ms / 1000.0 - (now - last)
        if wait > 0:
            sleeper(wait)
            now = clock()
    pacer["register"] = now


def execute_round(
    round_path: Path,
    *,
    root: Path,
    config: HarnessConfig,
    client: httpx.Client,
    runs_dir: Path,
    secrets: dict[str, str] | None = None,
    mode: str = "headless",
) -> Path:
    if mode != "headless":
        raise HarnessError(
            code="MODE_REQUIRES_UI",
            message=f"mode {mode} requires the Fase 7 UI",
            hint="use --mode headless, or run: heimdall-qa serve ROUND",
        )
    started = perf_counter()
    secrets = secrets or {}
    round_file = _load_round_file(round_path)
    suite = optional_suite(round_file, root)
    if suite is not None:
        from heimdall_qa.suite_run import execute_suite_round

        return execute_suite_round(
            round_path,
            suite,
            round_file,
            root=root,
            config=config,
            client=client,
            runs_dir=runs_dir,
            secrets=secrets,
            mode=mode,
        )
    cases = _load_included_cases(round_file, root)
    _reject_todo_status(cases)
    _require_coverage(cases, root)
    selected = [case for case in cases if _matches_dimensions(case, round_file.dimensions)]
    prepared = [_prepare_case(case, root, secrets, runs_dir=runs_dir) for case in selected]
    run_dir = create_run(runs_dir, round_file.id)
    shutil.copy(round_path, run_dir / "round.yaml")
    captures: dict[str, str] = {}
    pacer: dict[str, float] = {}
    records = [
        _execute_prepared(
            item,
            config=config,
            client=client,
            run_dir=run_dir,
            run_id=round_file.id,
            secrets=secrets,
            dimensions=round_file.dimensions,
            environment=round_file.environment,
            step_index=index,
            captures=captures,
            pacer=pacer,
        )
        for index, item in enumerate(prepared, start=1)
    ]
    write_summary(
        run_dir,
        _build_summary(
            round_file,
            mode,
            cases,
            records,
            started,
            root,
            human_reject_rate=0.0,
        ),
    )
    write_evidence(run_dir, _build_evidence(records))
    link_latest(runs_dir, run_dir)
    return run_dir


def optional_suite(round_file: RoundFile, root: Path) -> SuiteFile | None:
    if not round_file.suite:
        return None
    path = resolve_path(root, round_file.suite)
    if not path.is_file():
        return None
    try:
        suite = load_suite(path)
    except _LOAD_ERRORS:
        return None
    return suite if suite.steps else None


def _load_round_file(round_path: Path) -> RoundFile:
    try:
        return load_round(round_path)
    except _LOAD_ERRORS as exc:
        raise HarnessError(
            code="ROUND_INVALID",
            message="round YAML is invalid",
            hint="fix the round file and run heimdall-qa validate",
            details=(str(exc),),
        ) from exc


def _load_included_cases(round_file: RoundFile, root: Path) -> list[CaseFile]:
    loaded: list[CaseFile] = []
    errors: list[str] = []
    for relative in round_file.include:
        try:
            loaded.append(load_case(resolve_path(root, relative)))
        except _LOAD_ERRORS as exc:
            errors.append(f"{relative}: {exc}")
    if errors:
        raise HarnessError(
            code="CASE_INVALID",
            message="one or more case files are invalid",
            hint="fix the case YAML and run heimdall-qa validate",
            details=tuple(errors),
        )
    return loaded


def _reject_todo_status(cases: list[CaseFile]) -> None:
    todos = [
        f"case {case.id}: expect.status is TODO"
        for case in cases
        if str(case.expect.status).upper() == "TODO"
    ]
    if todos:
        raise HarnessError(
            code="ROUND_INVALID",
            message="round contains TODO expect.status",
            hint="replace TODO with the expected HTTP status before running",
            details=tuple(todos),
        )


def _require_coverage(cases: list[CaseFile], root: Path) -> None:
    missing: list[str] = []
    by_contract: dict[Path, list[CaseFile]] = {}
    for case in cases:
        by_contract.setdefault(resolve_path(root, case.contract), []).append(case)
    for contract_path, grouped in by_contract.items():
        contract = _load_contract_file(contract_path)
        present = {case.id for case in grouped}
        for item in expand(contract):
            if item.case_id not in present:
                missing.append(f"coverage: missing {item.case_id}")
    if missing:
        raise HarnessError(
            code="ROUND_INVALID",
            message="round is missing required coverage kinds",
            hint="add the missing cases or use a contract whose expand matches the include list",
            details=tuple(missing),
        )


def _load_contract_file(contract_path: Path) -> Contract:
    try:
        return load_contract(contract_path)
    except _LOAD_ERRORS as exc:
        raise HarnessError(
            code="CONTRACT_INVALID",
            message=f"contract is invalid: {contract_path}",
            hint="fix the contract YAML and run heimdall-qa validate",
            details=(str(exc),),
        ) from exc


def _matches_dimensions(case: CaseFile, dimensions: list[str]) -> bool:
    if not case.tags:
        return True
    return bool(set(case.tags) & set(dimensions))


def _prepare_case(
    case: CaseFile,
    root: Path,
    secrets: dict[str, str],
    *,
    runs_dir: Path | None = None,
) -> tuple[CaseFile, Contract, dict[str, Any]]:
    contract = _load_contract_file(resolve_path(root, case.contract))
    shared = read_shared_captures(runs_dir) if runs_dir is not None else {}
    _require_auth_secret(contract, secrets, shared)
    baseline = _load_baseline(resolve_path(root, contract.baseline), contract.baseline)
    return case, contract, baseline


def _require_auth_secret(
    contract: Contract,
    secrets: dict[str, str],
    shared: dict[str, str] | None = None,
) -> None:
    if contract.auth == "none" or contract.auth in _LAB_AUTH:
        return
    key = _AUTH_SECRET_KEYS.get(contract.auth)
    if not key:
        return
    if _usable_secret(secrets, key):
        return
    shared = shared or {}
    alias = _SECRET_CAPTURE_KEYS.get(key, key)
    if shared.get(key) or shared.get(alias):
        return
    raise HarnessError(
        code="SECRET_MISSING",
        message=f"missing secret for auth {contract.auth}",
        hint=_AUTH_HINTS.get(contract.auth, _DEFAULT_SECRET_HINT),
    )


def _has_admin_secret(secrets: dict[str, str], captures: dict[str, str]) -> bool:
    return bool(
        _usable_secret(secrets, "admin_secret")
        or _usable_secret(captures, "admin_secret")
    )


def _lab_skip_reason(
    case: CaseFile,
    contract: Contract,
    secrets: dict[str, str],
    captures: dict[str, str],
) -> str | None:
    if _has_admin_secret(secrets, captures):
        return None
    reason = (
        "admin_secret missing; add it to secrets.local.yaml and start NokrAPI "
        "profile admin on :9090. The API key in shared-captures.json is not enough"
    )
    if contract.auth == "admin":
        return reason
    if case.kind == "H-setup-frozen":
        return reason
    needed = _collect_placeholders(case, contract) & _FROZEN_CAPTURES
    if needed and not any(captures.get(name) for name in needed):
        return reason
    return None


def _collect_placeholders(case: CaseFile, contract: Contract) -> set[str]:
    found = set(_PLACEHOLDER.findall(contract.endpoint))
    found |= _placeholders_from(case.headers)
    found |= _placeholders_from(case.diff)
    found |= _placeholders_from(case.path_values)
    found |= _placeholders_from(case.generate)
    return found


def _placeholders_from(value: Any) -> set[str]:
    found: set[str] = set()
    if value is None:
        return found
    if isinstance(value, str):
        found.update(_PLACEHOLDER.findall(value))
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            found |= _placeholders_from(str(key))
            found |= _placeholders_from(item)
        return found
    if isinstance(value, list):
        for item in value:
            found |= _placeholders_from(item)
    return found


def _write_skipped_step(
    run_dir: Path,
    step_index: int,
    case_id: str,
    reason: str,
) -> StepResult:
    step_dir = write_step(
        run_dir,
        step_index,
        case_id,
        request={},
        response={"status": None, "headers": {}, "body": None},
        timing={"elapsed_ms": 0},
        packs=[],
    )
    return StepResult(
        step_dir,
        0,
        [],
        "",
        skipped=True,
        skip_reason=reason,
    )


def _load_baseline(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise HarnessError(
            code="BASELINE_MISSING",
            message=f"baseline not found: {label}",
            hint="create the JSON file or fix the contract baseline path",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError(
            code="BASELINE_MISSING",
            message=f"baseline is unreadable: {label}",
            hint="create the JSON file or fix the contract baseline path",
            details=(str(exc),),
        ) from exc
    if not isinstance(payload, dict):
        raise HarnessError(
            code="BASELINE_MISSING",
            message=f"baseline must be a JSON object: {label}",
            hint="create the JSON file or fix the contract baseline path",
        )
    return payload


def _execute_prepared(
    prepared: tuple[CaseFile, Contract, dict[str, Any]],
    *,
    config: HarnessConfig,
    client: httpx.Client,
    run_dir: Path,
    run_id: str,
    secrets: dict[str, str],
    dimensions: list[str],
    environment: str,
    step_index: int,
    captures: dict[str, str],
    pacer: dict[str, float],
) -> RoundStep:
    case, contract, baseline = prepared
    result = execute_step(
        case=case,
        contract=contract,
        baseline=baseline,
        config=config,
        client=client,
        run_dir=run_dir,
        step_index=step_index,
        run_id=run_id,
        secrets=secrets,
        dimensions=dimensions,
        environment=environment,
        captures=captures,
        pacer=pacer,
    )
    verdict = _auto_verdict(case, result)
    write_verdict(result.step_dir, verdict)
    return _round_step(case.id, result, verdict)


def _auto_verdict(case: CaseFile, result: StepResult) -> dict[str, Any]:
    if result.skipped:
        return {
            "status": "skip",
            "actor": "auto",
            "comment": result.skip_reason,
            "continue": True,
            "cause": "lab",
        }
    pack_fails = [item.pack_id for item in result.packs if item.status == "fail"]
    status_ok = _status_matches(case.expect.status, result.status_code)
    path_fails = _jsonpath_failures(case, result)
    if status_ok and not pack_fails and not path_fails:
        return {"status": "pass", "actor": "auto", "comment": "", "continue": True}
    comment = _fail_comment(result, pack_fails, status_ok, path_fails)
    return {
        "status": "fail",
        "actor": "auto",
        "comment": comment,
        "continue": True,
        "cause": _verdict_cause(case, result, pack_fails, status_ok),
    }


def _verdict_cause(
    case: CaseFile,
    result: StepResult,
    pack_fails: list[str],
    status_ok: bool,
) -> str:
    if _is_instrument_fail(case, result):
        return "instrument"
    if _is_product_fail(result, pack_fails, status_ok):
        return "product"
    if pack_fails:
        return "pack"
    return "product"


def _is_instrument_fail(case: CaseFile, result: StepResult) -> bool:
    kind = case.kind or ""
    if kind.startswith("N-over-"):
        for item in result.packs:
            if (
                item.pack_id == "business.rule"
                and item.status == "fail"
                and "business error" in item.detail
            ):
                return True
    if kind.startswith("N-rule-") and 200 <= result.status_code < 300:
        if case.saturate is not None or (case.burst or 0) > 1:
            return True
    return False


def _is_product_fail(result: StepResult, pack_fails: list[str], status_ok: bool) -> bool:
    if result.status_code >= 500:
        return True
    details = " ".join(item.detail for item in result.packs if item.status == "fail")
    if "X-Trace-Id" in details or "traceId" in details or "Portuguese" in details:
        return True
    if not status_ok:
        return True
    return any(
        pack_id in {"http.baseline", "http.error", "business.rule"} for pack_id in pack_fails
    )


def _status_matches(expect: int | str, actual: int) -> bool:
    if isinstance(expect, str) and expect.isdigit():
        expect = int(expect)
    return expect == actual


def _jsonpath_failures(case: CaseFile, result: StepResult) -> list[str]:
    expected = case.expect.jsonpath or {}
    if not expected:
        return []
    body = _step_response_body(result.step_dir)
    failures: list[str] = []
    for path, want in expected.items():
        got = _lookup_dollar_path(body, path)
        if got is _MISSING or got != want:
            failures.append(f"jsonpath {path} mismatch")
    return failures


def _lookup_dollar_path(body: Any, path: str) -> Any:
    if not path.startswith("$."):
        return _MISSING
    current = body
    for part in path[2:].split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _step_response_body(step_dir: Path) -> Any:
    payload = json.loads((step_dir / "response.json").read_text(encoding="utf-8"))
    return payload.get("body")


def _fail_comment(
    result: StepResult,
    pack_fails: list[str],
    status_ok: bool,
    path_fails: list[str],
) -> str:
    if not status_ok:
        if result.error:
            return result.error
        if result.status_code == 0:
            return "HTTP unreachable"
        return f"HTTP {result.status_code}"
    if path_fails:
        return path_fails[0]
    if pack_fails:
        return f"pack {pack_fails[0]} failed"
    return "step failed"


def _round_step(case_id: str, result: StepResult, verdict: str | dict[str, Any]) -> RoundStep:
    packs_payload = json.loads((result.step_dir / "packs.json").read_text(encoding="utf-8"))
    timing = json.loads((result.step_dir / "timing.json").read_text(encoding="utf-8"))
    if isinstance(verdict, dict):
        status = str(verdict.get("status", "fail"))
        raw_cause = verdict.get("cause")
        cause = raw_cause if isinstance(raw_cause, str) else None
    else:
        status = verdict
        cause = None
    return RoundStep(
        case_id=case_id,
        status_code=result.status_code,
        verdict=status,
        pack_fails=tuple(item.pack_id for item in result.packs if item.status == "fail"),
        pack_warns=tuple(item.pack_id for item in result.packs if item.status == "warn"),
        elapsed_ms=float(timing.get("elapsed_ms", 0)),
        logs_incomplete=bool(packs_payload.get("logs_incomplete")),
        cause=cause,
    )


def _build_summary(
    round_file: RoundFile,
    mode: str,
    cases: list[CaseFile],
    records: list[RoundStep],
    started: float,
    root: Path,
    human_reject_rate: float = 0.0,
) -> dict[str, Any]:
    elapsed = [item.elapsed_ms for item in records]
    return {
        "round_id": round_file.id,
        "mode": mode,
        "counts": {
            "pass": sum(1 for item in records if item.verdict == "pass"),
            "fail": sum(1 for item in records if item.verdict == "fail"),
            "skip": sum(1 for item in records if item.verdict == "skip"),
            "http_5xx": sum(1 for item in records if item.status_code >= 500),
            "instrument": sum(1 for item in records if item.cause == "instrument"),
        },
        "packs": {
            "fail": sum(len(item.pack_fails) for item in records),
            "warn": sum(len(item.pack_warns) for item in records),
        },
        "coverage_pct": _coverage_pct(cases, root),
        "latency_ms": {
            "p50": _percentile(elapsed, 50),
            "p95": _percentile(elapsed, 95),
        },
        "logs_incomplete": sum(1 for item in records if item.logs_incomplete),
        "human_reject_rate": human_reject_rate,
        "review_duration_ms": (perf_counter() - started) * 1000.0,
    }


def _coverage_pct(cases: list[CaseFile], root: Path) -> float:
    included = {case.id for case in cases}
    expand_ids: set[str] = set()
    seen: set[Path] = set()
    for case in cases:
        contract_path = resolve_path(root, case.contract)
        if contract_path in seen:
            continue
        seen.add(contract_path)
        expand_ids.update(
            item.case_id for item in expand(_load_contract_file(contract_path))
        )
    if not expand_ids:
        return 0.0
    return 100.0 * len(included) / len(expand_ids)


def _percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (percent / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _build_evidence(records: list[RoundStep]) -> str:
    lines = [
        "| case_id | http | verdict | pack_fails |",
        "| --- | --- | --- | --- |",
    ]
    for item in records:
        fails = ", ".join(item.pack_fails) if item.pack_fails else ""
        lines.append(
            f"| {item.case_id} | {item.status_code} | {item.verdict} | {fails} |"
        )
    return "\n".join(lines) + "\n"
