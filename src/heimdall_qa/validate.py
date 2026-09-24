"""What a round must satisfy before it is worth running.

`validate` is the contract between the harness and whoever writes the YAML: it is the
gate a run refuses to pass, so a rule that lives here is a rule nobody has to remember.
Findings are not sentences — see `heimdall_qa.findings` for the code, pointer, fix and
reason every one of them carries.

Two scopes, two homes, and the split is what keeps the rules testable:

* a rule about **one contract** (its fields, its rules, its cases) lives here;
* a rule about **a round or a campaign** (its environment, its track, the placeholders
  it produces, the secrets it may not carry) lives in `session_validate`, because
  `validate_round` and `validate_campaign` both consume it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from heimdall_qa.coverage import expand
from heimdall_qa.descriptor import resolve_project
from heimdall_qa.findings import ValidationFinding
from heimdall_qa.findings import explain
from heimdall_qa.findings import finding
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import LoadedCase
from heimdall_qa.schema.load import iter_cases
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.load import load_suite
from heimdall_qa.schema.load import resolve_path
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import SuiteFile
from heimdall_qa.session_validate import validate_round_session

__all__ = [
    "ValidationFinding",
    "explain",
    "resolve_path",
    "todo_paths",
    "validate_round",
]

#: The sentinel the scaffolding writes when it could not derive a value. It is not
#: `{{capture}}` — nobody resolves this one at run time, so a case that carries it
#: sends the literal string as the request body and reports a result for a request
#: nobody wrote.
TODO = "TODO"


def todo_paths(value: Any, where: str = "") -> list[str]:
    """Every `TODO` sentinel inside `value`, named by its dotted path from `where`.

    It walks the whole structure rather than checking `expect.status`, because a
    `TODO` is just as wrong inside `diff`, `headers`, `path_values` or a field's
    `example`, and those are the places the earlier check let through.
    """
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(todo_paths(item, f"{where}.{key}" if where else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(todo_paths(item, f"{where}[{index}]"))
    elif isinstance(value, str) and value.strip().upper() == TODO:
        found.append(where or "value")
    return found


def _dumped(model: Any) -> Any:
    """A model as the plain data a `TODO` scan can walk, aliases and all."""
    return model.model_dump(by_alias=True, exclude_none=True)


def _todo_findings(reference: str, payload: Any) -> list[ValidationFinding]:
    return [
        finding(
            "TODO_SENTINEL",
            reference,
            f"{where} is {TODO}",
            fix=(
                "fill it: a `capture:` from an earlier case, `generate: <kind>`, or the"
                " real value. The scaffold writes `TODO` where it could not derive one."
            ),
        )
        for where in todo_paths(payload)
    ]


def validate_round(
    round_path: Path,
    root: Path,
    project: ProjectView | None = None,
) -> list[ValidationFinding]:
    """Validates one round against the project in force.

    `project` is optional only so a caller that holds no view — a CLI verb that
    was given nothing but a root — still works: it is resolved from `root` by the
    same precedence rule a run uses, so a round is never validated against one
    project and executed against another.
    """
    view = project if project is not None else resolve_project(root)
    findings: list[ValidationFinding] = []
    try:
        round_file = load_round(round_path)
    except (ValidationError, ValueError, OSError) as exc:
        return [
            finding(
                "ROUND_UNREADABLE",
                str(round_path),
                str(exc),
                fix=(
                    "open the round and correct the YAML it reports; nothing it names"
                    " can be checked until it loads."
                ),
            )
        ]

    loaded: list[LoadedCase] = []
    for relative in round_file.include:
        try:
            found = list(iter_cases(root, [relative], view))
        except (ValidationError, ValueError, OSError) as exc:
            findings.append(
                finding(
                    "CASE_UNREADABLE",
                    relative,
                    str(exc),
                    fix="open the case file and correct the YAML it reports.",
                )
            )
            continue
        for item in found:
            loaded.append(item)
            findings.extend(_todo_findings(item.reference, _dumped(item.case)))

    findings.extend(_contract_findings(loaded, root, view))
    findings.extend(validate_round_session(round_file, loaded, round_path, root, view))
    suite = _load_suite(round_file.suite, root, findings, view)
    if suite is not None and suite.steps:
        findings.extend(_validate_loop_cases(suite, root, view))
        return _unique(findings)
    findings.extend(_coverage_findings(loaded, root, view))
    return _unique(findings)


def _load_suite(
    relative: str,
    root: Path,
    findings: list[ValidationFinding],
    project: ProjectView,
) -> SuiteFile | None:
    if not relative:
        return None
    path = resolve_path(root, relative, project)
    if not path.is_file():
        return None
    try:
        return load_suite(path)
    except (ValidationError, ValueError, OSError) as exc:
        findings.append(
            finding(
                "SUITE_UNREADABLE",
                relative,
                str(exc),
                fix="open the suite and correct the YAML it reports.",
            )
        )
        return None


def _validate_loop_cases(
    suite: SuiteFile,
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for step in suite.steps:
        if step.loop is None:
            continue
        relative = step.loop.case
        try:
            found = list(iter_cases(root, [relative], project))
        except (ValidationError, ValueError, OSError) as exc:
            findings.append(
                finding(
                    "CASE_UNREADABLE",
                    relative,
                    str(exc),
                    fix="open the case file and correct the YAML it reports.",
                )
            )
            continue
        for item in found:
            findings.extend(_todo_findings(item.reference, _dumped(item.case)))
        findings.extend(_contract_findings(found, root, project))
    return findings


def _coverage_findings(
    loaded: list[LoadedCase],
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    """Every condition the contract declares, and the case that does not exist yet."""
    findings: list[ValidationFinding] = []
    grouped = _by_contract(loaded, root, project, findings)
    for contract_path, (contract, items) in grouped.items():
        present = {item.case.id for item in items}
        for item in expand(contract):
            if item.case_id in present:
                continue
            findings.append(
                finding(
                    "COVERAGE_GAP",
                    str(contract_path),
                    f"no case covers {item.case_id} (kind {item.kind})",
                    fix=(
                        f"add `{item.case_id}` to the area's case file, or write the"
                        f" stubs with `heimdall-qa scaffold-endpoint {contract_path}`"
                    ),
                )
            )
    return findings


def _by_contract(
    loaded: list[LoadedCase],
    root: Path,
    project: ProjectView,
    findings: list[ValidationFinding],
) -> dict[Path, tuple[Contract, list[LoadedCase]]]:
    """The contract of every case, loaded once, with its cases grouped under it."""
    grouped: dict[Path, list[LoadedCase]] = {}
    for item in loaded:
        path = resolve_path(root, item.case.contract, project)
        grouped.setdefault(path, []).append(item)
    contracts: dict[Path, tuple[Contract, list[LoadedCase]]] = {}
    for path, items in grouped.items():
        try:
            contract = load_contract(path)
        except (ValidationError, ValueError, OSError) as exc:
            findings.append(
                finding(
                    "CONTRACT_UNREADABLE",
                    str(path),
                    str(exc),
                    fix="open the contract and correct the YAML it reports.",
                )
            )
            continue
        contracts[path] = (contract, items)
    return contracts


def _contract_findings(
    loaded: list[LoadedCase],
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    """Contract-level checks, once per distinct contract a case set points at.

    Three of them: no `TODO` anywhere in the contract, every product rule naming its
    failure with a code or an error text, and no case expecting a 422 that no rule of
    the contract — and no axis table — explains.
    """
    findings: list[ValidationFinding] = []
    contracts = _by_contract(loaded, root, project, findings)
    for path, (contract, items) in contracts.items():
        findings.extend(_todo_findings(str(path), _dumped(contract)))
        findings.extend(_rule_identity_findings(path, contract))
        findings.extend(_status_without_rule_findings(path, contract, items, project))
    return findings


def _rule_identity_findings(
    contract_path: Path,
    contract: Contract,
) -> list[ValidationFinding]:
    """A rule that names no failure cannot be asserted, only believed."""
    return [
        finding(
            "RULE_NEEDS_IDENTITY",
            str(contract_path),
            f"rule {rule.id} declares neither `code` nor `error`",
            fix=(
                f"give rule {rule.id} a `code` (the product's error code) or an `error`"
                " (the message it returns), so a case can assert which failure it saw"
            ),
        )
        for rule in contract.rules
        if not rule.code and not (isinstance(rule.error, str) and rule.error.strip())
    ]


def _status_without_rule_findings(
    contract_path: Path,
    contract: Contract,
    loaded: list[LoadedCase],
    project: ProjectView,
) -> list[ValidationFinding]:
    """`422` is a claim about *why* the request failed, so something has to explain it.

    Two things can: a rule of the contract declaring `status: 422`, or the project's
    table saying the validation axis answers 422 (the reference advice answers 400
    there and reserves 422 for its own product rules). A case that asserts 422 with
    neither of them is asserting a failure nobody wrote.
    """
    if any(rule.status == 422 for rule in contract.rules):
        return []
    if project.error_status("validation") == 422:
        return []
    return [
        finding(
            "422_WITHOUT_RULE",
            item.reference,
            "expect.status is 422, but no rule of the contract declares 422",
            fix=(
                f"add a rule with `status: 422` to `rules[]` in {contract_path}, or"
                " change expect.status to the status the API really answers"
            ),
        )
        for item in loaded
        if _expects(item.case, 422)
    ]


def _expects(case: CaseFile, status: int) -> bool:
    """Whether the case expects `status`, tolerating the string a YAML file may hold."""
    declared = case.expect.status
    return declared == status or str(declared).strip() == str(status)


def _unique(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    return list(dict.fromkeys(findings))
