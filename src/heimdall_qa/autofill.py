from typing import Any

from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import FieldSpec
from heimdall_qa.schema.models import LiveOnlyRule
from heimdall_qa.schema.models import RuleSpec

TODO_EXPECT = {"status": "TODO"}


def mechanical_expect(
    kind: str,
    contract: Contract,
    happy_status: int | None,
    project: ProjectView,
) -> dict[str, int | str]:
    """The `expect` a generated case starts with.

    The status of each negative axis comes from the project, not from a literal
    here: 400 for a malformed body and 422 for a product rule are both common, and
    which one a target answers is a fact about the target. Only the product-rule
    axis is read from the contract, because it varies per rule within one API.
    """
    if kind == "H01":
        return _status_or_todo(happy_status)
    if _is_bean_validation_kind(kind):
        return {"status": project.validation_status()}
    if kind == "N-auth":
        return {"status": project.error_status("auth")}
    if kind.startswith("N-rule-"):
        return _rule_expect(kind.removeprefix("N-rule-"), contract.rules)
    if kind == "I-missing":
        return {"status": project.error_status("missing_header")}
    if kind == "I-format":
        return {"status": project.validation_status()}
    if kind in {"N-notfound", "S-bola"}:
        return {"status": project.error_status("not_found")}
    if kind == "E-conflict":
        return {"status": project.error_status("environment_conflict")}
    if kind == "E-isolate":
        return {"status": project.error_status("environment_isolation")}
    if _is_happy_clone_kind(kind):
        return _two_xx_or_todo(happy_status)
    if kind.startswith("P-"):
        return _live_only_expect(kind.removeprefix("P-"), contract.live_only_rules)
    return dict(TODO_EXPECT)


def mechanical_payload(
    kind: str,
    contract: Contract,
    happy_status: int | None,
    project: ProjectView,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "diff": mechanical_diff(kind, contract),
        "expect": mechanical_expect(kind, contract, happy_status, project),
    }
    omit_headers = mechanical_omit_headers(kind, contract, project)
    if omit_headers:
        payload["omit_headers"] = omit_headers
    headers = mechanical_headers(kind, contract, project)
    if headers:
        payload["headers"] = headers
    burst = mechanical_burst(kind, contract)
    if burst:
        payload["burst"] = burst
    saturate = mechanical_saturate(kind, contract)
    if saturate:
        payload["saturate"] = saturate
    if kind == "H01" and contract.captures:
        payload["capture_response"] = dict(contract.captures)
    return payload


def mechanical_diff(kind: str, contract: Contract) -> dict[str, Any]:
    if kind.startswith("N-omit-") or kind.startswith("O-omit-"):
        field = _field_after(kind, ("N-omit-", "O-omit-"))
        return {"omit": [_json_name(contract, field)]}
    if kind.startswith("O-set-"):
        field = kind.removeprefix("O-set-")
        spec = contract.fields.get(field)
        value = spec.example if spec is not None and spec.example is not None else "qa"
        return {"set": {_json_name(contract, field): value}}
    if kind.startswith("N-pattern-"):
        field = kind.removeprefix("N-pattern-")
        spec = contract.fields.get(field)
        return {"set": {_json_name(contract, field): _invalid_pattern_value(spec)}}
    if kind.startswith("N-over-") and kind.endswith("-keys"):
        field = kind.removeprefix("N-over-").removesuffix("-keys")
        spec = contract.fields.get(field)
        count = (spec.max_keys + 1) if spec is not None and spec.max_keys else 33
        return {"set": {_json_name(contract, field): _key_collection(spec, count)}}
    if kind.startswith("N-over-") and not kind.endswith("-future") and not kind.endswith("-past"):
        field = kind.removeprefix("N-over-")
        spec = contract.fields.get(field)
        length = (spec.max_length + 1) if spec is not None and spec.max_length else 65
        return {"set": {_json_name(contract, field): _length_value(field, spec, length)}}
    if kind.startswith("B-max-") and kind.endswith("-keys"):
        field = kind.removeprefix("B-max-").removesuffix("-keys")
        spec = contract.fields.get(field)
        if spec is None or spec.max_keys is None:
            return {}
        return {"set": {_json_name(contract, field): _key_collection(spec, spec.max_keys)}}
    if kind.startswith("B-max-") and not kind.endswith("-future"):
        field = kind.removeprefix("B-max-")
        spec = contract.fields.get(field)
        if spec is None or spec.max_length is None:
            return {}
        return {
            "set": {
                _json_name(contract, field): _length_value(field, spec, spec.max_length)
            }
        }
    if kind.startswith("N-rule-"):
        return _rule_diff(kind.removeprefix("N-rule-"), contract.rules)
    if kind.startswith("P-"):
        return _live_diff(kind.removeprefix("P-"), contract.live_only_rules)
    if kind.startswith("N-denylist-"):
        key = kind.removeprefix("N-denylist-")
        return {"set": {f"properties.{key}": "redacted"}}
    return {}


