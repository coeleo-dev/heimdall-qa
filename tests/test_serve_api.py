"""The `/api/*` surface: its contract, its token, and its stream.

The desktop client talks to nothing else, so these are written against the models
rather than against JSON strings on purpose: `BootstrapModel.model_validate` *is* the
assertion. If `panel.py` gains a key the contract does not know, or loses one it does,
the validation fails here and says so — which is the entire reason the contract exists
instead of a `dict[str, Any]`.
"""

import json
from pathlib import Path

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from heimdall_qa import keys
from heimdall_qa.projects import id_for
from heimdall_qa.serve import api
from heimdall_qa.serve.app import create_app
from heimdall_qa.serve.models import BootstrapModel
from heimdall_qa.serve.models import RunAggregateModel
from heimdall_qa.serve.models import StepModel
from heimdall_qa.serve.models import StreamModel
from heimdall_qa.session import RoundSession
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at
from heimdall_qa.testing import stub_client

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"

#: The id the workspace derives from the root it is handed, so a key built here and
#: a key off the tree are the same string.
PROJECT = id_for(FIXTURES)
WALK_HN_KEY = keys.for_round(PROJECT, "rounds/walk-hn.yaml")


def _handler() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "missing")
        body = json.loads(request.content.decode("utf-8") or "{}")
        if "note" in body:
            return httpx.Response(
                202,
                json={"status": "ACCEPTED"},
                headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
            )
        return httpx.Response(
            400,
            json={"error": "note is required", "traceId": trace},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    return handler


def _app(tmp_path: Path):
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    session = RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=config_for(
            project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))
        ),
        client=httpx.Client(transport=httpx.MockTransport(_handler()), timeout=10.0),
        runs_dir=tmp_path / "runs",
    )
    return create_app(
        session=session,
        api_token="test-token",
        webapp=stub_client(tmp_path),
    )


def _client(tmp_path: Path) -> TestClient:
    return TestClient(_app(tmp_path), follow_redirects=False)


def _headers() -> dict[str, str]:
    return {"X-Heimdall-Token": "test-token"}


def _busy(response: httpx.Response) -> None:
    assert response.status_code == 200, response.text


# -- the contract ---------------------------------------------------------


def test_bootstrap_validates_against_the_model(tmp_path: Path):
    """Every field the client is promised, from one read, typed."""
    client = _client(tmp_path)
    response = client.get("/api/bootstrap", headers=_headers())
    _busy(response)
    model = BootstrapModel.model_validate(response.json())
    assert model.tree, "the collection has rounds in the fixture"
    assert model.selected, "something is always selected"
    assert model.engine.phase == "idle"
    assert model.unit.key == model.selected
    assert model.labels.scope_label["round"] == "Rodar este endpoint"
    # The start pane has no step and no aggregate, and says so with `None` rather
    # than an empty object a client would have to guess about.
    assert model.step is None
    assert model.run is None


def test_the_contract_refuses_a_field_it_was_not_taught(tmp_path: Path):
    """`extra="forbid"` is the teeth: `panel.py` gaining a key is a change the client
    has to be told about, and a model that shrugged would let the two drift silently.
    """
    from pydantic import ValidationError

    from heimdall_qa.serve.models import UnitCardModel

    with pytest.raises(ValidationError):
        UnitCardModel(
            key=WALK_HN_KEY,
            kind="round",
            label="note",
            status="pending",
            invented_field="not in the contract",
        )


# -- the token and the origin --------------------------------------------


def test_api_without_a_token_is_401(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/api/bootstrap")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_api_with_the_wrong_token_is_401(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/api/bootstrap", headers={"X-Heimdall-Token": "nope"})
    assert response.status_code == 401


