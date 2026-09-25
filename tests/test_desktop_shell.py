"""The desktop shell: the surface it serves, and what closing the window does.

These are the two things about the shell that can be wrong without a GUI: which
renderer answers at `/` (and that the SPA mount does not shadow the API), and the
order the shutdown runs in. The window itself is the one part a suite cannot open, and
that is deliberate — a test that needs a display is a test that does not run in CI.

`_drain` is exercised directly rather than through `run_desktop`: it takes the two
objects the shutdown needs and nothing else, so the contract "cancel first, stop the
socket second" can be asserted without a window, a port or a thread.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from heimdall_qa.desktop import app as desktop
from heimdall_qa.errors import HarnessError
from heimdall_qa.serve import api
from heimdall_qa.serve.app import create_app
from heimdall_qa.session import RoundSession
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at
from heimdall_qa.workspace import WorkspaceSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"

_BUNDLE_INDEX = """<!doctype html>
<html><body><div id="root"></div>
<script type="module" src="/assets/index-abc.js"></script></body></html>
"""


def _handler() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "ACCEPTED"},
            headers={"X-Trace-Id": request.headers.get("x-trace-id", "")},
        )

    return handler


def _bundle(tmp_path: Path) -> Path:
    """A stand-in for the built SPA: an index and one asset, which is all it is."""
    webapp = tmp_path / "webapp"
    (webapp / "assets").mkdir(parents=True)
    (webapp / "index.html").write_text(_BUNDLE_INDEX, encoding="utf-8")
    (webapp / "assets" / "index-abc.js").write_text("export {};", encoding="utf-8")
    return webapp


def _session(tmp_path: Path) -> RoundSession:
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    return RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=config_for(
            project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))
        ),
        client=httpx.Client(transport=httpx.MockTransport(_handler()), timeout=10.0),
        runs_dir=tmp_path / "runs",
    )


# -- which renderer answers at `/` ----------------------------------------


def test_the_spa_is_served_at_the_root(tmp_path: Path):
    app = create_app(
        session=_session(tmp_path),
        api_token="shell-token",
        webapp=_bundle(tmp_path),
    )
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_the_spa_mount_does_not_shadow_the_api(tmp_path: Path):
    """A `Mount` at `/` swallows every path registered after it.

    This is the failure the mount order exists to prevent, and it is silent: the SPA
    would answer `POST /api/start` with an `index.html` and a 405, which reads as a
    client bug for an afternoon.
    """
    client = TestClient(
        create_app(
            session=_session(tmp_path),
            api_token="shell-token",
            webapp=_bundle(tmp_path),
        )
    )
    response = client.get("/api/bootstrap", headers={"X-Heimdall-Token": "shell-token"})
    assert response.status_code == 200
    assert response.json()["pane"] in {"start", "campaign", "review"}


def test_the_bundle_asset_is_served_under_assets(tmp_path: Path):
    """Vite writes `/assets/*`, and the SPA is dead without it."""
    client = TestClient(
        create_app(
            session=_session(tmp_path),
            api_token="shell-token",
            webapp=_bundle(tmp_path),
        )
    )
    response = client.get("/assets/index-abc.js")
    assert response.status_code == 200
    assert "export" in response.text


def test_the_api_still_demands_the_token_when_the_spa_is_serving(tmp_path: Path):
    """Serving the window does not make the caller the window."""
    client = TestClient(
        create_app(
            session=_session(tmp_path),
            api_token="shell-token",
            webapp=_bundle(tmp_path),
        )
    )
    response = client.post("/api/select", json={"key": "round:walk-hn"})
    assert response.status_code == 401


def test_healthz_answers_without_a_token(tmp_path: Path):
    """The shell waits on this, and it is a liveness answer rather than a read.

    It is also the regression test for the mount order: registered after
    `StaticFiles(directory=..., html=True)` is mounted at `/`, this path returns the
    bundle's 404 and the shell never sees readiness.
    """
    client = TestClient(
        create_app(
            session=_session(tmp_path),
            api_token="shell-token",
            webapp=_bundle(tmp_path),
        )
    )
    response = client.get("/healthz")
    assert response.status_code == 200
    assert json.loads(response.text) == {"ok": True}


def test_an_unbuilt_bundle_is_named_with_the_two_command_fix(tmp_path: Path):
    """The failure a source checkout hits, worded for the person who hits it.

    `StaticFiles` would raise about a missing directory and stop there; the fix is two
    `npm` commands in a workspace the reader may not know exists.
    """
    with pytest.raises(HarnessError) as raised:
        create_app(
            session=_session(tmp_path),
            api_token="shell-token",
            webapp=tmp_path / "never-built",
        )
    error = raised.value
    assert error.code == "WEBAPP_NOT_BUILT"
    assert "npm --prefix desktop/webapp" in error.hint


def test_the_retired_page_routes_are_gone(tmp_path: Path):
    """Phase 6 deleted the Jinja page, and this keeps it deleted.

    ADR-04's rule was "one renderer"; ADR-05 replaced the renderer rather than adding a
    second one, so `/panel`, `/round` and `/done` are not routes any more. `StaticFiles`
    answers them with a 404 because no such file exists in the bundle, which is what
    this asserts — the failure it guards against is a stray re-registration that would
    put a second, disagreeing screen back on the same state.
    """
    client = TestClient(
        create_app(
            session=_session(tmp_path),
            api_token="shell-token",
            webapp=_bundle(tmp_path),
        )
    )
    assert client.get("/panel").status_code == 404
    assert client.get("/round").status_code == 404
    assert client.get("/done").status_code == 404


# -- what closing the window does -----------------------------------------


class _RecordingServer:
    """A `_Server` that records the order, and the engine's state at that moment.

    Recording the state at `stop` is what lets the ordering be asserted without
    monkeypatching: if the socket is taken away before the plan is drained, the engine
    is still busy at this instant, and the field says so.
    """

    def __init__(self, order: list[str], workspace: object) -> None:
        self._order = order
        self._workspace = workspace
        self.busy_at_stop: bool | None = None

    def stop(self) -> None:
        self._order.append("stop")
        self.busy_at_stop = self._workspace.view().engine.busy


def test_drain_stops_the_server_when_nothing_is_running(tmp_path: Path):
    order: list[str] = []
    workspace = WorkspaceSession.wrap(_session(tmp_path))
    server = _RecordingServer(order, workspace)
    desktop._drain(workspace, server)
    assert order == ["stop"]
    assert server.busy_at_stop is False


def test_drain_cancels_the_plan_before_taking_the_socket_away(tmp_path: Path):
    """The whole contract: the engine closes the run, and only then the port goes.

    The plan is parked on a verdict, which is the state that stays put — a `walk` run
    cannot finish on its own, so the cancel is the only thing that can move it, and
    whatever the assertion reads is the drain's doing and not a race. A `SIGKILL`
    instead would leave the step in flight half written; cancelling settles between
    steps and leaves the same artifact a walk stopped by hand leaves.
    """
    order: list[str] = []
    workspace = WorkspaceSession.wrap(_session(tmp_path))
    workspace.start("round", "walk")
    workspace.wait_settled(timeout=30)
    assert workspace.view().engine.phase == "awaiting", "the run must be parked to test this"

    server = _RecordingServer(order, workspace)
    desktop._drain(workspace, server)

    assert server.busy_at_stop is False, "the socket went away while the engine was still running"
    assert workspace.view().engine.phase == "cancelled"
    assert order == ["stop"]


def test_drain_tolerates_a_shell_that_never_started(tmp_path: Path):
    """A failure before the window opens still closes the client it was built with.

    `_Server` may be None (the port never bound) and the workspace may be None (the
    session was never built). Neither is an error, and a socket that *was* opened is
    closed even when there is nothing to cancel.
    """
    stops: list[str] = []

    class _JustStops:
        def stop(self) -> None:
            stops.append("stop")

    desktop._drain(None, None)
    desktop._drain(None, _JustStops())
    assert stops == ["stop"]


# -- the entry point ------------------------------------------------------


def test_the_desktop_command_is_in_help_and_imports_no_gui():
    """`--help` must work on a machine with no GUI stack.

    The parser builds the subcommand without importing pywebview, and the assertion
    is on the module rather than on `sys.modules`: the point is that *this* module
    defers the import, not that nothing anywhere has ever loaded it.
    """
    from heimdall_qa import desktop as desktop_package
    from heimdall_qa.cli import build_parser

    args = build_parser().parse_args(["desktop", "--root", "."])
    assert args.command == "desktop"
    assert args.target is None
    assert desktop_package.__file__ is not None
    assert not hasattr(desktop, "webview")


def test_the_token_is_a_fresh_secret_per_shell():
    """Two windows must not share one: the token is the whole authorization."""
    assert api.new_token() != api.new_token()
    assert len(api.new_token()) >= 32
