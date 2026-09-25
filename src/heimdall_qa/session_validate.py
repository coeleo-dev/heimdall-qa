"""The rules a *round* or a *campaign* must satisfy, shared by both entry points.

`validate_round` and `validate_campaign` both consume this module, which is why the
rules about sessions live here and not in `validate`:

* what a round includes is a **session** fact — which environment it runs in, which
  placeholders its cases produce and consume, whether a case belongs to another track;
* `validate_campaign_chain` walks the same rounds in order, carrying the captures of
  one round into the next, so the producer rule cannot live in either caller alone.

Every check answers with `ValidationFinding`, never with a sentence: the code is what a
test asserts and what `validate --explain` documents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from heimdall_qa.descriptor import resolve_project
from heimdall_qa.findings import ValidationFinding
from heimdall_qa.findings import finding
from heimdall_qa.fixtures import DOCUMENT_KINDS
from heimdall_qa.fixtures import SCALAR_KINDS
from heimdall_qa.fixtures import is_valid_document
from heimdall_qa.placeholders import is_seed
from heimdall_qa.placeholders import placeholder_names
from heimdall_qa.placeholders import unresolved
from heimdall_qa.project import ProjectView
from heimdall_qa.redact import credential_literals
from heimdall_qa.schema.load import LoadedCase
from heimdall_qa.schema.load import iter_cases
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.load import resolve_path
from heimdall_qa.schema.load import split_selector
from heimdall_qa.schema.models import CampaignFile
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import RoundFile

_SKIP = frozenset({"base_url"})
_UNIQUE_FIELDS = frozenset({"name", "feature_key", "featureKey"})
_UUID_KINDS = frozenset({"uuid", "uuid_v4"})

#: One character repeated: `'x' * (max_length + 1)` and its cousins. A seed that is
#: not data, under a field the harness knows how to generate.
_FILLER = re.compile(r"^([A-Za-z0-9])\1{3,}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_DOCUMENT = re.compile(
    r"^(?:\d{11}|\d{14}|\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})$"
)
_IDENTITY_SENTINEL = frozenset({"password", "refresh_token", "access_token"})


def validate_round_session(
    round_file: RoundFile,
    cases: list[LoadedCase],
    round_path: Path,
    root: Path,
    project: ProjectView | None = None,
) -> list[ValidationFinding]:
    """Every rule that needs the round's environment and its case set together."""
    view = project if project is not None else resolve_project(root)
    findings: list[ValidationFinding] = []
    contracts = _contracts_for_cases(cases, root, findings, view)
    findings.extend(_e_isolate_findings(round_file, cases))
    findings.extend(_live_only_findings(round_file, cases, contracts, root, view))
    findings.extend(_secret_findings(round_path, cases, contracts, root, view))
    findings.extend(_seed_findings(cases, contracts, root, view))
    findings.extend(_hardcoded_identity_findings(cases, contracts, root, view))
    findings.extend(_unique_json_findings(contracts, view))
    return findings


def validate_campaign_chain(
    campaign: CampaignFile,
    root: Path,
    project: ProjectView | None = None,
) -> list[ValidationFinding]:
    """The manifest's own rules, plus the captures that have to cross its rounds."""
    view = project if project is not None else resolve_project(root)
    findings: list[ValidationFinding] = []
    produced: set[str] = set()
    for entry in campaign.rounds:
        round_path = resolve_path(root, entry.round, view)
        findings.extend(_track_mix_findings(campaign, entry, round_path, root, view))
        if not round_path.is_file():
            continue
        try:
            round_file = load_round(round_path)
        except (ValidationError, ValueError, OSError):
            continue
        cases, case_findings = _load_cases(round_file, root, view)
        findings.extend(case_findings)
        contracts = _contracts_for_cases(cases, root, findings, view)
        consumers = _consumers(cases, contracts, root, view)
        unproduced = set(consumers) - _SKIP - produced - _producers(cases, contracts)
        for name in sorted(unproduced):
            findings.append(
                _no_producer_finding(f"{entry.round}: {consumers[name]}", name)
            )
        findings.extend(_resource_id_findings(entry.round, contracts, unproduced))
        produced |= _producers(cases, contracts)
        findings.extend(_unique_json_findings(contracts, view))
    return findings


