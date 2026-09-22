from pathlib import Path
import shutil

import yaml

from heimdall_qa.coverage import expand
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.load import load_round
from heimdall_qa.scaffold import scaffold_endpoint
from heimdall_qa.scaffold import scaffold_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INGEST = FIXTURES / "contracts" / "api-ingest-post.yaml"
ECHO = FIXTURES / "contracts" / "qa-echo-post.yaml"
NOTE = FIXTURES / "contracts" / "qa-note-post.yaml"


def _expect(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["expect"]


def test_autofill_n_omit_is_400(tmp_path: Path):
    dest = tmp_path / "cases"
    shutil.copy(NOTE, tmp_path / "qa-note-post.yaml")
    scaffold_endpoint(tmp_path / "qa-note-post.yaml", dest)
    assert _expect(dest / "note-N-omit-note.yaml") == {"status": 400}
    assert _expect(dest / "note-H01.yaml") == {"status": "TODO"}


def test_autofill_rule_422_and_no_tiered(tmp_path: Path):
    dest = tmp_path / "cases"
    ids = scaffold_endpoint(INGEST, dest, contract_ref="contracts/api-ingest-post.yaml")
    expect = _expect(dest / "ingest-N-rule-UNKNOWN_EVENT_TYPE.yaml")
    assert expect["status"] == 422
    assert expect["code"] == "UNKNOWN_EVENT_TYPE"
    assert "ingest-N-rule-TIERED" not in ids
    assert not (dest / "ingest-N-rule-TIERED.yaml").exists()
    assert _expect(dest / "ingest-N-omit-timestamp.yaml") == {"status": 400}
    assert _expect(dest / "ingest-N-auth.yaml") == {"status": 401}


def test_scaffold_round_include_matches_expand(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(ECHO, contracts / "qa-echo-post.yaml")
    result = scaffold_round(
        contracts / "qa-echo-post.yaml",
        tmp_path / "rounds",
        cases_dir=tmp_path / "cases" / "echo",
        contract_ref="contracts/qa-echo-post.yaml",
        h01_status=202,
        root=tmp_path,
        round_id="echo-http",
    )
    contract = load_contract(contracts / "qa-echo-post.yaml")
    expected = [item.case_id for item in expand(contract)]
    assert result["case_ids"] == expected
    round_file = load_round(Path(result["round"]))
    assert [Path(item).name.removesuffix(".yaml") for item in round_file.include] == expected
    h01 = load_case(tmp_path / "cases" / "echo" / "echo-H01.yaml")
    assert h01.expect.status == 202


def test_scaffold_round_does_not_overwrite_without_force(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(NOTE, contracts / "qa-note-post.yaml")
    cases = tmp_path / "cases"
    (cases).mkdir()
    existing = cases / "note-H01.yaml"
    existing.write_text(
        "\n".join(
            [
                "id: note-H01",
                "contract: contracts/qa-note-post.yaml",
                "kind: H01",
                "expect:",
                "  status: 201",
            ]
        ),
        encoding="utf-8",
    )
    scaffold_round(
        contracts / "qa-note-post.yaml",
        tmp_path / "rounds",
        cases_dir=cases,
        contract_ref="contracts/qa-note-post.yaml",
        root=tmp_path,
    )
    assert load_case(existing).expect.status == 201
    omit = load_case(cases / "note-N-omit-note.yaml")
    assert omit.expect.status == 400


def test_scaffold_round_writes_relative_contract_ref(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(NOTE, contracts / "qa-note-post.yaml")
    result = scaffold_round(
        contracts / "qa-note-post.yaml",
        tmp_path / "rounds",
        cases_dir=tmp_path / "cases" / "note",
        h01_status=201,
        root=tmp_path,
        round_id="note-http",
    )
    h01 = load_case(tmp_path / "cases" / "note" / "note-H01.yaml")
    assert h01.contract == "contracts/qa-note-post.yaml"
    assert not h01.contract.startswith("/")
    round_file = load_round(Path(result["round"]))
    assert all(item.startswith("cases/") for item in round_file.include)
