from pathlib import Path

import httpx
import pytest

from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.errors import HarnessError
from nokr_qa.session import RoundSession
from nokr_qa.workspace import WorkspaceSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"
H01_ONLY = FIXTURES / "rounds" / "h01-only.yaml"


def _http() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"status": "ACCEPTED"})

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)


def _workspace(tmp_path: Path, *, focus: Path | None = None) -> WorkspaceSession:
    return WorkspaceSession(
        root=FIXTURES,
        config=HarnessConfig(
            log_files=LogFiles(
                web=str(tmp_path / "web.log"),
                worker=str(tmp_path / "worker.log"),
            )
        ),
        client=_http(),
        runs_dir=tmp_path / "runs",
        focus=focus,
    )


def test_workspace_indexes_fixtures_and_selects_focus(tmp_path: Path):
    workspace = _workspace(tmp_path, focus=WALK_HN)
    view = workspace.view()
    assert view.selected.path == "rounds/walk-hn.yaml"
    assert view.pane == "start"
    assert view.selected.startable is True


def test_not_ready_round_cannot_start(tmp_path: Path):
    workspace = _workspace(tmp_path, focus=H01_ONLY)
    with pytest.raises(HarnessError) as caught:
        workspace.start("walk")
    assert caught.value.code == "ROUND_INVALID"


def test_rerun_creates_a_new_run_directory(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    workspace = WorkspaceSession(
        root=FIXTURES,
        config=HarnessConfig(log_files=LogFiles(web=str(web), worker=str(worker))),
        client=_http(),
        runs_dir=tmp_path / "runs",
        focus=WALK_HN,
    )
    workspace.start("walk")
    first = workspace.view().session.run_dir
    workspace.apply_verdict("fail", "stop after happy path", False)
    workspace.start("walk")
    second = workspace.view().session.run_dir
    assert first is not None and second is not None
    assert first != second
    assert first.is_dir() and second.is_dir()


def test_select_case_after_round_done_opens_historical_step(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    workspace = WorkspaceSession(
        root=FIXTURES,
        config=HarnessConfig(log_files=LogFiles(web=str(web), worker=str(worker))),
        client=_http(),
        runs_dir=tmp_path / "runs",
        focus=WALK_HN,
    )
    workspace.start("walk")
    workspace.apply_verdict("pass", "", True)
    workspace.apply_verdict("fail", "stop after negative", False)

    round_view = workspace.view()
    assert round_view.session.phase == "done"
    assert round_view.pane == "done"

    case_view = workspace.select("case:rounds/walk-hn.yaml:note-H01")
    assert case_view.pane == "historical"
    assert case_view.historical_dir is not None
    assert (case_view.historical_dir / "request.json").is_file()
    assert case_view.session.current_step_dir == case_view.historical_dir
    assert case_view.historical_dir.name.endswith("note-H01")

    later = workspace.select("case:rounds/walk-hn.yaml:note-N-omit-note")
    assert later.pane == "historical"
    assert later.historical_dir is not None
    assert later.historical_dir.name.endswith("note-N-omit-note")

    back_to_round = workspace.select("round:rounds/walk-hn.yaml")
    assert back_to_round.pane == "done"


def test_select_case_without_live_session_uses_disk_run(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    first = WorkspaceSession(
        root=FIXTURES,
        config=HarnessConfig(log_files=LogFiles(web=str(web), worker=str(worker))),
        client=_http(),
        runs_dir=runs_dir,
        focus=WALK_HN,
    )
    first.start("walk")
    first.apply_verdict("fail", "stop after happy path", False)

    reopened = WorkspaceSession(
        root=FIXTURES,
        config=HarnessConfig(log_files=LogFiles(web=str(web), worker=str(worker))),
        client=_http(),
        runs_dir=runs_dir,
        focus=WALK_HN,
    )
    view = reopened.select("case:rounds/walk-hn.yaml:note-H01")
    assert view.pane == "historical"
    assert view.historical_dir is not None
    assert (view.historical_dir / "response.json").is_file()


def test_busy_round_blocks_starting_another(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    session = RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=HarnessConfig(log_files=LogFiles(web=str(web), worker=str(worker))),
        client=_http(),
        runs_dir=tmp_path / "runs",
    )
    workspace = WorkspaceSession.wrap(session)
    workspace.start("walk")
    workspace.select("round:rounds/example.yaml")
    with pytest.raises(HarnessError) as caught:
        workspace.start("walk")
    assert caught.value.code == "ROUND_BUSY"