def test_a_foreign_origin_is_403_even_with_the_token(tmp_path: Path):
    """The token is not enough on its own, and that is deliberate.

    A page on another host cannot read this process's token, but it can be used as a
    confused deputy for a request that would carry it. The `Origin` check is what
    makes that useless.
    """
    client = _client(tmp_path)
    response = client.get(
        "/api/bootstrap",
        headers={**_headers(), "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ORIGIN"


def test_a_loopback_origin_is_allowed(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get(
        "/api/bootstrap",
        headers={**_headers(), "Origin": "http://127.0.0.1:8731"},
    )
    _busy(response)


def test_a_null_origin_is_refused(tmp_path: Path):
    """`file://` and sandboxed frames are `Origin: null`, and none of them belong."""
    client = _client(tmp_path)
    response = client.get(
        "/api/bootstrap",
        headers={**_headers(), "Origin": "null"},
    )
    assert response.status_code == 403


# -- mutation -------------------------------------------------------------


def test_select_answers_with_the_new_screen(tmp_path: Path):
    """A mutation returns the whole bootstrap, so the client never needs a second
    round trip to draw the result of its own action.
    """
    client = _client(tmp_path)
    key = WALK_HN_KEY
    response = client.post("/api/select", json={"key": key}, headers=_headers())
    _busy(response)
    model = BootstrapModel.model_validate(response.json())
    assert model.selected == key
    assert model.unit.label == "walk-hn"
    assert model.unit.scopes == ["round"]


def test_select_of_an_unknown_node_is_a_400_envelope(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/select", json={"key": "round:nope.yaml"}, headers=_headers()
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "ROUND_INVALID"
    assert payload["error"]["hint"]
    assert payload["comment_required"] is False


def test_start_and_verdict_drive_a_run_through_the_api(tmp_path: Path):
    client = _client(tmp_path)
    _busy(client.post("/api/start", json={"mode": "walk"}, headers=_headers()))
    client.app.state.workspace.wait_settled(timeout=30)

    step = client.get("/api/step", headers=_headers())
    _busy(step)
    model = StepModel.model_validate(step.json())
    assert model.awaiting_verdict is True
    assert model.http_status == 202

    response = client.post(
        "/api/verdict",
        json={"status": "pass", "comment": "", "continue_round": True},
        headers=_headers(),
    )
    _busy(response)
    client.app.state.workspace.wait_settled(timeout=30)
    assert BootstrapModel.model_validate(response.json()).pane in {"review", "done"}


def test_a_reprove_without_a_comment_says_comment_required(tmp_path: Path):
    """The one error the client must not just display.

    `comment_required` is a field and not a substring of a message, so the form can
    keep what the reviewer wrote instead of clearing it. That distinction used to be
    a `"comment" in message` check in `app.py`; the API makes it explicit.
    """
    client = _client(tmp_path)
    _busy(client.post("/api/start", json={"mode": "walk"}, headers=_headers()))
    client.app.state.workspace.wait_settled(timeout=30)
    response = client.post(
        "/api/verdict",
        json={"status": "fail", "comment": "", "continue_round": True},
        headers=_headers(),
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "VERDICT_INVALID"
    assert payload["comment_required"] is True


def test_a_second_start_while_busy_is_409(tmp_path: Path):
    """`ROUND_BUSY` keeps its own status code, because the client has a case for it:
    "a run is already going" is not a validation error, it is a wait.
    """
    client = _client(tmp_path)
    _busy(
        client.post(
            "/api/start", json={"mode": "walk"}, headers=_headers()
        )
    )
    second = client.post("/api/start", json={"mode": "walk"}, headers=_headers())
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ROUND_BUSY"
    client.app.state.workspace.cancel()
    client.app.state.workspace.wait_settled(timeout=30)


def test_the_step_endpoint_is_404_when_no_step_is_on_screen(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/api/step", headers=_headers())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_run_endpoint_answers_the_aggregate_at_the_end(tmp_path: Path):
    client = _client(tmp_path)
    before = client.get("/api/run", headers=_headers())
    assert before.status_code == 404

    _busy(client.post("/api/start", json={"mode": "walk"}, headers=_headers()))
    client.app.state.workspace.wait_settled(timeout=30)
    _busy(
        client.post(
            "/api/verdict",
            json={"status": "fail_stop", "comment": "stop here", "continue_round": True},
            headers=_headers(),
        )
    )
    client.app.state.workspace.wait_settled(timeout=30)
    response = client.get("/api/run", headers=_headers())
    _busy(response)
    model = RunAggregateModel.model_validate(response.json())
    assert model.run_path
    assert isinstance(model.summary, dict)


# -- the stream -----------------------------------------------------------


def test_the_stream_opens_with_the_current_engine(tmp_path: Path):
    """The first frame is sent at once, so the client is never blank on connect.

    Driven directly rather than over a socket: `TestClient` cannot close a streaming
    body from the client side, so a test that reads a live SSE response hangs on a
    correct implementation. `event_frames` takes its own `is_disconnected`, which is
    the seam that makes this a test instead of a timeout.
    """
    app = _app(tmp_path)
    workspace = app.state.workspace

    async def run() -> list[str]:
        frames: list[str] = []
        stop = {"now": False}

        async def is_disconnected() -> bool:
            return stop["now"]

        async for frame in api.event_frames(workspace, is_disconnected):
            frames.append(frame)
            stop["now"] = True
        return frames

    frames = anyio.run(run)
    assert frames == [frames[0]]
    assert frames[0].startswith("event: state\n")
    payload = json.loads(frames[0].split("data: ", 1)[1].strip())
    model = StreamModel.model_validate(payload)
    assert model.engine.phase == "idle"
    assert model.pane == "start"
    assert model.selected


def test_the_stream_pushes_the_revision_that_moved(tmp_path: Path):
    """A change while the stream is open arrives as a frame, not as a poll result.

    The engine's revision is what the loop blocks on, so this is also the test that
    the dirty flag fires for the changes a reviewer cares about: a plan starting, and
    a step settling.
    """
    app = _app(tmp_path)
    workspace = app.state.workspace

    async def run() -> list[StreamModel]:
        seen: list[StreamModel] = []
        stop = {"now": False}

        async def is_disconnected() -> bool:
            return stop["now"]

        async for frame in api.event_frames(workspace, is_disconnected):
            if frame.startswith(":"):
                continue
            seen.append(
                StreamModel.model_validate(json.loads(frame.split("data: ", 1)[1]))
            )
            if len(seen) == 1:
                # The first frame is the idle snapshot; start a plan while the stream
                # is open and the next frame has to be a different revision.
                workspace.start("round", "walk", None)
            elif len(seen) == 2:
                stop["now"] = True
        return seen

    frames = anyio.run(run)
    assert len(frames) == 2
    assert frames[1].engine.revision > frames[0].engine.revision
    assert frames[1].engine.phase in {"running", "awaiting"}
    workspace.wait_settled(timeout=30)


def test_the_events_route_is_registered_as_a_get(tmp_path: Path):
    """The transport the client opens, spelled out. Cheap, and it catches a route
    renamed while the client was not."""
    app = _app(tmp_path)
    routes = {
        (route.path, tuple(sorted(route.methods)))
        for route in app.routes
        if getattr(route, "methods", None)
    }
    assert ("/api/events", ("GET",)) in routes


def test_the_revision_moves_when_a_plan_starts(tmp_path: Path):
    """The dirty flag the stream exists to carry. Without it the stream would have to
    poll, which is the cost the SSE transport replaces."""
    client = _client(tmp_path)
    before = client.app.state.workspace.engine_revision()
    _busy(client.post("/api/start", json={"mode": "walk"}, headers=_headers()))
    client.app.state.workspace.wait_settled(timeout=30)
    after = client.app.state.workspace.engine_revision()
    assert after > before

    # And it settles: an idle engine stops moving, which is what lets the stream block
    # instead of spin. `elapsed_ms` changes on every read and must not count.
    settled = client.app.state.workspace.engine_revision()
    client.app.state.workspace.wait_change(settled, timeout=0.2)
    assert client.app.state.workspace.engine_revision() == settled
    client.app.state.workspace.cancel()
    client.app.state.workspace.wait_settled(timeout=30)
