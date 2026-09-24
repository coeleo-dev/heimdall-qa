from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from heimdall_qa.schema.models import LoopSpec
from heimdall_qa.schema.models import ProbeSpec
from heimdall_qa.schema.models import SurfaceSpec
from heimdall_qa.validate import validate_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"
H01_ROUND = FIXTURES / "rounds" / "h01-only.yaml"
INGEST_CASE = "cases/ingest.yaml#ingest-H01"


def _write_values_round(tmp_path: Path, *, times: int = 2, probe: dict | None = None) -> Path:
    for folder in ("cases", "contracts", "baselines", "suites"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "cases" / "ingest.yaml").write_text(
        (FIXTURES / "cases" / "ingest.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "contracts" / "api-ingest-post.yaml").write_text(
        (FIXTURES / "contracts" / "api-ingest-post.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "baselines" / "ingest-sum.json").write_text(
        (FIXTURES / "baselines" / "ingest-sum.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    surfaces = [
        {
            "id": "wallet",
            "get": "/api/users/{{external_user_id}}/balance",
            "jsonpath": "$.balance",
            "expect": "exact",
        }
    ]
    if probe is not None:
        surfaces_payload = probe
    else:
        surfaces_payload = {
            "id": "values-ingest",
            "oracle": {"catalog": "session", "ingest": "flat_session"},
            "surfaces": surfaces,
            "exclude": ["http_402"],
        }
    suite = {
        "id": "values-ingest",
        "catalog": {"pricing_model": "FLAT", "unit_amount": "0.00003"},
        "steps": [
            {
                "loop": {
                    "times": times,
                    "case": INGEST_CASE,
                    "generate": {"idempotency_key": "uuid_v4", "transaction_id": "uuid"},
                }
            },
            {"probe": surfaces_payload},
        ],
    }
    (tmp_path / "suites" / "values-ingest.yaml").write_text(
        yaml.safe_dump(suite, sort_keys=False),
        encoding="utf-8",
    )
    round_path = tmp_path / "round.yaml"
    round_path.write_text(
        "\n".join(
            [
                "id: values-ingest",
                "suite: suites/values-ingest.yaml",
                "mode: review",
                "environment: sandbox",
                "include:",
                f"  - {INGEST_CASE}",
            ]
        ),
        encoding="utf-8",
    )
    return round_path


def test_validate_h01_only_without_suite_steps_still_fails_coverage():
    errors = validate_round(H01_ROUND, FIXTURES)
    assert any("ingest-N-omit-timestamp" in error for error in errors)


def test_values_round_skips_expand_coverage(tmp_path: Path):
    round_path = _write_values_round(tmp_path)
    assert validate_round(round_path, tmp_path) == []


def test_loop_times_below_one_rejected():
    with pytest.raises(ValidationError):
        LoopSpec(times=0, case=INGEST_CASE)


def test_probe_without_surfaces_rejected():
    with pytest.raises(ValidationError):
        ProbeSpec(id="x", oracle={}, surfaces=[])


def test_empty_jsonpath_rejected():
    with pytest.raises(ValidationError):
        SurfaceSpec(id="wallet", get="/api/x", jsonpath="", expect="exact")


def test_expect_outside_enum_rejected():
    with pytest.raises(ValidationError):
        SurfaceSpec(id="wallet", get="/api/x", jsonpath="$.balance", expect="maybe")


def test_values_round_invalid_expect_in_yaml(tmp_path: Path):
    _write_values_round(
        tmp_path,
        probe={
            "id": "bad",
            "oracle": {},
            "surfaces": [
                {
                    "id": "wallet",
                    "get": "/api/x",
                    "jsonpath": "$.balance",
                    "expect": "maybe",
                }
            ],
        },
    )
    errors = validate_round(tmp_path / "round.yaml", tmp_path)
    assert errors
