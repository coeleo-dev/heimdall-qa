from pathlib import Path
import shutil

from nokr_qa.scaffold import scaffold_endpoint
from nokr_qa.validate import validate_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INGEST_CONTRACT = FIXTURES / "contracts" / "api-ingest-post.yaml"

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
        contract_ref="contracts/api-ingest-post.yaml",
    )
    for required in REQUIRED_IDS:
        assert required in ids
        assert (out / f"{required}.yaml").is_file()

    includes = [f"cases/{case_id}.yaml" for case_id in ids]
    round_path = tmp_path / "round.yaml"
    include_yaml = "\n".join(f"  - {path}" for path in includes)
    round_path.write_text(
        "\n".join(
            [
                "id: ingest-scaffolded",
                "suite: unused.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                include_yaml,
            ]
        ),
        encoding="utf-8",
    )
    errors = validate_round(round_path, tmp_path)
    assert any("TODO" in error for error in errors)
    omit = out / "ingest-N-omit-timestamp.yaml"
    payload = omit.read_text(encoding="utf-8")
    assert "status: 400" in payload
    assert "ingest-N-rule-TIERED" not in ids