def mechanical_omit_headers(
    kind: str,
    contract: Contract,
    project: ProjectView,
) -> list[str]:
    """The headers a generated case must leave out to produce the failure it names.

    `N-auth` used to omit the literal `Authorization`, which made the harness
    unable to test a route whose credential travels in another header — and the
    header is a fact the descriptor already declares, next to the scheme the
    contract names. Reading it here is the same move fase 1.4 made everywhere
    else: the literal was the product's, not the harness's.
    """
    if kind == "N-auth":
        scheme = project.auth_named(contract.auth)
        # `auth: none` on an `N-auth` case means the contract and the coverage
        # disagree; the harness's own default header is the honest answer, and the
        # case will fail loudly rather than silently send no header at all.
        return [scheme.header if scheme is not None else "Authorization"]
    if kind == "I-missing":
        return ["X-Idempotency-Key"]
    if kind.startswith("N-rule-"):
        for rule in contract.rules:
            if rule.id == kind.removeprefix("N-rule-") and rule.omit_headers:
                return list(rule.omit_headers)
    return []


def mechanical_burst(kind: str, contract: Contract) -> int | None:
    rule = _rule_for_kind(kind, contract)
    if rule is None or rule.saturate is not None:
        return None
    return rule.burst


def mechanical_saturate(kind: str, contract: Contract) -> dict[str, Any] | None:
    rule = _rule_for_kind(kind, contract)
    if rule is None or rule.saturate is None:
        return None
    return rule.saturate.model_dump(exclude_none=True)


def mechanical_headers(
    kind: str,
    contract: Contract,
    project: ProjectView,
) -> dict[str, str]:
    """The headers a generated case has to *add* to produce the failure it names.

    Every arm here was unreachable until the Java parity fixture ran: the
    environment arm returned early, so `I-format` sent a perfectly good
    idempotency key while its `expect` said 400, and a rule that fails only with
    a particular header was sent without it. The reference corpus already carried
    those headers because a human wrote them, which is why nothing failed — a
    hand-authored case is not a test of the generator.
    """
    if kind in {"E-conflict", "E-isolate"}:
        # The point of these cases is that the same resource collides across
        # environments, so they are generated against the second one. A project with
        # no environment header cannot express that, and a case that silently omits
        # it would pass while testing nothing.
        name = project.environment_header_name()
        isolation = project.isolation_environment()
        if name is None or isolation is None:
            raise HarnessError(
                code="DESCRIPTOR_ENVIRONMENT_HEADER_MISSING",
                message=(
                    f"case kind {kind} needs a second environment, but the project"
                    " descriptor declares no environment_header.isolation"
                ),
                hint=(
                    "declare environment_header.name, .values and .isolation in the"
                    " project descriptor"
                ),
            )
        return {name: project.environment_value(isolation)}
    if kind == "I-format":
        return {"X-Idempotency-Key": "not-a-uuid-v4"}
    if kind.startswith("N-rule-"):
        for rule in contract.rules:
            if rule.id == kind.removeprefix("N-rule-") and rule.headers:
                return dict(rule.headers)
    if kind.startswith("P-"):
        for rule in contract.live_only_rules:
            if rule.id == kind.removeprefix("P-") and rule.headers:
                return dict(rule.headers)
    return {}


