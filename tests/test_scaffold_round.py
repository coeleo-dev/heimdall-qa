import shutil
from pathlib import Path

import yaml

from heimdall_qa.coverage import expand
from heimdall_qa.scaffold import scaffold_endpoint
from heimdall_qa.scaffold import scaffold_round
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.load import load_round
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INGEST = FIXTURES / "contracts" / "api-ingest-post.yaml"
ECHO = FIXTURES / "contracts" / "qa-echo-post.yaml"
NOTE = FIXTURES / "contracts" / "qa-note-post.yaml"
#: Scaffolding reads the descriptor for the isolation cases it generates.
_PROJECT = project_at(FIXTURES / "qa" / "project.yaml")


def _case(dest: Path, area: str, case_id: str) -> dict:
    """One case out of the area file the scaffold writes it into."""
    written = yaml.safe_load((dest / f"{area}.yaml").read_text(encoding="utf-8"))
    return written[case_id]


def _expect(dest: Path, area: str, case_id: str) -> dict:
    return _case(dest, area, case_id)["expect"]


def test_autofill_n_omit_is_400(tmp_path: Path):
    dest = tmp_path / "cases"
    shutil.copy(NOTE, tmp_path / "qa-note-post.yaml")
    scaffold_endpoint(tmp_path / "qa-note-post.yaml", dest, project=_PROJECT)
    assert _expect(dest, "note", "note-N-omit-note") == {"status": 400}
    assert _expect(dest, "note", "note-H01") == {"status": "TODO"}


def test_autofill_rule_422_and_no_tiered(tmp_path: Path):
    dest = tmp_path / "cases"
    ids = scaffold_endpoint(
        INGEST, dest, project=_PROJECT, contract_ref="contracts/api-ingest-post.yaml"
    )
    expect = _expect(dest, "ingest", "ingest-N-rule-UNKNOWN_EVENT_TYPE")
    assert expect["status"] == 422
    assert expect["code"] == "UNKNOWN_EVENT_TYPE"
    assert "ingest-N-rule-TIERED" not in ids
    written = yaml.safe_load((dest / "ingest.yaml").read_text(encoding="utf-8"))
    assert "ingest-N-rule-TIERED" not in written
    assert _expect(dest, "ingest", "ingest-N-omit-timestamp") == {"status": 400}
    assert _expect(dest, "ingest", "ingest-N-auth") == {"status": 401}



def test_scaffold_round_include_matches_expand(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(ECHO, contracts / "qa-echo-post.yaml")
    result = scaffold_round(
        contracts / "qa-echo-post.yaml",
        tmp_path / "rounds",
        project=_PROJECT,
        cases_dir=tmp_path / "cases",
        contract_ref="contracts/qa-echo-post.yaml",
        h01_status=202,
        root=tmp_path,
        round_id="echo-http",
    )
    contract = load_contract(contracts / "qa-echo-post.yaml")
    expected = [item.case_id for item in expand(contract)]
    assert result["case_ids"] == expected
    round_file = load_round(Path(result["round"]))
    #: One line, naming the area file: the round runs exactly the cases of the
    #: contract it was generated from, and listing them one by one is how a round
    #: ends up disagreeing with the file it includes.
    assert round_file.include == ["cases/echo.yaml"]
    assert list(_written(tmp_path / "cases" / "echo.yaml")) == expected
    h01 = load_case(tmp_path / "cases" / "echo.yaml", "echo-H01")
    assert h01.expect.status == 202


def test_scaffold_round_does_not_overwrite_without_force(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(NOTE, contracts / "qa-note-post.yaml")
    cases = tmp_path / "cases"
    (cases).mkdir()
    existing = cases / "note.yaml"
    existing.write_text(
        "\n".join(
            [
                "note-H01:",
                "  contract: contracts/qa-note-post.yaml",
                "  kind: H01",
                "  expect:",
                "    status: 201",
            ]
        ),
        encoding="utf-8",
    )
    scaffold_round(
        contracts / "qa-note-post.yaml",
        tmp_path / "rounds",
        cases_dir=cases,
        project=_PROJECT,
        contract_ref="contracts/qa-note-post.yaml",
        root=tmp_path,
    )
    assert load_case(existing, "note-H01").expect.status == 201
    omit = load_case(cases / "note.yaml", "note-N-omit-note")
    assert omit.expect.status == 400


def test_scaffold_round_writes_relative_contract_ref(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(NOTE, contracts / "qa-note-post.yaml")
    result = scaffold_round(
        contracts / "qa-note-post.yaml",
        tmp_path / "rounds",
        project=_PROJECT,
        cases_dir=tmp_path / "cases",
        h01_status=201,
        root=tmp_path,
        round_id="note-http",
    )
    h01 = load_case(tmp_path / "cases" / "note.yaml", "note-H01")
    assert h01.contract == "contracts/qa-note-post.yaml"
    assert not h01.contract.startswith("/")
    round_file = load_round(Path(result["round"]))
    assert all(item.startswith("cases/") for item in round_file.include)


def test_the_declared_area_names_the_file_and_the_round(tmp_path: Path):
    """`expand` names every case id after `contract.area`, so the file follows.

    Deriving it again from the endpoint put the cases in a file whose name
    disagreed with the case ids inside it, and named the round after a route the
    contract had already renamed.
    """
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    source = contracts / "qa-note-post.yaml"
    shutil.copy(NOTE, source)
    source.write_text(
        "area: notes\n" + source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = scaffold_round(
        source,
        tmp_path / "rounds",
        project=_PROJECT,
        root=tmp_path,
    )
    assert result["round_id"] == "notes"
    assert (tmp_path / "cases" / "notes.yaml").is_file()
    #: The file's name and the case ids inside it agree, which is the point.
    assert all(case_id.startswith("notes-") for case_id in _written(tmp_path / "cases" / "notes.yaml"))
    assert (tmp_path / "rounds" / "notes.yaml").is_file()


def test_a_contract_without_an_area_keeps_the_last_path_segment(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(NOTE, contracts / "qa-note-post.yaml")
    result = scaffold_round(
        contracts / "qa-note-post.yaml",
        tmp_path / "rounds",
        project=_PROJECT,
        root=tmp_path,
    )
    assert result["round_id"] == "note"
    assert (tmp_path / "cases" / "note.yaml").is_file()
    assert all(case_id.startswith("note-") for case_id in _written(tmp_path / "cases" / "note.yaml"))


def _written(path: Path) -> dict:
    """The case map a scaffolded area file holds, in file order."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))

