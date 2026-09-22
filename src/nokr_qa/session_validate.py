from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nokr_qa.schema.load import load_case
from nokr_qa.schema.load import load_contract
from nokr_qa.schema.load import load_round
from nokr_qa.schema.models import CampaignFile
from nokr_qa.schema.models import CaseFile
from nokr_qa.schema.models import Contract
from nokr_qa.schema.models import RoundFile


def resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return root / path

_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_SKIP = frozenset({"base_url"})
_UNIQUE_FIELDS = frozenset({"name", "feature_key", "featureKey"})
_UUID_KINDS = frozenset({"uuid", "uuid_v4"})
_CATALOG_UNIQUE_PATHS = (
    "/platform/billable-metrics",
    "/platform/feature-keys",
    "/platform/plans",
    "/platform/webhooks",
    "/platform/api-keys",
    "/platform/rate-cards",
)


def validate_round_session(
    round_file: RoundFile,
    cases: list[CaseFile],
    root: Path,
) -> list[str]:
    errors: list[str] = []
    contracts = _contracts_for_cases(cases, root, errors)
    errors.extend(_e_isolate_errors(round_file, cases))
    errors.extend(_unique_json_errors(contracts))
    return errors


def validate_campaign_chain(campaign: CampaignFile, root: Path) -> list[str]:
    errors: list[str] = []
    produced: set[str] = set()
    for entry in campaign.rounds:
        round_path = resolve_path(root, entry.round)
        if not round_path.is_file():
            continue
        try:
            round_file = load_round(round_path)
        except (ValidationError, ValueError, OSError):
            continue
        cases, case_errors = _load_cases(round_file, root)
        errors.extend(case_errors)
        contracts = _contracts_for_cases(cases, root, errors)
        consumers: set[str] = set()
        for contract in contracts.values():
            consumers |= _placeholders_in(contract.endpoint)
            consumers |= _baseline_placeholders(root, contract)
        for case in cases:
            consumers |= _case_consumers(case)
            if case.kind == "E-isolate" and round_file.environment == "sandbox":
                if str(case.expect.status) == "200":
                    errors.append(
                        f"{entry.round}: case {case.id} E-isolate sandbox must expect 403, not 200"
                    )
        consumers -= _SKIP
        same_round = _producers(cases, contracts)
        missing = consumers - produced - same_round
        for name in sorted(missing):
            errors.append(
                f"{entry.round}: placeholder {{{{{name}}}}} has no producer before this round "
                "(H01 capture_response, contract.captures, capture:, or generate: uuid)"
            )
        errors.extend(_resource_id_errors(entry.round, contracts, missing))
        produced |= same_round
        errors.extend(_unique_json_errors(contracts))
    return errors


def _load_cases(round_file: RoundFile, root: Path) -> tuple[list[CaseFile], list[str]]:
    loaded: list[CaseFile] = []
    errors: list[str] = []
    for relative in round_file.include:
        path = resolve_path(root, relative)
        try:
            loaded.append(load_case(path))
        except (ValidationError, ValueError, OSError) as exc:
            errors.append(f"case {relative}: {exc}")
    return loaded, errors


def _contracts_for_cases(
    cases: list[CaseFile],
    root: Path,
    errors: list[str],
) -> dict[Path, Contract]:
    found: dict[Path, Contract] = {}
    for case in cases:
        path = resolve_path(root, case.contract)
        if path in found:
            continue
        try:
            found[path] = load_contract(path)
        except (ValidationError, ValueError, OSError) as exc:
            errors.append(f"contract {case.contract}: {exc}")
    return found


def _e_isolate_errors(round_file: RoundFile, cases: list[CaseFile]) -> list[str]:
    if round_file.environment != "sandbox":
        return []
    errors: list[str] = []
    for case in cases:
        if case.kind != "E-isolate":
            continue
        if str(case.expect.status) == "200":
            errors.append(
                f"case {case.id}: E-isolate sandbox must expect 403, not 200"
            )
    return errors


def _unique_json_errors(contracts: dict[Path, Contract]) -> list[str]:
    errors: list[str] = []
    for path, contract in contracts.items():
        method, rest = _split_endpoint(contract.endpoint)
        if method != "POST":
            continue
        if contract.idempotency != "header_uuid_v4":
            continue
        if not _is_catalog_unique_path(rest):
            continue
        names = set(contract.fields)
        for spec in contract.fields.values():
            names.add(spec.json_name)
        if not names & _UNIQUE_FIELDS:
            continue
        if contract.unique_json:
            continue
        errors.append(
            f"contract {path}: POST with unique name/featureKey needs unique_json"
        )
    return errors


def _resource_id_errors(
    round_ref: str,
    contracts: dict[Path, Contract],
    missing: set[str],
) -> list[str]:
    errors: list[str] = []
    for contract in contracts.values():
        if not contract.resource_id_in_path:
            continue
        for name in sorted(_placeholders_in(contract.endpoint) - _SKIP):
            if name not in missing:
                continue
            errors.append(
                f"{round_ref}: resource_id_in_path needs a producer for {{{{{name}}}}} "
                "(POST contract.captures or H01 capture_response)"
            )
    return errors


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    method, _, rest = endpoint.strip().partition(" ")
    return method.upper(), rest


def _is_catalog_unique_path(path: str) -> bool:
    return any(marker in path for marker in _CATALOG_UNIQUE_PATHS)


def _producers(cases: list[CaseFile], contracts: dict[Path, Contract]) -> set[str]:
    names: set[str] = set()
    for contract in contracts.values():
        names.update(contract.captures)
        if contract.unique_json:
            names.add("last_unique_json")
    for case in cases:
        names.update((case.capture_response or {}).keys())
        names.update((case.capture or {}).keys())
        for dest, kind in (case.generate or {}).items():
            if str(kind) in _UUID_KINDS:
                names.add(dest.split(".")[-1])
    return names


def _case_consumers(case: CaseFile) -> set[str]:
    names = _placeholders_in(case.headers)
    names |= _placeholders_in(case.diff)
    names |= _placeholders_in(case.path_values)
    for dest, kind in (case.generate or {}).items():
        text = str(kind)
        if text.startswith("captured."):
            names.add(text.removeprefix("captured."))
        names |= _placeholders_in(dest)
        names |= _placeholders_in(text)
    return names


def _baseline_placeholders(root: Path, contract: Contract) -> set[str]:
    if not contract.baseline:
        return set()
    path = resolve_path(root, contract.baseline)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return _placeholders_in(payload)


def _placeholders_in(value: Any) -> set[str]:
    found: set[str] = set()
    if value is None:
        return found
    if isinstance(value, str):
        found.update(_PLACEHOLDER.findall(value))
        return found
    if isinstance(value, dict):
        for item in value.values():
            found |= _placeholders_in(item)
        for key in value:
            found |= _placeholders_in(str(key))
        return found
    if isinstance(value, list):
        for item in value:
            found |= _placeholders_in(item)
    return found
