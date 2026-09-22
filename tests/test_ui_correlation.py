"""Gate 2 of E2: the browser step correlates with the JVM logs by trace_id.

The point of the whole design is that `logs/collector.py` is reused untouched, so
these tests exercise the real collector through the step, not a stub.
"""

from pathlib import Path
import json

from support_ui import FakeUiSession
from support_ui import build_ui_tree
from support_ui import run_ui_round
from support_ui import ui_config
from support_ui import write_log_line


def _step_dir(run_dir: Path) -> Path:
    return run_dir / "steps" / "001-ui-overview"


def test_step_logs_carry_the_same_trace_id_as_the_browser(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = FakeUiSession(
        on_read=lambda trace_id: write_log_line(config.log_files.web, trace_id)
    )
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    ui = json.loads((_step_dir(run_dir) / "ui.json").read_text(encoding="utf-8"))
    trace_id = ui["trace_id"]
    assert trace_id == "nokrqa-ui-demo-1"

    lines = (_step_dir(run_dir) / "logs-web.txt").read_text(encoding="utf-8").splitlines()
    assert lines, "the step must keep the log lines for its own trace"
    assert all(f"trace_id: [{trace_id}]" in line for line in lines)

    packs = json.loads((_step_dir(run_dir) / "packs.json").read_text(encoding="utf-8"))
    results = {item["pack_id"]: item["status"] for item in packs["results"]}
    assert results["observability"] == "pass"
    assert results["http.success"] == "pass"
    assert packs["logs_incomplete"] is False


def test_worker_lines_are_collected_by_the_same_marker(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)

    def emit(trace_id: str) -> None:
        write_log_line(config.log_files.web, trace_id)
        write_log_line(config.log_files.worker, trace_id, message="consumer ack")

    run_dir, _ = run_ui_round(
        round_path, tmp_path, session=FakeUiSession(on_read=emit), config=config
    )

    worker = (_step_dir(run_dir) / "logs-worker.txt").read_text(encoding="utf-8")
    assert "consumer ack" in worker


def test_other_traces_never_leak_into_the_step_logs(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    write_log_line(config.log_files.web, "nokrqa-someone-else-7")

    run_dir, _ = run_ui_round(
        round_path,
        tmp_path,
        session=FakeUiSession(
            on_read=lambda trace_id: write_log_line(config.log_files.web, trace_id)
        ),
        config=config,
    )

    logs = (_step_dir(run_dir) / "logs-web.txt").read_text(encoding="utf-8")
    assert "someone-else" not in logs


def test_logs_are_reported_incomplete_when_the_trace_never_appears(tmp_path: Path):
    # `ui.logs_ms` is 0 here so the collector falls back immediately; a real run
    # waits `ui.logs_ms` first. Either way the step says so instead of pretending.
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path, logs_ms=0)
    run_dir, _ = run_ui_round(
        round_path, tmp_path, session=FakeUiSession(), config=config
    )

    packs = json.loads((_step_dir(run_dir) / "packs.json").read_text(encoding="utf-8"))
    assert packs["logs_incomplete"] is True
    assert packs["log_fallback"] == "window_plus_minus_1s"
    results = {item["pack_id"]: item["status"] for item in packs["results"]}
    assert results["observability"] == "fail"


def test_secrets_never_reach_the_artifacts(tmp_path: Path):
    token = "nk_test_leaked_in_body"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJxYSJ9.c2lnbmF0dXJl"
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = FakeUiSession(
        values={"keys.view_once.api_key": token},
        aria=f'- text: "{token}"\n- text: "{jwt}"',
    )
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    for name in ("ui.json", "ui-values.json", "aria.yml", "network.json", "console.log"):
        text = (_step_dir(run_dir) / name).read_text(encoding="utf-8")
        assert token not in text, f"{name} leaked the api key"
        assert jwt not in text, f"{name} leaked the JWT"
    assert "[REDACTED]" in (_step_dir(run_dir) / "ui-values.json").read_text(encoding="utf-8")