def _load_cases(
    round_file: RoundFile,
    root: Path,
    project: ProjectView,
) -> tuple[list[LoadedCase], list[ValidationFinding]]:
    loaded: list[LoadedCase] = []
    findings: list[ValidationFinding] = []
    for relative in round_file.include:
        try:
            loaded.extend(iter_cases(root, [relative], project))
        except (ValidationError, ValueError, OSError) as exc:
            findings.append(
                finding(
                    "CASE_UNREADABLE",
                    relative,
                    str(exc),
                    fix="open the case file and correct the YAML it reports.",
                )
            )
    return loaded, findings


def _contracts_for_cases(
    cases: list[LoadedCase],
    root: Path,
    findings: list[ValidationFinding],
    project: ProjectView,
) -> dict[Path, Contract]:
    found: dict[Path, Contract] = {}
    for item in cases:
        path = resolve_path(root, item.case.contract, project)
        if path in found:
            continue
        try:
            found[path] = load_contract(path)
        except (ValidationError, ValueError, OSError) as exc:
            findings.append(
                finding(
                    "CONTRACT_UNREADABLE",
                    item.case.contract,
                    str(exc),
                    fix="open the contract and correct the YAML it reports.",
                )
            )
    return found


def _contract_of(
    item: LoadedCase,
    contracts: dict[Path, Contract],
    root: Path,
    project: ProjectView,
) -> Contract | None:
    return contracts.get(resolve_path(root, item.case.contract, project))


# ── environment ────────────────────────────────────────────────────────────────


def _e_isolate_findings(
    round_file: RoundFile,
    cases: list[LoadedCase],
) -> list[ValidationFinding]:
    """Isolation is only tested by a refusal: sandbox must answer 403, not 200.

    The refusal is the whole assertion, so any other expectation is the case looking
    at the wrong namespace — a `200` that a reviewer reads as "the boundary held", or
    a `404` that reads as "there was nothing there to isolate".
    """
    if round_file.environment != "sandbox":
        return []
    return [
        finding(
            "E_ISOLATE_SANDBOX_STATUS",
            item.reference,
            f"E-isolate expects {item.case.expect.status} in sandbox, not 403",
            fix=(
                "set expect.status: 403 — a case that reaches production data from"
                " sandbox is either a product defect or a case pointed at the wrong"
                " namespace, and both must be reported, not asserted as success"
            ),
        )
        for item in cases
        if item.case.kind == "E-isolate" and str(item.case.expect.status) != "403"
    ]


