"""The rules that used to be prose, one violation each.

`validate` is the contract between the harness and whoever writes the YAML. A rule
that lives only in a skill is a rule applied from memory, and memory is not a gate:
F6 measured nine of twenty-one actionable rules as machine-checkable and unchecked,
and the one the skill led with — *never invent a 422 that no `rules[]` explains* —
came back with zero mentions on a case that did exactly that.

Every test below violates one code and then repairs it, because the failure mode of
a rule is not being missing: it is being eager. A rule that fires on correct content
teaches authors to ignore the validator, which is how a team ends up back at prose.
The assertion is the `code`, never the sentence, so a rewording cannot retire a rule
in silence.
"""

import json
from pathlib import Path

import pytest
import yaml

from heimdall_qa.findings import RULE_WHY
from heimdall_qa.findings import explain
from heimdall_qa.findings import finding
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.session_validate import validate_campaign_chain
from heimdall_qa.testing import project_at
from heimdall_qa.validate import validate_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The descriptor in force, the same file the CLI resolves for this repository's own
#: fixtures: `document_number` is declared a document, which the seed and identity
#: rules need in order to know what they are looking at.
PROJECT = project_at(FIXTURES / "qa" / "project.yaml")

_CONTRACT = {
    "endpoint": "POST /qa/echo",
    "auth": "none",
    "baseline": "baselines/empty.json",
    "fields": {
        "email": {"required": True, "type": "string", "json": "email"},
        "document_number": {
            "required": False,
            "type": "string",
            "json": "document_number",
        },
    },
    "rules": [],
}