def _is_bean_validation_kind(kind: str) -> bool:
    return (
        kind.startswith("N-omit-")
        or kind.startswith("N-pattern-")
        or kind.startswith("N-over-")
        or kind.startswith("N-denylist-")
    )


def _is_happy_clone_kind(kind: str) -> bool:
    if kind in {"I-replay", "I-new-key"}:
        return True
    if kind.startswith("I-replay-"):
        return True
    if kind.startswith("O-"):
        return True
    return kind.startswith("B-")


def _status_or_todo(status: int | None) -> dict[str, int | str]:
    if status is None:
        return dict(TODO_EXPECT)
    return {"status": status}


def _two_xx_or_todo(status: int | None) -> dict[str, int | str]:
    if status is None or status < 200 or status >= 300:
        return dict(TODO_EXPECT)
    return {"status": status}


def _rule_expect(rule_id: str, rules: list[RuleSpec]) -> dict[str, int | str]:
    for rule in rules:
        if rule.id != rule_id:
            continue
        payload: dict[str, int | str] = {"status": rule.status}
        if rule.code:
            payload["code"] = rule.code
        return payload
    return dict(TODO_EXPECT)


def _live_only_expect(rule_id: str, rules: list[LiveOnlyRule]) -> dict[str, int | str]:
    for rule in rules:
        if rule.id != rule_id:
            continue
        if rule.status is None:
            return dict(TODO_EXPECT)
        return {"status": rule.status}
    return dict(TODO_EXPECT)


def _rule_diff(rule_id: str, rules: list[RuleSpec]) -> dict[str, Any]:
    for rule in rules:
        if rule.id != rule_id:
            continue
        diff: dict[str, Any] = {}
        if rule.omit:
            diff["omit"] = list(rule.omit)
        if rule.set:
            diff["set"] = dict(rule.set)
        return diff
    return {}


def _live_diff(rule_id: str, rules: list[LiveOnlyRule]) -> dict[str, Any]:
    for rule in rules:
        if rule.id != rule_id:
            continue
        diff: dict[str, Any] = {}
        if rule.omit:
            diff["omit"] = list(rule.omit)
        if rule.set:
            diff["set"] = dict(rule.set)
        return diff
    return {}


def _field_after(kind: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if kind.startswith(prefix):
            return kind.removeprefix(prefix)
    return kind


def _json_name(contract: Contract, field: str) -> str:
    spec = contract.fields.get(field)
    if spec is None:
        return field
    return spec.json_name


def _key_collection(spec: FieldSpec | None, count: int) -> list[str] | dict[str, int]:
    keys = [f"k{index}" for index in range(count)]
    if spec is not None and spec.flat:
        return {key: 1 for key in keys}
    return keys


def _rule_for_kind(kind: str, contract: Contract) -> RuleSpec | None:
    if not kind.startswith("N-rule-"):
        return None
    wanted = kind.removeprefix("N-rule-")
    for rule in contract.rules:
        if rule.id == wanted:
            return rule
    return None


def _length_value(field: str, spec: FieldSpec | None, length: int) -> str:
    seed = _length_seed(field, spec)
    return (seed * ((length // len(seed)) + 1))[:length]


def _length_seed(field: str, spec: FieldSpec | None) -> str:
    if spec is not None and isinstance(spec.example, str) and spec.example:
        return spec.example
    lowered = field.lower()
    if lowered == "password" or lowered.endswith("_password"):
        return "Aa1x"
    return "a"


def _invalid_pattern_value(spec: FieldSpec | None) -> Any:
    if spec is not None and spec.invalid is not None:
        return spec.invalid
    pattern = spec.pattern if spec is not None else None
    if pattern is None:
        return "!!!"
    if "SANDBOX" in pattern or "PRODUCTION" in pattern:
        return "STAGING"
    if "CPF|CNPJ" in pattern:
        return "PJ"
    if "^[a-z]" in pattern:
        return "ABC"
    if "yyyy" in pattern or "\\d{4}-\\d{2}-\\d{2}" in pattern:
        return "01-01-1990"
    return "!!!"
