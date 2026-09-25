"""The pywebview shell: one process, one window, one workspace.

What this module owns is the *lifetime*, not the harness. It binds a loopback port,
starts the app that `heimdall-qa serve` would have started, waits for the socket, hands
the window a URL with the run's token in it, and — the part that needs a decision
rather than code — decides what closing the window means.

Four things are deliberate here and each is a place a shell like this usually goes
wrong:

- **The window is not the process's owner.** The run directory is the deliverable and
  the engine writes it, so closing the window cancels the plan *through the engine*
  rather than killing a process mid-write. A cancel settles between steps and leaves
  the rounds that finished with their summaries, which is the same artifact a `walk`
  run stopped by hand leaves. A `SIGKILL` would leave a half-written step.
- **The port is bound by the OS.** Port 0 and then read the socket, so two windows
  open at once do not fight over a configured port and nothing has to be chosen.
- **The token is a fragment, never a query.** A fragment is not sent to the server and
  not kept in the history; a query string would be both.
- **A missing WebKit is a sentence, not a traceback.** On Linux this is the most
  likely first contact a new user has with `heimdall-qa desktop`, and
  `ImportError: No module named 'gi'` is where an onboarding ends.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from typing import Any

import httpx
import uvicorn

from heimdall_qa.errors import HarnessError
from heimdall_qa.mcp.runtime import DEFAULT_PORT
from heimdall_qa.operations import Settings
from heimdall_qa.operations import build_workspace
from heimdall_qa.serve import api
from heimdall_qa.serve.app import create_app
from heimdall_qa.serve.bind import assert_local_bind
from heimdall_qa.serve.webapp import WEBAPP
from heimdall_qa.workspace import WorkspaceSession

#: How long to wait for uvicorn to bind. The app is built in this process, so this
#: bounds a socket call and not a network hop; twenty seconds is generous on purpose,
#: because a slow first import on a cold cache is not a failure worth reporting.
_READY_TIMEOUT_SECONDS = 20.0
_READY_POLL_SECONDS = 0.05

#: How long a cancelled plan is given to settle before the process exits. A step
#: already on the wire is not interrupted — the engine settles between steps — so this
#: bounds the wait for a slow request rather than the cancel itself. The cost of
#: expiring here is one step's response file; the cost of waiting forever is a window
#: that will not close.
_SETTLE_GRACE_SECONDS = 20.0


def run_desktop(
    settings: Settings,
    *,
    target: str | None = None,
    mcp_port: int = DEFAULT_PORT,
) -> int:
    """Open the window and return once it is closed."""
    webview = _load_webview()
    host = settings.config.ui.host
    # Same rule as `serve`, with no exception for the shell: a review harness is not a
    # service, and this is the line that keeps it off a network.
    assert_local_bind(host)

    token = api.new_token()
    client = httpx.Client(timeout=10.0)
    workspace: WorkspaceSession | None = None
    shell: _Server | None = None
    try:
        workspace = build_workspace(settings, client=client, target=target)
        app = create_app(
            workspace=workspace,
            api_token=token,
            webapp=WEBAPP,
            mcp_port=mcp_port,
        )
        shell = _Server(app, host=host, port=0)
        url = shell.start(timeout=_READY_TIMEOUT_SECONDS)

        webview.create_window(
            "Heimdall QA",
            f"{url}/#token={token}",
            width=1440,
            height=920,
            min_size=(960, 600),
            # The tree is the navigation; a browser-style back button would be a second
            # history over a screen that only ever shows one thing at a time.
            easy_drag=False,
        )
        _start(webview)
    finally:
        _drain(workspace, shell)
        client.close()
    return 0


class _Server:
    """uvicorn on a thread, and the port it actually got.

    `started` is uvicorn's own event and not a poll of `/healthz`: the app is
    constructed in this process, so "the socket is bound and startup ran" is exactly
    what the window needs, and asserting it over HTTP would be asking the shell to
    authenticate against itself.
    """

    def __init__(self, app: Any, *, host: str, port: int) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                access_log=False,
                # The shell's own stdout is the terminal it was started from; a
                # uvicorn banner there would be noise in front of the window.
                log_level="warning",
            )
        )
        self._thread = threading.Thread(
            target=self._server.run,
            name="heimdall-desktop-server",
            daemon=True,
        )

    def start(self, *, timeout: float) -> str:
        """Bind, wait for readiness, and answer with the URL the window loads."""
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive():
                raise HarnessError(
                    code="DESKTOP_SERVER_DIED",
                    message="the review server stopped before the window opened",
                    hint="re-run with --verbose for the traceback",
                )
            if time.monotonic() > deadline:
                raise HarnessError(
                    code="DESKTOP_SERVER_TIMEOUT",
                    message=f"the review server did not start within {timeout:.0f}s",
                    hint="re-run with --verbose; another process may hold the port",
                )
            time.sleep(_READY_POLL_SECONDS)
        bound = self._server.servers[0].sockets[0]
        host, port = bound.getsockname()[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5.0)


def _drain(workspace: WorkspaceSession | None, shell: _Server | None) -> None:
    """Cancel what is running, wait for it to settle, and stop the server.

    The order matters and it is the whole contract: cancel through the engine first, so
    the round in flight closes the way every other stop closes it, and only then take
    the socket away. Doing it the other way round would leave the engine writing into a
    process that is already going.
    """
    if workspace is not None and workspace.view().engine.busy:
        print(
            "a plan was running — cancelling so the run closes with its evidence",
            file=sys.stderr,
        )
        workspace.cancel()
        settled = workspace.wait_settled(timeout=_SETTLE_GRACE_SECONDS)
        if settled.engine.busy:
            print(
                f"the step on the wire did not settle in {_SETTLE_GRACE_SECONDS:.0f}s;"
                " the run is left as a hand-stopped walk",
                file=sys.stderr,
            )
    if shell is not None:
        shell.stop()


def _load_webview() -> Any:
    """pywebview, or a message that names the missing system package.

    The project's rule for a missing piece — a step type that is not registered, an
    unknown config key — is an error a reader can act on. On Linux, pywebview draws
    through GTK and WebKit, which are system packages a `pip install` cannot bring, and
    the import that fails is `gi`, whose message names neither the client nor the fix.

    Both refusals name `sys.executable`, because the failure a reader actually meets is
    not "the dependency is missing" but "*this* interpreter cannot see it" — a venv on
    a different Python than the system's, or an editable install whose metadata was
    written before the dependency was declared. The interpreter that ran the command is
    the one fact neither `pip list` elsewhere nor the error's own text can supply.
    """
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - a broken install, worded for the reader
        raise HarnessError(
            code="DESKTOP_NO_PYWEBVIEW",
            message=(
                f"the desktop client needs pywebview, which {sys.executable} cannot import"
            ),
            hint=(
                "pip install -e .   # pywebview is a core dependency, and an editable"
                " install made before it was declared keeps stale metadata; on Linux it"
                " also needs the system python3-gi, which pip cannot bring"
            ),
        ) from exc

    if _webkit_missing():
        raise HarnessError(
            code="DESKTOP_NO_WEBKIT",
            message=(
                f"pywebview is installed but {sys.executable} cannot import the system"
                f" WebKit"
            ),
            hint=(
                "apt install python3-gi gir1.2-webkit2-4.1   # and check that this"
                " interpreter can see them: a venv on another Python needs"
                " --system-site-packages"
            ),
        )
    return webview


def _webkit_missing() -> bool:
    """Whether the GTK backend is absent on a system that needs it.

    Only asked on Linux, and only about `gi`: pywebview can also draw through Qt
    WebEngine, so a false here is not a promise that the window opens — it is a refusal
    to claim WebKit is missing when something else may serve. `_start` catches the case
    this cannot see.
    """
    if not sys.platform.startswith("linux"):
        return False
    return importlib.util.find_spec("gi") is None


def _start(webview: Any) -> None:
    """`webview.start`, with the backend's own complaint translated."""
    try:
        webview.start()
    except Exception as exc:  # pragma: no cover - depends on the host's GUI stack
        raise HarnessError(
            code="DESKTOP_NO_BACKEND",
            message=f"no GUI backend could open the window: {exc}",
            hint="apt install python3-gi gir1.2-webkit2-4.1   # or: heimdall-qa serve",
        ) from exc