_H01 = {
    "kind": "H01",
    "expect": {"status": 200},
}


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _write_json(path: Path, payload: object) -> Path:
    """A baseline is JSON, and the rules that read one read it as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _tree(
    tmp_path: Path,
    *,
    contract: dict | None = None,
    case: dict | None = None,
    baseline: object | None = None,
    environment: str = "sandbox",
) -> Path:
    """One contract, one case file, one round. The shape a rule can be violated in."""
    _write(tmp_path / "contracts" / "echo.yaml", contract or _CONTRACT)
    _write(
        tmp_path / "cases" / "echo.yaml",
        {"echo-H01": {"contract": "contracts/echo.yaml", **(case or _H01)}},
    )
    _write_json(tmp_path / "baselines" / "empty.json", baseline or {})
    return _write(
        tmp_path / "round.yaml",
        {
            "id": "echo",
            "suite": "suites/unused.yaml",
            "mode": "review",
            "environment": environment,
            "include": ["cases/echo.yaml"],
        },
    )


def _codes(findings: list) -> set[str]:
    return {item.code for item in findings}


# ── 422_WITHOUT_RULE ───────────────────────────────────────────────────────────


def test_a_422_no_rule_explains_is_refused(tmp_path: Path):
    """The rule the skill led with, and the one that had no check behind it.

    A 422 is a claim about *why* the request failed. Either a rule of the contract
    declares it or the project's axis table does; a case that asserts it with
    neither is asserting a failure nobody wrote.
    """
    round_path = _tree(
        tmp_path,
        case={**_H01, "kind": "N-rule-INVENTED", "expect": {"status": 422}},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "422_WITHOUT_RULE" in _codes(findings)


def test_a_422_a_rule_declares_is_accepted(tmp_path: Path):
    """The repair: declare the rule, and the same case passes."""
    contract = {
        **_CONTRACT,
        "rules": [{"id": "QUOTA", "status": 422, "error": "QUOTA_EXCEEDED"}],
    }
    round_path = _tree(
        tmp_path,
        contract=contract,
        case={**_H01, "kind": "N-rule-QUOTA", "expect": {"status": 422}},
    )
    assert "422_WITHOUT_RULE" not in _codes(validate_round(round_path, tmp_path, PROJECT))


# ── RULE_NEEDS_IDENTITY ────────────────────────────────────────────────────────


def test_a_rule_that_names_no_failure_cannot_be_asserted(tmp_path: Path):
    """A rule is only testable if it says what the API answers with."""
    contract = {**_CONTRACT, "rules": [{"id": "DUPLICATE", "status": 409}]}
    round_path = _tree(
        tmp_path,
        contract=contract,
        case={**_H01, "kind": "N-rule-DUPLICATE", "expect": {"status": 409}},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "RULE_NEEDS_IDENTITY" in _codes(findings)


def test_a_rule_with_a_code_has_an_identity(tmp_path: Path):
    contract = {
        **_CONTRACT,
        "rules": [{"id": "DUPLICATE", "status": 409, "code": "DUPLICATE_NAME"}],
    }
    round_path = _tree(
        tmp_path,
        contract=contract,
        case={**_H01, "kind": "N-rule-DUPLICATE", "expect": {"status": 409}},
    )
    assert "RULE_NEEDS_IDENTITY" not in _codes(
        validate_round(round_path, tmp_path, PROJECT)
    )


# ── SEED_PLACEHOLDER ───────────────────────────────────────────────────────────


def test_a_seed_the_case_never_fills_is_refused(tmp_path: Path):
    """`replace-with-generate` is a question, and a question is not a value.

    The runner already refuses it; the point of the rule is that the author hears
    about it from `validate` and not from a failed run.
    """
    round_path = _tree(
        tmp_path,
        case={**_H01, "diff": {"set": {"email": "replace-with-generate"}}},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "SEED_PLACEHOLDER" in _codes(findings)


def test_generating_the_seed_is_the_repair(tmp_path: Path):
    round_path = _tree(
        tmp_path,
        case={
            **_H01,
            "diff": {"set": {"email": "replace-with-generate"}},
            "generate": {"email": "email"},
        },
    )
    assert "SEED_PLACEHOLDER" not in _codes(
        validate_round(round_path, tmp_path, PROJECT)
    )


def test_a_baseline_seed_the_case_does_not_fill_is_refused(tmp_path: Path):
    round_path = _tree(
        tmp_path,
        baseline={"email": "replace-with-generate"},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "SEED_PLACEHOLDER" in _codes(findings)


# ── HARDCODED_ID ───────────────────────────────────────────────────────────────


def test_a_literal_identity_value_is_refused(tmp_path: Path):
    """A value the API must consider unique cannot be a literal.

    The second run collides with the first, and the failure reads as a product
    defect — which is exactly the kind of evidence a review exists to not produce.
    """
    round_path = _tree(
        tmp_path,
        case={**_H01, "diff": {"set": {"email": "someone@example.com"}}},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "HARDCODED_ID" in _codes(findings)


def test_a_generated_identity_value_is_accepted(tmp_path: Path):
    round_path = _tree(
        tmp_path,
        case={
            **_H01,
            "diff": {"set": {"email": "{{register_email}}"}},
        },
    )
    assert "HARDCODED_ID" not in _codes(validate_round(round_path, tmp_path, PROJECT))


def test_a_document_a_case_breaks_on_purpose_is_not_identity_data(tmp_path: Path):
    """`11111111111` is what an `N-rule-INVALID_CPF` sends, and it is the point.

    Identity data is a value the harness generates *because it has to be valid*; a
    value the case means to be rejected will never collide with anything. Without
    that distinction the rule refuses correct content, which is how a rule earns
    being ignored.
    """
    round_path = _tree(
        tmp_path,
        case={
            **_H01,
            "kind": "N-rule-INVALID_CPF",
            "diff": {"set": {"document_number": "11111111111"}},
            "expect": {"status": 400},
        },
    )
    assert "HARDCODED_ID" not in _codes(validate_round(round_path, tmp_path, PROJECT))


def test_a_phone_number_is_not_a_document(tmp_path: Path):
    """Eleven digits is a CPF and a mobile number; the field name breaks the tie."""
    round_path = _tree(
        tmp_path,
        case={**_H01, "diff": {"set": {"mobile_phone": "11999999999"}}},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "HARDCODED_ID" not in _codes(findings)


def test_a_value_the_contract_declares_as_an_example_is_data(tmp_path: Path):
    """What the API's own documentation asks for is not something anyone invented."""
    contract = {
        **_CONTRACT,
        "fields": {
            "email": {
                "required": True,
                "type": "string",
                "json": "email",
                "example": "doc@example.com",
            }
        },
    }
    round_path = _tree(
        tmp_path,
        contract=contract,
        case={**_H01, "diff": {"set": {"email": "doc@example.com"}}},
    )
    assert "HARDCODED_ID" not in _codes(validate_round(round_path, tmp_path, PROJECT))


# ── SECRET_IN_ROUND ────────────────────────────────────────────────────────────


