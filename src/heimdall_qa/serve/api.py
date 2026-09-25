"""The JSON and event-stream API the desktop client talks to.

The client is a webview with a bundle in it, and every fact it draws — the collection,
the run, the step in front of it — arrives through here. The Jinja screens that used to
render the same panels as HTML were retired with phase 6 of `contrib/architecture.md`
§9.5, and `contract.py` is now the one translation of `panel.py` that remains: a
divergence between what the harness knows and what the window draws fails validation
here instead of becoming two screens that disagree.

**Why every route checks a token.** A mutation API on loopback is reachable by any
page the host's browser loads — the browser is happy to `POST` to `127.0.0.1` from
`https://evil.example`. The HTML forms escaped this because a cross-origin form post
carries the browser's `Origin`, and because the worst it could do was start a run that
a reviewer would see. A JSON surface has no such accident: it is explicit about
content type, it can be read back, and "start a campaign" is a side effect worth
forging. So a per-run token is required, and `Origin` is checked as well — the token
alone would be enough for a caller that already has it, and the `Origin` check closes
the case where a browser is tricked into carrying one.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic import ConfigDict
from starlette.concurrency import run_in_threadpool

from heimdall_qa import operations
from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import to_dict
from heimdall_qa.projects import ProjectRef
from heimdall_qa.projects import ProjectsRegistry
from heimdall_qa.projects import id_for
from heimdall_qa.serve import contract
from heimdall_qa.serve.models import ApiErrorModel
from heimdall_qa.serve.models import HarnessErrorModel
from heimdall_qa.serve.models import StreamModel
from heimdall_qa.workspace import WorkspaceSession

#: The header the client sends the token in. A header and not a query parameter: a
#: token in a URL ends up in the process list, in a log line, and in `Referer`, and a
#: per-run secret has no business being anywhere a password would not.
TOKEN_HEADER = "x-heimdall-token"

#: How long the stream blocks on the engine before looking around. A short slice so
#: that a client hanging up is noticed inside a second rather than after a long idle
#: wait — `run_in_threadpool` cannot abandon a blocked thread, so the wait has to be
#: short enough that waiting for it is not the cost.
_SLICE_SECONDS = 2.0

#: How much idle time accumulates before a keep-alive comment. A proxy or a sleeping
#: laptop drops an idle connection; a comment line is the cheapest thing that keeps it
#: open without pretending something happened.
_HEARTBEAT_SECONDS = 15.0

#: Hosts an `Origin` may name and still be believed. Everything the desktop shell and
#: a local test can legitimately be.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: Status codes for the refusals the client has cases for. Everything else is a 400:
#: the harness's errors are about the request, not about the server being unwell.
_STATUS_BY_CODE = {
    "ROUND_BUSY": 409,
    "NOT_FOUND": 404,
    # A path that resolves outside the content root is refused the way a forbidden
    # origin is: the request asked for something it may not have, and 403 is the word
    # for that rather than "your YAML was malformed".
    "SOURCE_OUTSIDE_CONTENT": 403,
    # Structural conflicts: there is no project open to act on, or the close would
    # leave none. Both mean "the state, not the request, is what refuses you".
    "PROJECT_EMPTY": 409,
    "PROJECT_UNKNOWN": 404,
    # The registry is the client's own memory, so a registry the harness cannot read
    # or write is the server being unwell rather than the request being wrong.
    "REGISTRY_INVALID": 500,
    "REGISTRY_UNWRITABLE": 500,
}


class SelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = ""
    mode: str = "review"
    node: str | None = None


class VerdictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    comment: str = ""
    continue_round: bool = True


class FocusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int


class SourceRequest(BaseModel):
    """Which file the editor wants. The path is content-root-relative, never absolute."""

    model_config = ConfigDict(extra="forbid")

    path: str


class SaveSourceRequest(BaseModel):
    """A file's whole text. Whole and not a patch: the client holds the document.

    A diff-based save would have to be applied by the server, and two editors applying
    patches to the same file is a merge problem this harness has no business growing.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    text: str


