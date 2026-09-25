"""The review surface: one app, one client, one JSON boundary.

This factory used to install one of two renderers — a server-rendered Jinja page, or the
built SPA — and phase 6 of `contrib/architecture.md` §9.5 retired the first. What is
left is the shape ADR-04 was actually protecting: **one renderer for one workspace**. It
is now the SPA, mounted from `serve/webapp.py`, and Jinja is not "kept around just in
case" — the duplication the earlier ADR refused was between two renderers, not between
two technologies.

What the API and the client share is the workspace object, so they cannot disagree about
what the engine is doing. The renderer only decides how it is drawn, and there is one
answer to that now.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from heimdall_qa.demo.sample import DemoService
from heimdall_qa.mcp.runtime import DEFAULT_PORT
from heimdall_qa.mcp.runtime import McpRuntime
from heimdall_qa.projects import ProjectsRegistry
from heimdall_qa.serve import api
from heimdall_qa.serve.webapp import webapp_dir
from heimdall_qa.session import RoundSession
from heimdall_qa.workspace import WorkspaceSession


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Let the app come up, and stop the sockets it owns when it goes down.

    A daemon thread would die with the process anyway; stopping them *here* is what
    releases the sockets before the window exits, so a reviewer who closes and reopens
    the client does not meet their own stale listener still holding 8765 — or the
    demo's ephemeral port, which is why the demo is stopped in the same place.
    """
    try:
        yield
    finally:
        app.state.mcp.stop()
        app.state.demo.stop()


def create_app(
    *,
    session: RoundSession | None = None,
    workspace: WorkspaceSession | None = None,
    api_token: str | None = None,
    webapp: Path | None = None,
    registry: ProjectsRegistry | None = None,
    mcp_port: int = DEFAULT_PORT,
    demo: DemoService | None = None,
) -> FastAPI:
    """The client over one workspace.

    `webapp` is a parameter and not a constant so that a test can point the mount at a
    throwaway directory — including an empty one, which is how `WEBAPP_NOT_BUILT` is
    exercised without moving the real bundle.

    `registry`, `mcp_port` and `demo` are parameters for the same reason: a test that
    adds a project must not write into the home of whoever runs the suite, a test that
    flips the MCP switch must not fight the real port 8765 for a socket, and a test that
    presses the demo button must not materialize a project into the real data home.
    """
    if workspace is None:
        if session is None:
            raise TypeError("create_app requires session or workspace")
        workspace = WorkspaceSession.wrap(session)
    app = FastAPI(
        title="Heimdall QA",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    #: The per-run secret `/api/*` requires. Passed in by the desktop shell so the
    #: window can be handed the same value the server checks; generated here otherwise
    #: so that "forgot to set it" cannot mean "no token needed".
    app.state.api_token = api_token or api.new_token()
    app.state.workspace = workspace
    #: The client's own memory of which projects it has open. Built here rather than
    #: read per request so that the file's path is decided once, and so the whole
    #: surface — tree, add, remove — agrees about where that memory lives.
    app.state.registry = registry or ProjectsRegistry()
    #: The embedded MCP server, constructed but **not** started. A switch that is on
    #: when the window opens would be a socket nobody asked for; the reviewer turns it
    #: on from the Server dialog, and it is stopped on shutdown.
    app.state.mcp = McpRuntime(port=mcp_port)
    #: The bundled demo project, constructed but **not** started. Same rule as the MCP
    #: server: pressing the button in the Server dialog is what binds a socket and
    #: materializes the sample, and nothing happens on a window that is merely open.
    app.state.demo = demo if demo is not None else DemoService()
    #: The JSON surface, registered before the mount so that a mount at `/` cannot
    #: shadow it. See `serve/api.py` for why it needs a token.
    api.install_api(app)
    install_client(app, webapp)
    return app


def install_client(app: FastAPI, webapp: Path | None = None) -> None:
    """Serve the built SPA at `/`, and nothing else.

    Every route this surface owns is registered *before* the mount, because a `Mount`
    at `/` answers for every path that is not already matched — including ones declared
    after it. That is not a subtle ordering preference: a bundle that answered for
    `POST /api/start`, or for `/healthz`, would look like a client bug for an afternoon.
    The mount is therefore the last statement in this function and must stay there.

    `webapp_dir` is called first, so a checkout that has not run `npm run build` gets
    `WEBAPP_NOT_BUILT` and a command to type instead of a blank window.
    """
    built = webapp_dir(webapp)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Unauthenticated liveness: the socket is up and the app is constructed.

        A plain 200 with no token, because it says nothing about the workspace. The
        alternative — reading `/api/bootstrap` — would mean the shell holding the token
        and checking authorization twice, and would tell a caller the whole collection.
        """
        return JSONResponse({"ok": True})

    app.mount("/", StaticFiles(directory=str(built), html=True), name="webapp")
