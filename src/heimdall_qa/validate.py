from pathlib import Path

from pydantic import ValidationError

from heimdall_qa.coverage import expand
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.load import load_suite
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import SuiteFile
from heimdall_qa.session_validate import validate_round_session


def resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return root / path


def validate_round(round_path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    try:
        round_file = load_round(round_path)
    except (ValidationError, ValueError, OSError) as exc:
        return [f"round: {exc}"]

    loaded: list[CaseFile] = []
    for relative in round_file.include:
        case_path = resolve_path(root, relative)
        try:
            case = load_case(case_path)
        except (ValidationError, ValueError, OSError) as exc:
            errors.append(f"case {relative}: {exc}")
            continue
        loaded.append(case)
        if str(case.expect.status).upper() == "TODO":
            errors.append(f"case {case.id}: expect.status is TODO")

    errors.extend(_rule_identity_errors(loaded, root))
    errors.extend(validate_round_session(round_file, loaded, root))
    suite = _load_suite(round_file.suite, root, errors)
    if suite is not None and suite.steps:
        errors.extend(_validate_loop_cases(suite, root))
        return _unique(errors)
    errors.extend(_coverage_errors(loaded, root))
    return _unique(errors)


def _load_suite(relative: str, root: Path, errors: list[str]) -> SuiteFile | None:
    if not relative:
        return None
    path = resolve_path(root, relative)
    if not path.is_file():
        return None
    try:
        return load_suite(path)
    except (ValidationError, ValueError, OSError) as exc:
        errors.append(f"suite {relative}: {exc}")
        return None


def _validate_loop_cases(suite: SuiteFile, root: Path) -> list[str]:
    errors: list[str] = []
    for step in suite.steps:
        if step.loop is None:
            continue
        relative = step.loop.case
        try:
            case = load_case(resolve_path(root, relative))
        except (ValidationError, ValueError, OSError) as exc:
            errors.append(f"case {relative}: {exc}")
            continue
        if str(case.expect.status).upper() == "TODO":
            errors.append(f"case {case.id}: expect.status is TODO")
        errors.extend(_rule_identity_errors([case], root))
    return errors


def _coverage_errors(loaded: list[CaseFile], root: Path) -> list[str]:
    errors: list[str] = []
    by_contract: dict[Path, list[CaseFile]] = {}
    for case in loaded:
        contract_path = resolve_path(root, case.contract)
        by_contract.setdefault(contract_path, []).append(case)
    for contract_path, cases in by_contract.items():
        try:
            contract = load_contract(contract_path)
        except (ValidationError, ValueError, OSError) as exc:
            errors.append(f"contract {contract_path}: {exc}")
            continue
        present = {case.id for case in cases}
        errors.extend(_rule_identity_errors(cases, root))
        for item in expand(contract):
            if item.case_id not in present:
                errors.append(f"coverage: missing {item.case_id}")
    return errors


def _rule_identity_errors(cases: list[CaseFile], root: Path) -> list[str]:
    errors: list[str] = []
    seen: set[Path] = set()
    for case in cases:
        contract_path = resolve_path(root, case.contract)
        if contract_path in seen:
            continue
        seen.add(contract_path)
        try:
            contract = load_contract(contract_path)
        except (ValidationError, ValueError, OSError) as exc:
            errors.append(f"contract {contract_path}: {exc}")
            continue
        for rule in contract.rules:
            if rule.code or (isinstance(rule.error, str) and rule.error.strip()):
                continue
            errors.append(
                f"contract {contract_path}: rule {rule.id} needs code or error"
            )
    return errors


def _unique(errors: list[str]) -> list[str]:
    return list(dict.fromkeys(errors))
