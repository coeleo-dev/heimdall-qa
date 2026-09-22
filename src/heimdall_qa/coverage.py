from dataclasses import dataclass

from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import FieldSpec
from heimdall_qa.schema.models import LiveOnlyRule


@dataclass(frozen=True)
class CoverageItem:
    case_id: str
    kind: str


def area_from_endpoint(endpoint: str) -> str:
    path = endpoint.strip().split()[-1]
    return path.rstrip("/").split("/")[-1]


def expand(contract: Contract) -> list[CoverageItem]:
    area = contract.area or area_from_endpoint(contract.endpoint)
    items: list[CoverageItem] = []

    def add(kind: str) -> None:
        items.append(CoverageItem(case_id=f"{area}-{kind}", kind=kind))

    add("H01")
    _add_optional_kinds(add, contract)
    _add_boundary_kinds(add, contract)
    _add_negative_kinds(add, contract)
    _add_auth_and_rules(add, contract)
    _add_idempotency_kinds(add, contract)
    _add_environment_kinds(add, contract)
    _add_live_only_kinds(add, contract.live_only_rules)
    return items


def _add_optional_kinds(add, contract: Contract) -> None:
    for name, field in contract.fields.items():
        if field.required:
            continue
        add(f"O-omit-{name}")
        add(f"O-set-{name}")
        if field.json_alias:
            add(f"O-alias-{name}")


def _add_boundary_kinds(add, contract: Contract) -> None:
    for name, field in contract.fields.items():
        if field.max_length is not None:
            add(f"B-max-{name}")
        if field.max_keys is not None:
            add(f"B-max-{name}-keys")
        if field.window is None:
            continue
        if field.window.future_minutes is not None:
            add(f"B-max-{name}-future")
        if field.window.past_hours is not None:
            add(f"B-min-{name}-past")


def _add_negative_kinds(add, contract: Contract) -> None:
    for name, field in contract.fields.items():
        if field.required:
            add(f"N-omit-{name}")
    for name, field in contract.fields.items():
        if field.pattern:
            add(f"N-pattern-{name}")
    for name, field in contract.fields.items():
        _add_over_kinds(add, name, field)
        for key in field.denylist:
            add(f"N-denylist-{key}")
    if contract.resource_id_in_path:
        add("N-notfound")
        add("S-bola")


def _add_over_kinds(add, name: str, field: FieldSpec) -> None:
    if field.max_length is not None:
        add(f"N-over-{name}")
    if field.max_keys is not None:
        add(f"N-over-{name}-keys")
    if field.window is None:
        return
    if field.window.future_minutes is not None:
        add(f"N-over-{name}-future")
    if field.window.past_hours is not None:
        add(f"N-over-{name}-past")


def _add_auth_and_rules(add, contract: Contract) -> None:
    if contract.auth != "none":
        add("N-auth")
    for rule in contract.rules:
        add(f"N-rule-{rule.id}")


def _add_idempotency_kinds(add, contract: Contract) -> None:
    if contract.idempotency == "header_uuid_v4":
        add("I-replay")
        add("I-new-key")
        add("I-missing")
        add("I-format")
    if contract.dedup:
        add(f"I-replay-{contract.dedup}")


def _add_environment_kinds(add, contract: Contract) -> None:
    method = contract.endpoint.strip().split()[0].upper()
    if method == "GET" and contract.auth == "jwt":
        add("E-isolate")
    if "P-GAP-6" in contract.p_gaps or contract.environment_conflict:
        add("E-conflict")


def _add_live_only_kinds(add, rules: list[LiveOnlyRule]) -> None:
    for rule in rules:
        add(f"P-{rule.id}")
