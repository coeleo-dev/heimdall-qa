import json
from pathlib import Path

import httpx
import pytest

from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.errors import HarnessError
from nokr_qa.session import RoundSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"


def _handler(web_log: Path, *, log_h01: bool, log_n: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "missing")
        body = json.loads(request.content.decode("utf-8") or "{}")
        if "note" in body:
            if log_h01:
                _append_trace(web_log, trace)
            return httpx.Response(
                202,
                json={"status": "ACCEPTED"},
                headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
            )
        if log_n:
            _append_trace(web_log, trace)
        return httpx.Response(
            400,
            json={"error": "note is required", "traceId": trace},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    return handler


def _append_trace(web_log: Path, trace: str) -> None:
    with web_log.open("a", encoding="utf-8") as handle:
        handle.write(
            "2026-09-01 16:00:00 [vt] INFO  c.n.Qa [SANDBOX] - "
            f"trace_id: [{trace}] - accepted\n"
        )


def _session(tmp_path: Path, handler) -> RoundSession:
    web_log = tmp_path / "nokr-web.log"
    worker_log = tmp_path / "nokr-worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    return RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=HarnessConfig(
            log_files=LogFiles(web=str(web_log), worker=str(worker_log)),
        ),
        client=client,
        runs_dir=tmp_path / "runs",
    )


def test_walk_pauses_on_first_step_without_human_verdict(tmp_path: Path):
    web_log = tmp_path / "nokr-web.log"
    web_log.write_text("", encoding="utf-8")
    session = _session(tmp_path, _handler(web_log, log_h01=True, log_n=False))
    view = session.start("walk")
    assert view.phase == "step"
    assert view.current_step_dir is not None
    assert view.current_step_dir.name.endswith("note-H01")
    assert not (view.current_step_dir / "verdict.json").exists()
    assert view.queue[0].current is True
    assert view.queue[1].current is False
    assert view.environment == "sandbox"
    assert view.progress == (1, 2)
    assert view.can_prev is False
    assert view.can_next is False
    assert view.awaiting_verdict is True


def test_review_auto_advances_passing_h01_and_pauses_on_n(tmp_path: Path):
    web_log = tmp_path / "nokr-web.log"
    web_log.write_text("", encoding="utf-8")
    session = _session(tmp_path, _handler(web_log, log_h01=True, log_n=False))
    view = session.start("review")
    assert view.phase == "step"
    assert view.current_step_dir is not None
    assert view.current_step_dir.name.endswith("note-N-omit-note")
    h01 = next(view.run_dir.glob("steps/*-note-H01"))
    verdict = json.loads((h01 / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["actor"] == "auto"
    assert verdict["status"] == "pass"


def test_apply_verdict_fail_requires_comment(tmp_path: Path):
    web_log = tmp_path / "nokr-web.log"
    web_log.write_text("", encoding="utf-8")
    session = _session(tmp_path, _handler(web_log, log_h01=True, log_n=False))
    session.start("walk")
    with pytest.raises(HarnessError) as caught:
        session.apply_verdict("fail", "", True)
    assert caught.value.code == "VERDICT_INVALID"
    assert session.view().phase == "step"
    assert not (session.view().current_step_dir / "verdict.json").exists()


def test_apply_verdict_stop_finishes_run(tmp_path: Path):
    web_log = tmp_path / "nokr-web.log"
    web_log.write_text("", encoding="utf-8")
    session = _session(tmp_path, _handler(web_log, log_h01=True, log_n=False))
    session.start("walk")
    view = session.apply_verdict("fail", "message is unclear", False)
    assert view.phase == "done"
    assert view.run_dir is not None
    summary = json.loads((view.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["fail"] >= 1
    assert summary["human_reject_rate"] == 1.0
    latest = tmp_path / "runs" / "latest"
    assert latest.is_symlink()
    assert latest.resolve() == view.run_dir.resolve()


def test_session_headless_mode_requires_ui(tmp_path: Path):
    web_log = tmp_path / "nokr-web.log"
    web_log.write_text("", encoding="utf-8")
    session = _session(tmp_path, _handler(web_log, log_h01=True, log_n=False))
    with pytest.raises(HarnessError) as caught:
        session.start("headless")
    assert caught.value.code == "MODE_REQUIRES_UI"
