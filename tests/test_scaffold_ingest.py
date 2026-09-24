import shutil
from pathlib import Path

import yaml

from heimdall_qa.scaffold import scaffold_endpoint
from heimdall_qa.testing import project_at
from heimdall_qa.validate import validate_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INGEST_CONTRACT = FIXTURES / "contracts" / "api-ingest-post.yaml"
#: Scaffolding reads the descriptor for the isolation cases it generates.
_PROJECT = project_at(FIXTURES / "qa" / "project.yaml")

REQUIRED_IDS = (
    "ingest-N-omit-timestamp",
    "ingest-N-omit-transaction_id",
    "ingest-N-pattern-event_type",
    "ingest-N-rule-UNKNOWN_EVENT_TYPE",
)


def test_scaffold_ingest_stubs_still_invalid(tmp_path: Path):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(INGEST_CONTRACT, contracts / "api-ingest-post.yaml")
    out = tmp_path / "cases"
    ids = scaffold_endpoint(
        contracts / "api-ingest-post.yaml",
        out,
        project=_PROJECT,
        contract_ref="contracts/api-ingest-post.yaml",
    )
    for required in REQUIRED_IDS:
        assert required in ids
    written = yaml.safe_load((out / "ingest.yaml").read_text(encoding="utf-8"))
    assert list(written) == list(ids)

    round_path = tmp_path / "round.yaml"
    round_path.write_text(
        "\n".join(
            [
                "id: ingest-scaffolded",
                "suite: unused.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                "  - cases/ingest.yaml",
            ]
        ),
        encoding="utf-8",
    )
    errors = validate_round(round_path, tmp_path)
    assert any("TODO" in error for error in errors)
    assert written["ingest-N-omit-timestamp"]["expect"] == {"status": 400}
    assert "ingest-N-rule-TIERED" not in ids
