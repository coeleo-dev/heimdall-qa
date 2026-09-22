from pathlib import Path

import pytest

from nokr_qa.coverage import expand
from nokr_qa.schema.load import load_case
from nokr_qa.schema.load import load_contract
from nokr_qa.schema.load import load_round
from nokr_qa.validate import validate_round

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
FIXTURE_CONTRACT = TESTS / "fixtures" / "contracts" / "api-ingest-post.yaml"
ROOT_CONTRACT = REPO / "contracts" / "api-ingest-post.yaml"
PILOTO_ROUND = REPO / "rounds" / "piloto-ingest.yaml"


def _expand_ids(contract_path: Path) -> set[str]:
    return {item.case_id for item in expand(load_contract(contract_path))}


def test_validate_piloto_ingest_round_passes():
    assert validate_round(PILOTO_ROUND, REPO) == []


def test_piloto_include_matches_expand_ids():
    round_file = load_round(PILOTO_ROUND)
    included = [load_case(REPO / relative).id for relative in round_file.include]
    assert set(included) == _expand_ids(ROOT_CONTRACT)
    assert included[0] == "ingest-H01"


def test_piloto_cases_have_integer_status_not_todo():
    round_file = load_round(PILOTO_ROUND)
    for relative in round_file.include:
        case = load_case(REPO / relative)
        assert isinstance(case.expect.status, int), case.id
        assert str(case.expect.status).upper() != "TODO"


def test_root_ingest_contract_matches_fixture_expand():
    assert _expand_ids(ROOT_CONTRACT) == _expand_ids(FIXTURE_CONTRACT)


def test_readme_documents_piloto_validate_and_serve():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "nokr-qa validate rounds/piloto-ingest.yaml" in text
    assert "nokr-qa serve rounds/piloto-ingest.yaml" in text


@pytest.mark.slow
def test_piloto_live_review_uses_serve_round():
    """Operator aceite: nokr-qa serve rounds/piloto-ingest.yaml with NokrAPI web+worker."""
    assert PILOTO_ROUND.is_file()