def test_a_credential_in_the_rounds_own_content_is_refused(tmp_path: Path):
    """A credential in content is a credential committed to git."""
    round_path = _tree(
        tmp_path,
        case={**_H01, "headers": {"Authorization": "Bearer test_key_plantedkey"}},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "SECRET_IN_ROUND" in _codes(findings)


def test_a_reference_to_a_secret_is_how_content_carries_one(tmp_path: Path):
    round_path = _tree(
        tmp_path,
        case={**_H01, "headers": {"Authorization": "Bearer {{api_key}}"}},
    )
    assert "SECRET_IN_ROUND" not in _codes(
        validate_round(round_path, tmp_path, PROJECT)
    )


# ── LIVE_ONLY_IN_SANDBOX ───────────────────────────────────────────────────────


def test_a_live_only_rule_asked_in_sandbox_is_refused(tmp_path: Path):
    """A rule only production can answer, asserted in sandbox, is an invented answer."""
    contract = {**_CONTRACT, "live_only_rules": [{"id": "GO_LIVE", "status": 200}]}
    round_path = _tree(
        tmp_path,
        contract=contract,
        case={**_H01, "kind": "P-GO_LIVE"},
    )
    findings = validate_round(round_path, tmp_path, PROJECT)
    assert "LIVE_ONLY_IN_SANDBOX" in _codes(findings)


def test_the_same_rule_in_the_live_environment_is_the_point(tmp_path: Path):
    contract = {**_CONTRACT, "live_only_rules": [{"id": "GO_LIVE", "status": 200}]}
    round_path = _tree(
        tmp_path,
        contract=contract,
        case={**_H01, "kind": "P-GO_LIVE"},
        environment="production",
    )
    assert "LIVE_ONLY_IN_SANDBOX" not in _codes(
        validate_round(round_path, tmp_path, PROJECT)
    )


# ── TRACK_MIX ──────────────────────────────────────────────────────────────────


def _campaign(tmp_path: Path, round_path: Path, **exclude: object) -> Path:
    return _write(
        tmp_path / "campaigns" / "c.yaml",
        {
            "id": "c",
            "environment": "sandbox",
            "exclude": exclude or {"kinds": ["D"], "sections": ["A5", "A6"]},
            "rounds": [
                {
                    "round": str(round_path.relative_to(tmp_path)),
                    "endpoint": "POST /qa/echo",
                    "matrix": "A2",
                    "auth": "none",
                }
            ],
        },
    )


def test_a_round_that_brings_back_an_excluded_kind_is_refused(tmp_path: Path):
    """The manifest states the campaign's scope once; a round cannot re-open it."""
    round_path = _tree(tmp_path, case={**_H01, "kind": "D"})
    campaign = load_campaign(_campaign(tmp_path, round_path))
    findings = validate_campaign_chain(campaign, tmp_path, PROJECT)
    assert "TRACK_MIX" in _codes(findings)


def test_an_excluded_section_is_refused_too(tmp_path: Path):
    round_path = _tree(tmp_path)
    campaign = load_campaign(
        _campaign(tmp_path, round_path, kinds=["D"], sections=["A2"])
    )
    findings = validate_campaign_chain(campaign, tmp_path, PROJECT)
    assert "TRACK_MIX" in _codes(findings)


def test_a_round_outside_the_exclusions_passes(tmp_path: Path):
    round_path = _tree(tmp_path)
    campaign = load_campaign(_campaign(tmp_path, round_path))
    assert "TRACK_MIX" not in _codes(
        validate_campaign_chain(campaign, tmp_path, PROJECT)
    )


# ── the catalog itself ─────────────────────────────────────────────────────────


def test_every_code_validate_can_emit_has_a_reason():
    """`--explain` is the index, and a code with no line is a code nobody can read."""
    assert explain().count("\n") == len(RULE_WHY) - 1
    for code, why in RULE_WHY.items():
        assert code in explain()
        assert why.strip()


def test_a_code_nobody_documented_fails_at_the_first_call():
    """The catalog is an invariant, not a suggestion: an unknown code raises."""
    with pytest.raises(KeyError):
        finding("NO_SUCH_RULE", "where", "message", fix="fix")


def test_the_cli_lists_the_rules_without_needing_a_round(tmp_path: Path):
    """`--explain` is cheap on purpose: it replaces reading the spec."""
    from heimdall_qa.cli import main

    assert main(["validate", "--explain"]) == 0


# ── the rules that left the vocabulary ─────────────────────────────────────────

SKILLS = Path(__file__).resolve().parents[1] / ".agents" / "skills"


def test_the_two_browser_rules_are_declaredly_out_of_the_core():
    """F6 §3.1's last two are about a surface the core no longer has.

    A `ui` step and a missing anchor are one fact here: a step kind that is not
    registered fails at load, naming the kind, before `validate` ever sees a round
    (`tests/test_step_kinds.py`). They are not codes because there is nothing left
    for a code to catch.
    """
    assert not [code for code in RULE_WHY if "ANCHOR" in code or code.startswith("UI")]


def test_the_five_rules_f6_deleted_did_not_come_back():
    """Two were already gone, one stays as a single line per skill, two are retired.

    Of the retired pair, one was factually wrong: the harness has no
    `register_gap_ms`. A skill that cites an attribute nobody implemented is how a
    rule becomes folklore, so the name is asserted absent.
    """
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SKILLS.rglob("SKILL.md"))
    )

    assert "register_gap_ms" not in text
    assert "criar o `.bru`" not in text
    assert "Fases 11" not in text
