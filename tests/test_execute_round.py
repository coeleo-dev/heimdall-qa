import json
from pathlib import Path

import httpx
import pytest

from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.runner import execute_round
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_ROUND = FIXTURES / "rounds" / "example.yaml"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"


def _echo_handler(request: httpx.Request) -> httpx.Response:
    trace = request.headers.get("x-trace-id", "missing")
    return httpx.Response(
        202,
        json={"status": "ACCEPTED"},
        headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=10.0,
    )


def _project(tmp_path: Path) -> ProjectView:
    """The descriptor in force, with this test's own (empty) log files."""
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    return project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))


def _config(tmp_path: Path) -> HarnessConfig:
    return config_for(_project(tmp_path))


def test_execute_round_writes_summary_evidence_verdict_and_latest(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    run_dir = execute_round(
        EXAMPLE_ROUND,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_client(_echo_handler),
        runs_dir=runs_dir,
        mode="headless",
    )
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "counts" in summary
    assert "pass" in summary["counts"]
    assert "fail" in summary["counts"]
    assert "skip" in summary["counts"]
    assert "http_5xx" in summary["counts"]
    assert "instrument" in summary["counts"]
    assert summary["counts"]["instrument"] == 0
    assert "packs" in summary
    assert "coverage_pct" in summary
    assert "latency_ms" in summary
    assert "p50" in summary["latency_ms"]
    assert "p95" in summary["latency_ms"]
    assert "logs_incomplete" in summary
    assert summary["human_reject_rate"] == 0
    assert "review_duration_ms" in summary
    assert "oracle" in summary
    assert summary["oracle"] == "reference"
    evidence = (run_dir / "evidence.md").read_text(encoding="utf-8")
    assert "echo-H01" in evidence
    verdict = json.loads(
        (run_dir / "steps" / "001-echo-H01" / "verdict.json").read_text(encoding="utf-8")
    )
    assert verdict["actor"] == "auto"
    assert verdict["continue"] is True
    latest = runs_dir / "latest"
    assert latest.is_symlink()
    assert latest.resolve() == run_dir.resolve()
    assert not (run_dir / "analysis.md").exists()


def test_execute_round_review_mode_requires_ui(tmp_path: Path):
    with pytest.raises(HarnessError) as caught:
        execute_round(
            EXAMPLE_ROUND,
            root=FIXTURES,
            config=_config(tmp_path),
            client=_client(_echo_handler),
            runs_dir=tmp_path / "runs",
            mode="review",
        )
    assert caught.value.code == "MODE_REQUIRES_UI"


def test_execute_round_connect_error_finishes_with_fail_step(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    runs_dir = tmp_path / "runs"
    run_dir = execute_round(
        EXAMPLE_ROUND,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_client(handler),
        runs_dir=runs_dir,
        mode="headless",
    )
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["fail"] >= 1
    verdict = json.loads(
        (run_dir / "steps" / "001-echo-H01" / "verdict.json").read_text(encoding="utf-8")
    )
    assert verdict["status"] == "fail"
    assert verdict["actor"] == "auto"
    assert (runs_dir / "latest").is_symlink()


def test_execute_round_records_which_descriptor_was_in_force(tmp_path: Path):
    """Precedence with no record is indistinguishable from a mistake (ADR-01)."""
    runs_dir = tmp_path / "runs"
    run_dir = execute_round(
        EXAMPLE_ROUND,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_client(_echo_handler),
        runs_dir=runs_dir,
        mode="headless",
        descriptor={"origin": "target", "path": "qa/project.yaml"},
    )
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["descriptor"] == {"origin": "target", "path": "qa/project.yaml"}


def test_execute_round_without_a_descriptor_records_that_too(tmp_path: Path):
    """`null` beats a missing key: the reader can tell "none" from "not recorded"."""
    runs_dir = tmp_path / "runs"
    run_dir = execute_round(
        EXAMPLE_ROUND,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_client(_echo_handler),
        runs_dir=runs_dir,
        mode="headless",
    )
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "descriptor" in summary
    assert summary["descriptor"] is None