class FolderRequest(BaseModel):
    """A folder to create under `campaigns/`, relative to the project's content root.

    `project` is optional and named outright when the action came from a tree node:
    with several projects open, "nova pasta" has to say *where*, and the node under the
    pointer is the only honest answer — it is not necessarily the selected one.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    project: str = ""


class MoveRequest(BaseModel):
    """A campaign to move, and the folder to move it into. Both content-root-relative."""

    model_config = ConfigDict(extra="forbid")

    path: str
    directory: str
    project: str = ""


class ProjectRequest(BaseModel):
    """A directory to open. A path, and nothing else: everything else is derived."""

    model_config = ConfigDict(extra="forbid")

    root: str


class McpRequest(BaseModel):
    """The MCP switch's whole vocabulary. There is no third position."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class DemoRequest(BaseModel):
    """The demo button's vocabulary, deliberately the same shape as the MCP switch's.

    One binary setting and no path: where the demo is materialized is the server's
    decision (`demo_root`), and letting the client name a directory would let it name
    any directory.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool


def resolve_verdict(status: str, continue_round: bool) -> tuple[str, bool]:
    """The review screen's verdict buttons, as the two the session knows.

    `pass` continues, `fail_stop` stops, and `fail` does whatever the form said. This
    is the same rule the Jinja form applied and is now the same *function*, because a
    second copy would be a second answer to "what does this button mean" — and the two
    screens would disagree about it on the first change.
    """
    if status == "fail_stop":
        return "fail", False
    if status == "pass":
        return "pass", True
    return "fail", continue_round


def is_comment_error(payload: dict[str, Any]) -> bool:
    """Whether a refusal is the one the form has to keep the reviewer's text for.

    `validate_verdict` decides it and words the message; recovering it from the words
    is not lovely, but it is what both surfaces did, and keeping one implementation is
    better than two guesses. Moving the flag onto `HarnessError` would be the honest
    fix and is a change for the phase that retires the Jinja form.
    """
    if payload.get("code") != "VERDICT_INVALID":
        return False
    message = str(payload.get("message") or "").lower()
    return "comment" in message or "reprove" in message


def new_token() -> str:
    """A fresh per-run secret. `create_app` owns the lifetime; this owns the shape."""
    return secrets.token_urlsafe(32)


def install_api(app: FastAPI) -> None:
    """Register the `/api/*` routes on an app that already has a workspace.

    Kept out of `create_app` so that the Jinja routes and the API can be read as the
    two separate surfaces they are, and so a test can build an app with the API only.
    """

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _read(request, contract.bootstrap_model)

    @app.get("/api/step")
    async def step(request: Request) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        payload = await _read(request, contract.step_model)
        if payload is None:
            return _error_response(
                HarnessError(
                    code="NOT_FOUND",
                    message="no step is on screen",
                    hint="open a case, or wait for the run to stop at one",
                )
            )
        return payload

    @app.get("/api/run")
    async def run(request: Request) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        payload = await _read(request, contract.run_model)
        if payload is None:
            return _error_response(
                HarnessError(
                    code="NOT_FOUND",
                    message="no finished run is on screen",
                    hint="the aggregate appears when a run is done",
                )
            )
        return payload

    @app.post("/api/select")
    async def select(request: Request, body: SelectRequest) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _mutate(request, lambda ws: ws.select(body.key))

    @app.post("/api/start")
    async def start(request: Request, body: StartRequest) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _mutate(request, lambda ws: ws.start(body.scope, body.mode, body.node))

    @app.post("/api/verdict")
    async def verdict(request: Request, body: VerdictRequest) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        resolved, follows = resolve_verdict(body.status, body.continue_round)
        return await _mutate(
            request,
            lambda ws: ws.apply_verdict(resolved, body.comment, follows),
        )

    @app.post("/api/cancel")
    async def cancel(request: Request) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _mutate(request, lambda ws: ws.cancel())

    @app.post("/api/focus")
    async def focus(request: Request, body: FocusRequest) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _mutate(request, lambda ws: ws.focus(body.index))

    @app.get("/api/source")
    async def source(request: Request) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        raw_path = request.query_params.get("path", "")
        if not raw_path:
            return _error_response(
                HarnessError(
                    code="SOURCE_NOT_EDITABLE",
                    message="no `path` was given",
                    hint="ask for `?path=rounds/smoke.yaml`, relative to the content root",
                )
            )
        return await _source(request, lambda ws: ws.read_source(raw_path))

    @app.post("/api/source")
    async def save_source(request: Request, body: SaveSourceRequest) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _source(
            request,
            lambda ws: ws.write_source(body.path, body.text),
        )

    @app.post("/api/folder")
    async def create_folder(request: Request, body: FolderRequest) -> Any:
        """Create a real folder under `campaigns/`, and answer with the new tree.

        The answer is the whole bootstrap because the point of the action is visible
        in the tree: an empty folder has nothing in it to notice, so a client told
        "created" with an unchanged tree would be right to think it failed.
        """
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _mutate(
            request,
            lambda ws: ws.create_folder(body.path, body.project),
        )

    @app.post("/api/move")
    async def move_campaign(request: Request, body: MoveRequest) -> Any:
        """Move a campaign into another folder. Only campaigns move, by construction.

        The campaign is keyed by the `id:` inside it and not by its path, so the
        selection survives the move and the client does not have to re-select a node
        that just changed identity.
        """
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _mutate(
            request,
            lambda ws: ws.move_campaign(body.path, body.directory, body.project),
        )

    @app.get("/api/projects")
    async def projects(request: Request) -> Any:
        """The registry file and its entries, with the ones on screen marked."""
        guard = _guard(request)
        if guard is not None:
            return guard
        return await run_in_threadpool(_projects_payload, request)

    @app.post("/api/projects")
    async def add_project(request: Request, body: ProjectRequest) -> Any:
        """Register a directory and draw it, in one act.

        The gate refuses anything that does not look like a project before a byte is
        written, so a slip in a file dialog cannot turn into a walk over a home
        directory.
        """
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _open_project(request, body.root)

    @app.delete("/api/projects/{project_id}")
    async def remove_project(request: Request, project_id: str) -> Any:
        """Close a project: it leaves the tree and the registry, and no file is deleted."""
        guard = _guard(request)
        if guard is not None:
            return guard
        return await _mutate(
            request,
            lambda ws: _close_project(request.app.state.registry, ws, project_id),
        )

    @app.get("/api/mcp")
    async def mcp(request: Request) -> Any:
        """Whether the embedded MCP server is up, and how to point a client at it."""
        guard = _guard(request)
        if guard is not None:
            return guard
        return await run_in_threadpool(_mcp_payload, request)

    @app.post("/api/mcp")
    async def set_mcp(request: Request, body: McpRequest) -> Any:
        """Flip the embedded MCP server, and answer with its state either way.

        A refused bind is the *answer* to this request and not a failure of it — the
        switch was flipped and the port was taken — so it comes back as `state: error`
        with the reason and a 200. The dialog draws it where the switch is, which is
        the only place the reader can act on it.
        """
        guard = _guard(request)
        if guard is not None:
            return guard
        runtime = request.app.state.mcp
        state = await run_in_threadpool(
            runtime.start if body.enabled else runtime.stop
        )
        return contract.mcp_model(state).model_dump(mode="json")

    @app.get("/api/demo")
    async def demo(request: Request) -> Any:
        """Whether the bundled demo is up, and where its project landed."""
        guard = _guard(request)
        if guard is not None:
            return guard
        return await run_in_threadpool(_demo_payload, request)

    @app.post("/api/demo")
    async def set_demo(request: Request, body: DemoRequest) -> Any:
        """Flip the demo: bring the mock up and materialize the project, or take it down.

        The same shape as the MCP switch with one extra act. Turning it **on** starts
        the mock, then copies the sample out pointed at the port that socket actually
        bound and opens it — because a demo nobody can see is not the feature. Turning
        it **off** stops the socket and leaves the files, so the project the reviewer was
        reading, and the runs they made in it, are still there afterwards.
        """
        guard = _guard(request)
        if guard is not None:
            return guard
        service = request.app.state.demo
        if not body.enabled:
            state = await run_in_threadpool(service.stop)
            return contract.demo_model(
                state,
                root=service.root,
                project_id=id_for(service.root),
            ).model_dump(mode="json")
        state, ref = await run_in_threadpool(service.start, _registry(request))
        if ref is not None:
            opened = await _mutate(request, lambda ws: ws.add_project(ref))
            if isinstance(opened, JSONResponse):
                # The socket is up but the project could not be opened — the registry
                # gate refused it, which a shipped sample should never do. The refusal
                # is the honest answer; a 200 would hide a demo with no tree.
                return opened
        return contract.demo_model(
            state,
            root=service.root,
            project_id=id_for(service.root),
        ).model_dump(mode="json")

    @app.get("/api/events")
    async def events(request: Request) -> Any:
        guard = _guard(request)
        if guard is not None:
            return guard
        return StreamingResponse(
            event_frames(_workspace(request), request.is_disconnected),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                # A webview that proxies the stream would buffer it into uselessness,
                # and the whole point is that a line arrives when it happens.
                "X-Accel-Buffering": "no",
            },
        )


# -- the stream -----------------------------------------------------------


async def event_frames(
    workspace: WorkspaceSession,
    is_disconnected: Any,
) -> AsyncIterator[str]:
    """Push the engine whenever it moves, and nothing when it does not.

    The loop blocks on the engine's own condition variable rather than re-reading a
    snapshot on a timer, so an idle harness costs a thread that is asleep and no work.
    The slice exists only so a client hanging up is noticed promptly; it is not a poll,
    because nothing is read and nothing is sent when the revision has not moved.

    Split from the route so it can be driven without a socket: a test hands in its own
    `is_disconnected` and reads frames until it says stop. The alternative — reading a
    live SSE body through `TestClient` — cannot close the stream from the client side,
    and a test that hangs on a passing implementation is worse than no test.
    """
    revision = await run_in_threadpool(workspace.engine_revision)
    first = await run_in_threadpool(workspace.view)
    yield _sse_event(StreamModel(**contract.stream_payload(first)))
    idle = 0.0
    while not await is_disconnected():
        current = await run_in_threadpool(workspace.wait_change, revision, _SLICE_SECONDS)
        if current == revision:
            idle += _SLICE_SECONDS
            if idle >= _HEARTBEAT_SECONDS:
                idle = 0.0
                yield ": keep-alive\n\n"
            continue
        idle = 0.0
        revision = current
        view = await run_in_threadpool(workspace.view)
        yield _sse_event(StreamModel(**contract.stream_payload(view)))


def _sse_event(model: StreamModel) -> str:
    """One SSE frame. `event: state` so a client can ignore anything else it is sent."""
    return f"event: state\ndata: {model.model_dump_json()}\n\n"


# -- plumbing -------------------------------------------------------------


def _workspace(request: Request) -> WorkspaceSession:
    return request.app.state.workspace


def _registry(request: Request) -> ProjectsRegistry:
    return request.app.state.registry


def _projects_payload(request: Request) -> dict[str, Any]:
    """`GET /api/projects`: the registry, and which of its lines the tree is drawing.

    Read off both the file and the workspace because they are not the same set — the
    root a client was launched for is open without ever having been registered — and a
    dialog that showed only one of them would contradict the other.
    """
    registry = _registry(request)
    workspace = _workspace(request)
    return contract.projects_model(
        registry.load(),
        registry=str(registry.path),
        open_ids=frozenset(project.id for project in workspace.projects),
    ).model_dump(mode="json")


def _mcp_payload(request: Request) -> dict[str, Any]:
    return contract.mcp_model(request.app.state.mcp.state()).model_dump(mode="json")


def _demo_payload(request: Request) -> dict[str, Any]:
    """`GET /api/demo`, with the root and id read off the service that owns them."""
    service = request.app.state.demo
    return contract.demo_model(
        service.state(),
        root=service.root,
        project_id=id_for(service.root),
    ).model_dump(mode="json")


async def _open_project(request: Request, raw_root: str) -> Any:
    """Register a directory and open it, answering with the tree that now includes it.

    The two halves are one act because they are one intention. Writing the file alone
    would leave the project invisible until the next launch; drawing it alone would
    forget it as soon as the window closed.
    """
    try:
        ref = await run_in_threadpool(_register, _registry(request), raw_root)
    except HarnessError as err:
        return _error_response(err)
    return await _mutate(request, lambda ws: ws.add_project(ref))


def _register(registry: ProjectsRegistry, raw_root: str) -> ProjectRef:
    """The blocking half of `POST /api/projects`, off the event loop."""
    return operations.register_project(Path(raw_root), registry=registry)


def _close_project(
    registry: ProjectsRegistry,
    workspace: WorkspaceSession,
    project_id: str,
) -> None:
    """Forget a project in the tree and in the file, in that order.

    The tree goes first because it is the half that can refuse: closing the last open
    project would leave a window with nothing to draw, and refusing *after* rewriting
    the file would forget a project while still showing it — the worst of both.
    """
    workspace.remove_project(project_id)
    registry.remove(project_id)


async def _read(request: Request, build: Any) -> Any:
    """Read the screen off the event loop and translate it, or `None` if there is none.

    `build` returns `None` for a pane that has no step or no aggregate, and that is a
    legitimate answer the caller turns into a 404 — not a crash two lines later.
    """
    view = await run_in_threadpool(_workspace(request).view)
    model = build(view)
    return None if model is None else model.model_dump(mode="json")


async def _source(request: Request, act: Any) -> Any:
    """Read or write a file, and answer with the document and its findings.

    A document the validator dislikes is a 200, not an error. The save happened, and
    what the client must draw is the findings — refusing the write would have thrown
    the reviewer's edit away to make a status code more satisfying. Only a path that
    leaves the content root, or a kind that is not editable, is a refusal.
    """
    try:
        document = await run_in_threadpool(act, _workspace(request))
    except HarnessError as err:
        return _error_response(err)
    return contract.source_model(document).model_dump(mode="json")


async def _mutate(request: Request, act: Any) -> Any:
    """Run a mutating call off the event loop and answer with the new screen.

    A mutation answers with the whole bootstrap rather than an acknowledgement: the
    client has just changed the selection or started a plan and needs to draw the
    result, and a second round trip for it would be one of the round trips the app
    exists to avoid.
    """
    workspace = _workspace(request)
    try:
        await run_in_threadpool(act, workspace)
    except HarnessError as err:
        return _error_response(err)
    view = await run_in_threadpool(workspace.view)
    return contract.bootstrap_model(view).model_dump(mode="json")


def _guard(request: Request) -> JSONResponse | None:
    """`None` when the caller may proceed, a refusal otherwise."""
    if not _origin_allowed(request):
        return _error_response(
            HarnessError(
                code="FORBIDDEN_ORIGIN",
                message="this origin may not call the API",
                hint="the API answers the app it was started with, and nothing else",
            ),
            status_code=403,
        )
    expected = getattr(request.app.state, "api_token", "")
    presented = request.headers.get(TOKEN_HEADER, "")
    # `compare_digest` and not `==`: the token is per-run and short-lived, but a
    # variable-time comparison is the kind of thing that gets copied into somewhere it
    # matters, and writing it correctly here costs nothing.
    if not expected or not secrets.compare_digest(presented, expected):
        return _error_response(
            HarnessError(
                code="UNAUTHORIZED",
                message="missing or invalid API token",
                hint="the running shell holds the token; a bare request does not",
            ),
            status_code=401,
        )
    return None


def _origin_allowed(request: Request) -> bool:
    """Whether the request's `Origin` is one this process started.

    Absent `Origin` is allowed: a script, a test and the CLI are not a browser and
    cannot be forged by a page. Everything a browser sends is checked against
    loopback, which is where the shell's own window lives.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin == "null":
        # A sandboxed or `file://` page. There is no legitimate one here, and the
        # token would be the only thing standing behind it — so refuse.
        return False
    host = urlsplit(origin).hostname
    return host in _LOOPBACK_HOSTS


def _error_response(err: HarnessError, status_code: int | None = None) -> JSONResponse:
    payload = to_dict(err)
    envelope = ApiErrorModel(
        error=HarnessErrorModel(
            code=err.code,
            message=err.message,
            hint=err.hint,
            details=[str(item) for item in err.details],
            exit_code=err.exit_code,
        ),
        comment_required=_is_comment_error(payload),
    )
    return JSONResponse(
        envelope.model_dump(mode="json"),
        status_code=status_code or _STATUS_BY_CODE.get(err.code, 400),
    )


def _is_comment_error(payload: dict[str, Any]) -> bool:
    """The same rule `app.py` applies, in one place now that two surfaces need it."""
    if payload.get("code") != "VERDICT_INVALID":
        return False
    message = str(payload.get("message") or "").lower()
    return "comment" in message or "reprove" in message
