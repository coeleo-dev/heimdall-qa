import json
from pathlib import Path

from heimdall_qa.campaign import campaign_status
from heimdall_qa.campaign import find_latest_run
from heimdall_qa.campaign import validate_campaign
from heimdall_qa.cli import main
from heimdall_qa.schema.load import load_campaign

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OK = FIXTURES / "campaigns" / "ok.yaml"
MISSING = FIXTURES / "campaigns" / "missing-round.yaml"


def test_validate_ok_campaign_passes():
    assert validate_campaign(OK, FIXTURES) == []


def test_validate_missing_round_names_path():
    errors = validate_campaign(MISSING, FIXTURES)
    assert errors
    assert any("rounds/does-not-exist.yaml" in error for error in errors)


def test_validate_invalid_manifest(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("id: 1\n", encoding="utf-8")
    errors = validate_campaign(path, tmp_path)
    assert errors
    assert str(path) in errors[0]


def test_campaign_status_fake_runs(tmp_path: Path):
    runs = tmp_path / "runs"
    stamp = "2026-09-01T1200"
    run_dir = runs / f"{stamp}-example"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "round_id": "example",
                "counts": {"pass": 1, "fail": 0, "skip": 0, "http_5xx": 0},
            }
        ),
        encoding="utf-8",
    )
    older = runs / "2026-08-01T0100-example"
    older.mkdir()
    (older / "summary.json").write_text(
        json.dumps({"counts": {"pass": 0, "fail": 9, "skip": 0, "http_5xx": 2}}),
        encoding="utf-8",
    )
    payload = campaign_status(OK, FIXTURES, runs)
    assert payload["id"] == "example-campaign"
    assert len(payload["rounds"]) == 1
    row = payload["rounds"][0]
    assert row["status"] == "pass"
    assert row["round_id"] == "example"
    assert row["counts"]["http_5xx"] == 0
    assert find_latest_run(runs, "example") == run_dir


def test_find_latest_run_disambiguates_same_minute(tmp_path: Path):
    runs = tmp_path / "runs"
    first = runs / "2026-09-01T2205-example"
    second = runs / "2026-09-01T2205-example~2"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert find_latest_run(runs, "example") == second


def test_campaign_status_not_reviewed(tmp_path: Path):
    payload = campaign_status(OK, FIXTURES, tmp_path / "runs")
    assert payload["rounds"][0]["status"] == "not_reviewed"


def test_campaign_status_http_5xx(tmp_path: Path):
    runs = tmp_path / "runs"
    run_dir = runs / "2026-09-01T1500-example"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"counts": {"pass": 0, "fail": 0, "skip": 0, "http_5xx": 1}}),
        encoding="utf-8",
    )
    payload = campaign_status(OK, FIXTURES, runs)
    assert payload["rounds"][0]["status"] == "http_5xx"


def test_cli_campaign_validate_missing(capsys):
    code = main(["campaign", "validate", str(MISSING), "--root", str(FIXTURES)])
    captured = capsys.readouterr()
    assert code == 1
    assert "rounds/does-not-exist.yaml" in captured.err


def test_cli_campaign_status_json(tmp_path: Path, capsys):
    code = main(
        [
            "campaign",
            "status",
            str(OK),
            "--root",
            str(FIXTURES),
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["rounds"][0]["status"] == "not_reviewed"


def test_fixture_ok_campaign_excludes_d():
    campaign = load_campaign(OK)
    assert "D" in campaign.exclude.kinds


def test_a_round_without_a_dto_reports_no_dto_key(tmp_path: Path):
    """`dto` is provenance, so a target with none omits it instead of reporting null."""
    campaign = tmp_path / "campaign.yaml"
    campaign.write_text(
        "\n".join(
            [
                "id: demo",
                "environment: sandbox",
                "rounds:",
                "  - round: rounds/missing.yaml",
                "    endpoint: POST /qa/echo",
                "    matrix: A4",
            ]
        ),
        encoding="utf-8",
    )
    row = campaign_status(campaign, tmp_path, tmp_path / "runs")["rounds"][0]
    assert "dto" not in row
    assert row["endpoint"] == "POST /qa/echo"


def test_a_round_with_a_dto_still_reports_it(tmp_path: Path):
    campaign = tmp_path / "campaign.yaml"
    campaign.write_text(
        "\n".join(
            [
                "id: demo",
                "environment: sandbox",
                "rounds:",
                "  - round: rounds/missing.yaml",
                "    endpoint: POST /qa/echo",
                "    dto: com.example.qa.EchoRequest",
                "    matrix: A4",
            ]
        ),
        encoding="utf-8",
    )
    row = campaign_status(campaign, tmp_path, tmp_path / "runs")["rounds"][0]
    assert row["dto"] == "com.example.qa.EchoRequest"
