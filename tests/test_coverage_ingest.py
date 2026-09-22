from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError

from heimdall_qa.coverage import expand
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import Waive
from heimdall_qa.validate import validate_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INGEST_CONTRACT = FIXTURES / "contracts" / "api-ingest-post.yaml"
OPTIONAL_CONTRACT = FIXTURES / "contracts" / "with-optional.yaml"
H01_ROUND = FIXTURES / "rounds" / "h01-only.yaml"
ECHO_CONTRACT = FIXTURES / "contracts" / "qa-echo-post.yaml"
EXAMPLE_ROUND = FIXTURES / "rounds" / "example.yaml"
NOTE_CONTRACT = FIXTURES / "contracts" / "qa-note-post.yaml"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"


def _case_ids(contract_path: Path) -> set[str]:
    return {item.case_id for item in expand(load_contract(contract_path))}


def test_expand_ingest_includes_required_kinds():
    ids = _case_ids(INGEST_CONTRACT)
    assert "ingest-H01" in ids
    assert "ingest-N-omit-timestamp" in ids
    assert "ingest-N-omit-transaction_id" in ids
    assert "ingest-N-pattern-event_type" in ids
    assert "ingest-N-rule-UNKNOWN_EVENT_TYPE" in ids
    assert not any(case_id.startswith("ingest-O-omit-") for case_id in ids)


def test_expand_get_jwt_includes_e_isolate():
    kinds = {item.kind for item in expand(_get_contract(auth="jwt"))}
    assert "E-isolate" in kinds
    assert "E-conflict" not in kinds


def test_expand_get_api_key_skips_e_isolate():
    kinds = {item.kind for item in expand(_get_contract(auth="api_key"))}
    assert "E-isolate" not in kinds
    assert "E-conflict" not in kinds


def test_expand_get_api_key_pgap6_includes_e_conflict():
    kinds = {
        item.kind
        for item in expand(_get_contract(auth="api_key", p_gaps=["P-GAP-6"]))
    }
    assert "E-conflict" in kinds
    assert "E-isolate" not in kinds


def _get_contract(*, auth: str, p_gaps: list[str] | None = None) -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "GET /api/users/{{external_user_id}}",
            "dto": "com.nokr.domain.user.controller.UserController",
            "auth": auth,
            "baseline": "baselines/empty.json",
            "fields": {},
            "p_gaps": p_gaps or [],
        }
    )


def test_optional_field_requires_omit_and_set():
    ids = _case_ids(OPTIONAL_CONTRACT)
    assert "ingest-O-omit-note" in ids
    assert "ingest-O-set-note" in ids


def test_waive_short_reason_without_pgap_rejected():
    with pytest.raises(ValidationError):
        Waive(reason="too short")
    long_reason = "POST /platform/api-keys is view-once; playbook A1 skips the header"
    assert len(long_reason) >= 40
    Waive(reason=long_reason)
    Waive(reason="short", p_gap="P-GAP-1")


def test_expand_echo_is_only_h01():
    ids = _case_ids(ECHO_CONTRACT)
    assert ids == {"echo-H01"}


def test_expand_note_is_h01_and_n_omit():
    ids = _case_ids(NOTE_CONTRACT)
    assert ids == {"note-H01", "note-N-omit-note"}


def test_validate_walk_hn_round_passes():
    assert validate_round(WALK_HN, FIXTURES) == []


def test_validate_example_round_passes():
    assert validate_round(EXAMPLE_ROUND, FIXTURES) == []


def test_validate_h01_only_round_fails():
    errors = validate_round(H01_ROUND, FIXTURES)
    joined = "\n".join(errors)
    assert errors
    assert "ingest-N-omit-timestamp" in joined


def test_stub_status_todo_is_invalid(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(INGEST_CONTRACT, contracts / "api-ingest-post.yaml")
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    (case_dir / "ingest-H01.yaml").write_text(
        "\n".join(
            [
                "id: ingest-H01",
                "contract: contracts/api-ingest-post.yaml",
                "kind: H01",
                "expect:",
                "  status: TODO",
            ]
        ),
        encoding="utf-8",
    )
    round_path = tmp_path / "round.yaml"
    round_path.write_text(
        "\n".join(
            [
                "id: todo-h01",
                "suite: unused.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                "  - cases/ingest-H01.yaml",
            ]
        ),
        encoding="utf-8",
    )
    errors = validate_round(round_path, tmp_path)
    assert any("TODO" in error for error in errors)


def test_validate_rule_without_code_or_error_is_incomplete(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "note.yaml").write_text(
        "\n".join(
            [
                "endpoint: POST /qa/note",
                "dto: com.nokr.qa.NoteRequest",
                "auth: none",
                "baseline: baselines/note.json",
                "fields:",
                "  note:",
                "    required: true",
                "    json: note",
                "rules:",
                "  - id: DUPLICATE",
                "    status: 409",
            ]
        ),
        encoding="utf-8",
    )
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "note-H01.yaml").write_text(
        "\n".join(
            [
                "id: note-H01",
                "contract: contracts/note.yaml",
                "kind: H01",
                "expect:",
                "  status: 201",
            ]
        ),
        encoding="utf-8",
    )
    (cases / "note-N-omit-note.yaml").write_text(
        "\n".join(
            [
                "id: note-N-omit-note",
                "contract: contracts/note.yaml",
                "kind: N-omit-note",
                "expect:",
                "  status: 400",
            ]
        ),
        encoding="utf-8",
    )
    (cases / "note-N-rule-DUPLICATE.yaml").write_text(
        "\n".join(
            [
                "id: note-N-rule-DUPLICATE",
                "contract: contracts/note.yaml",
                "kind: N-rule-DUPLICATE",
                "expect:",
                "  status: 409",
            ]
        ),
        encoding="utf-8",
    )
    round_path = tmp_path / "round.yaml"
    round_path.write_text(
        "\n".join(
            [
                "id: note-http",
                "suite: unused.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                "  - cases/note-H01.yaml",
                "  - cases/note-N-omit-note.yaml",
                "  - cases/note-N-rule-DUPLICATE.yaml",
            ]
        ),
        encoding="utf-8",
    )
    errors = validate_round(round_path, tmp_path)
    joined = "\n".join(errors)
    assert errors
    assert "DUPLICATE" in joined
    assert "code" in joined or "error" in joined