def _live_only_findings(
    round_file: RoundFile,
    cases: list[LoadedCase],
    contracts: dict[Path, Contract],
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    """A rule only production can answer, asked in sandbox, is a fabricated answer."""
    if round_file.environment != "sandbox":
        return []
    findings: list[ValidationFinding] = []
    for item in cases:
        contract = _contract_of(item, contracts, root, project)
        if contract is None:
            continue
        live_only = {rule.id for rule in contract.live_only_rules}
        for rule_id in sorted(live_only):
            if item.case.kind != f"P-{rule_id}":
                continue
            findings.append(
                finding(
                    "LIVE_ONLY_IN_SANDBOX",
                    item.reference,
                    f"kind P-{rule_id} is a live-only rule, and this round is sandbox",
                    fix=(
                        f"move `{item.reference}` to the live campaign, or drop the"
                        f" rule from `live_only_rules` in the contract if it is really"
                        " observable in sandbox"
                    ),
                )
            )
    return findings


def _track_mix_findings(
    campaign: CampaignFile,
    entry: Any,
    round_path: Path,
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    """What the manifest excludes cannot come back in through a round.

    `exclude` is how a campaign states its scope in one place (kind `D`, the sections
    that need a browser or a human eye). Nothing read it before, so a round that
    brought an excluded case back ran under a manifest that said it would not.
    """
    findings: list[ValidationFinding] = []
    excluded_sections = set(campaign.exclude.sections)
    if entry.matrix in excluded_sections:
        findings.append(
            finding(
                "TRACK_MIX",
                entry.round,
                f"matrix {entry.matrix} is excluded by the campaign's `exclude.sections`",
                fix=(
                    f"drop the entry from {campaign.id}, or remove {entry.matrix} from"
                    " `exclude.sections` if the campaign really covers it"
                ),
            )
        )
    if not round_path.is_file():
        return findings
    try:
        round_file = load_round(round_path)
    except (ValidationError, ValueError, OSError):
        return findings
    cases, _ = _load_cases(round_file, root, project)
    excluded_kinds = set(campaign.exclude.kinds)
    for item in cases:
        if item.case.kind not in excluded_kinds:
            continue
        findings.append(
            finding(
                "TRACK_MIX",
                item.reference,
                f"kind {item.case.kind} is excluded by the campaign's `exclude.kinds`",
                fix=(
                    f"drop `{item.reference}` from the round, or run it from a campaign"
                    f" that does not exclude kind {item.case.kind}"
                ),
            )
        )
    return findings


# ── what may not be written into content ───────────────────────────────────────


def _secret_findings(
    round_path: Path,
    cases: list[LoadedCase],
    contracts: dict[Path, Contract],
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    """A credential belongs in `secrets.local.yaml`, which is not committed.

    The text is scanned, not the model: a token in a `notes:` block or in a comment is
    just as committed as one in a field, and the redactor keeps those words out of the
    artifacts for the same reason.
    """
    findings: list[ValidationFinding] = []
    for path in _session_files(round_path, cases, contracts, root, project):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for literal in credential_literals(text, project.redact_patterns()):
            findings.append(
                finding(
                    "SECRET_IN_ROUND",
                    str(path),
                    f"carries a credential ({literal[:6]}…)",
                    fix=(
                        "move the value to `secrets.local.yaml` and reference it as"
                        " `secret.<name>`; content is committed, and a rotated token"
                        " in git is a rotation you cannot finish"
                    ),
                )
            )
    return findings


def _session_files(
    round_path: Path,
    cases: list[LoadedCase],
    contracts: dict[Path, Contract],
    root: Path,
    project: ProjectView,
) -> list[Path]:
    files: list[Path] = [round_path, *contracts.keys()]
    for item in cases:
        relative, _ = split_selector(item.reference)
        files.append(resolve_path(root, relative, project))
    for contract in contracts.values():
        if not contract.baseline:
            continue
        baseline = resolve_path(root, contract.baseline, project)
        files.append(baseline)
    seen: set[Path] = set()
    return [path for path in files if not (path in seen or seen.add(path))]


def _seed_findings(
    cases: list[LoadedCase],
    contracts: dict[Path, Contract],
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    """A seed the case never fills is a value the request sends verbatim.

    Two shapes, one reason: the `replace-with-…` sentinel the scaffold leaves behind,
    and the filler a boundary case writes by hand (`'x' * n`) under a field the harness
    would have generated — a password is not a string of `x`.
    """
    findings: list[ValidationFinding] = []
    for item in cases:
        contract = _contract_of(item, contracts, root, project)
        findings.extend(_case_seed_findings(item, project, contract))
        if contract is not None and contract.baseline:
            findings.extend(_baseline_seed_findings(item, contract, root, project))
    return findings


def _rule_declared_set(kind: str, contract: Contract | None) -> dict[str, Any]:
    """The `set` the contract's own rule declares, as leaf paths to values.

    A rule case names the rule it triggers (`N-rule-INVALID_CPF`), and the contract
    declares what triggers it. Where the case restates that value in its own
    `diff.set`, the value is the case's **subject** — and the filler check below
    cannot tell it from a seed nobody filled, because the two have the same shape.
    `'11111111111'` is eleven ones: as a filler that is laziness, as a CPF it is a
    document whose check digits cannot pass. The contract is what says which one it is,
    and this is where that is read.

    Sibling of `autofill.mechanical_diff`, which builds a rule case's `diff` from this
    same `set`; a case written by machine and a case written by hand then agree on what
    the rule's trigger is.
    """
    if contract is None:
        return {}
    if kind.startswith("N-rule-"):
        rules = contract.rules
        wanted = kind.removeprefix("N-rule-")
    elif kind.startswith("P-"):
        rules = contract.live_only_rules
        wanted = kind.removeprefix("P-")
    else:
        return {}
    for rule in rules:
        if rule.id == wanted:
            return {path: value for path, value in _leaves(rule.set or {})}
    return {}


def _case_seed_findings(
    item: LoadedCase,
    project: ProjectView,
    contract: Contract | None = None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    generated = set(item.case.generate or {})
    assigned = item.case.diff.get("set") if isinstance(item.case.diff.get("set"), dict) else {}
    declared = _rule_declared_set(item.case.kind, contract)
    for path, value in _leaves(assigned):
        if not isinstance(value, str):
            continue
        if is_seed(value) and path not in generated:
            findings.append(_seed_finding(item.reference, f"diff.set.{path}", value))
            continue
        if declared.get(path) == value:
            # The rule this case triggers declares this exact value, so the case is
            # stating its trigger and not leaving a seed. Read as a filler, the advice
            # would be to `generate` a *valid* document — the one value that cannot
            # provoke the rule the case exists for.
            continue
        kind = _identity_kind(path.rsplit(".", 1)[-1], project)
        if kind is not None and _FILLER.match(value):
            findings.append(
                finding(
                    "SEED_PLACEHOLDER",
                    item.reference,
                    f"diff.set.{path} is a filler seed for an identity field",
                    fix=(
                        f"generate it instead: `generate: {{{path}: {kind}}}` — a filler"
                        " is not identity data, and a boundary case pads the value the"
                        " harness generated rather than inventing one"
                    ),
                )
            )
    return findings


def _baseline_seed_findings(
    item: LoadedCase,
    contract: Contract,
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    path = resolve_path(root, contract.baseline, project)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    findings: list[ValidationFinding] = []
    for dotted, value in _leaves(payload):
        if not isinstance(value, str) or not is_seed(value):
            continue
        if _fills(item.case, dotted):
            continue
        name = dotted.rsplit(".", 1)[-1]
        findings.append(
            _seed_finding(
                item.reference,
                f"{contract.baseline}: {dotted}",
                value,
                fix=(
                    f"let the case fill it — `generate: {{{dotted}: {name if name in SCALAR_KINDS else '<kind>'}}}`"
                    " or a `diff.set` for it — or delete the entry if the API does not"
                    " take the field"
                ),
            )
        )
    return findings


def _seed_finding(
    reference: str,
    where: str,
    value: str,
    fix: str = "",
) -> ValidationFinding:
    return finding(
        "SEED_PLACEHOLDER",
        reference,
        f"{where} is still the seed {value!r}",
        fix=fix
        or (
            "fill the seed: `generate: {<path>: <kind>}` in the case, or a"
            " `diff.set` with the value this case means to send"
        ),
    )


def _fills(case: CaseFile, dotted: str) -> bool:
    """Whether the case sets, omits or generates `dotted` (or a path around it)."""
    set_paths = case.diff.get("set") if isinstance(case.diff.get("set"), dict) else {}
    omitted = case.diff.get("omit") if isinstance(case.diff.get("omit"), list) else []
    for candidate in (*set_paths, *(case.generate or {}), *omitted):
        if str(candidate) == dotted:
            return True
        if dotted.startswith(f"{candidate}.") or str(candidate).startswith(f"{dotted}."):
            return True
    return False


def _identity_kind(name: str, project: ProjectView) -> str | None:
    """The fixture kind an identity-bearing field name maps to, or `None`.

    A declared `fixtures.field_kinds` entry wins — a project that calls its tax id
    `tax_id` says so — and the scalar kinds the harness can always build are the floor.
    """
    declared = project.field_kinds().get(name)
    if declared:
        return declared
    return name if name in SCALAR_KINDS else None


def _hardcoded_identity_findings(
    cases: list[LoadedCase],
    contracts: dict[Path, Contract],
    root: Path,
    project: ProjectView,
) -> list[ValidationFinding]:
    """Identity data comes from the harness, so the next run does not collide.

    Only two shapes count, because only two are unmistakable from the value alone: an
    e-mail address and a national document number. A value the contract itself declares
    as an `example` is data the API documentation asked for, not something invented.
    """
    declared: set[str] = set()
    for contract in contracts.values():
        for field in contract.fields.values():
            if isinstance(field.example, str):
                declared.add(field.example)
    findings: list[ValidationFinding] = []
    for item in cases:
        findings.extend(
            _identity_in(
                item.reference,
                _case_values(item.case),
                declared,
                project,
            )
        )
    for path, contract in contracts.items():
        if not contract.baseline:
            continue
        baseline = resolve_path(root, contract.baseline, project)
        if not baseline.is_file():
            continue
        try:
            payload = json.loads(baseline.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        findings.extend(
            _identity_in(f"{path}: {contract.baseline}", payload, declared, project)
        )
    return findings


def _identity_in(
    reference: str,
    payload: Any,
    declared: set[str],
    project: ProjectView,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for dotted, value in _leaves(payload):
        if not isinstance(value, str) or value in declared:
            continue
        if unresolved(value):
            continue
        kind = _literal_kind(value, dotted.rsplit(".", 1)[-1], project)
        if kind is None:
            continue
        name = dotted.rsplit(".", 1)[-1]
        findings.append(
            finding(
                "HARDCODED_ID",
                reference,
                f"{dotted} is a literal {kind}",
                fix=(
                    "declaring a value that must be unique is not a value: use"
                    f" `generate: {{{name}: {kind}}}` (or `heimdall-qa fixture {kind}`),"
                    " so a second run does not collide with the first"
                ),
            )
        )
    return findings


def _literal_kind(value: str, name: str, project: ProjectView) -> str | None:
    """What kind of identity `value` looks like, when it looks like one at all.

    Two shapes are unmistakable and they are treated differently on purpose:

    * an **e-mail address** is one by construction, so the value alone is enough;
    * a **document number** is eleven or fourteen digits, which is also what a phone
      number is — so the field name has to declare the kind, and the check digits have
      to pass. A document a case broke on purpose is its subject, not identity data:
      no re-run will collide with a value the API is expected to reject.
    """
    kind = _identity_kind(name, project)
    if kind in _IDENTITY_SENTINEL and _FILLER.match(value):
        return kind
    if _EMAIL.match(value):
        return "email"
    if (
        kind in DOCUMENT_KINDS
        and _DOCUMENT.match(value)
        and is_valid_document(value, kind)
    ):
        return kind
    return None


def _case_values(case: CaseFile) -> dict[str, Any]:
    """The parts of a case that become the request, and nothing else."""
    return {
        "diff": case.diff,
        "headers": case.headers,
        "path_values": case.path_values,
    }


def _leaves(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_leaves(item, f"{prefix}.{key}" if prefix else str(key)))
        return found
    if isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_leaves(item, f"{prefix}[{index}]"))
        return found
    return [(prefix, value)]


# ── placeholders and producers ─────────────────────────────────────────────────
#
# The producer rule needs the *history* of a run, so it is a campaign rule and not
# a round one: a round that consumes `{{billable_metric_id}}` is correct when the
# round before it created one, and `validate` on that round alone cannot know. The
# chain in `validate_campaign_chain` is where the captures of one round are carried
# into the next, and it is the only caller of the helpers below.


def _consumers(
    cases: list[LoadedCase],
    contracts: dict[Path, Contract],
    root: Path,
    project: ProjectView,
) -> dict[str, str]:
    """Placeholder name -> the file that asks for it, first mention winning."""
    consumers: dict[str, str] = {}
    for path, contract in contracts.items():
        for name in placeholder_names(contract.endpoint):
            consumers.setdefault(name, str(path))
        for name in _baseline_placeholders(root, contract, project):
            consumers.setdefault(name, f"{path}: {contract.baseline}")
    for item in cases:
        for name in _case_consumers(item.case):
            consumers.setdefault(name, item.reference)
    return consumers


def _no_producer_finding(where: str, name: str) -> ValidationFinding:
    return finding(
        "PLACEHOLDER_NO_PRODUCER",
        where or "round",
        f"placeholder {{{{{name}}}}} has no producer before this round",
        fix=(
            "produce it: `capture_response` on the H01 that creates it,"
            " `contract.captures`, `capture:`, or `generate: {<field>: uuid}` —"
            " otherwise the API receives literal braces"
        ),
    )


def _resource_id_findings(
    round_ref: str,
    contracts: dict[Path, Contract],
    missing: set[str],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for contract in contracts.values():
        if not contract.resource_id_in_path:
            continue
        for name in sorted(placeholder_names(contract.endpoint) - _SKIP):
            if name not in missing:
                continue
            findings.append(
                finding(
                    "RESOURCE_ID_NO_PRODUCER",
                    round_ref,
                    f"resource_id_in_path needs a producer for {{{{{name}}}}}",
                    fix=(
                        "capture the id the POST returned (`captures:` on the contract"
                        " or `capture_response` on its H01) before this round runs"
                    ),
                )
            )
    return findings


def _unique_json_findings(
    contracts: dict[Path, Contract],
    project: ProjectView,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for path, contract in contracts.items():
        if not _needs_unique_json(contract, project):
            continue
        findings.append(
            finding(
                "UNIQUE_JSON_MISSING",
                str(path),
                "a POST whose name must be unique declares no `unique_json`",
                fix=(
                    "declare `unique_json: <field>` in the contract, so the harness"
                    " generates a name the catalog has not seen"
                ),
            )
        )
    return findings


def _needs_unique_json(contract: Contract, project: ProjectView) -> bool:
    method, rest = _split_endpoint(contract.endpoint)
    if method != "POST" or contract.idempotency != "header_uuid_v4":
        return False
    if not project.catalog_unique(rest) or contract.unique_json:
        return False
    names = set(contract.fields)
    for spec in contract.fields.values():
        names.add(spec.json_name)
    return bool(names & _UNIQUE_FIELDS)


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    method, _, rest = endpoint.strip().partition(" ")
    return method.upper(), rest


def _producers(cases: list[LoadedCase], contracts: dict[Path, Contract]) -> set[str]:
    names: set[str] = set()
    for contract in contracts.values():
        names.update(contract.captures)
        if contract.unique_json:
            names.add("last_unique_json")
    for item in cases:
        names.update((item.case.capture_response or {}).keys())
        names.update((item.case.capture or {}).keys())
        for dest, kind in (item.case.generate or {}).items():
            if str(kind) in _UUID_KINDS:
                names.add(dest.split(".")[-1])
    return names


def _case_consumers(case: CaseFile) -> set[str]:
    names = placeholder_names(case.headers)
    names |= placeholder_names(case.diff)
    names |= placeholder_names(case.path_values)
    for dest, kind in (case.generate or {}).items():
        text = str(kind)
        if text.startswith("captured."):
            names.add(text.removeprefix("captured."))
        names |= placeholder_names(dest)
        names |= placeholder_names(text)
    return names


def _baseline_placeholders(
    root: Path,
    contract: Contract,
    project: ProjectView,
) -> set[str]:
    if not contract.baseline:
        return set()
    path = resolve_path(root, contract.baseline, project)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return placeholder_names(payload)
