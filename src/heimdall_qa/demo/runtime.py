"""The demo API on a thread, on a port the operating system picks.

The MCP runtime in `heimdall_qa.mcp.runtime` owns `8765` on purpose — the URL it
prints has to survive a restart, because the reader pastes it into a client. The demo
is the opposite case: its port is written into a descriptor the harness will read in
the same minute, and the process that owns it is the process that just started it. So
the port is **ephemeral**, and the state carries whatever the socket actually bound.

That is what makes two demo runs, or a demo beside a real service, not a conflict.

Three decisions, all inherited from the MCP runtime because they were already right:

- **The `uvicorn.Server` is ours.** `uvicorn.run` builds its own and keeps no
  reference, which leaves nothing to stop.
- **A refused bind is a state, not a crash.** A switch that took the window down with
  it would be worse than the switch.
- **The log path is given, not guessed.** It lives in the demo's data directory, so a
  second materialization does not append to the first one's evidence.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from heimdall_qa.demo.app import create_app
from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import to_dict
from heimdall_qa.serve.bind import assert_local_bind

#: Loopback only, and asked for as `0` so the kernel chooses. A fixed number would be
#: a port a second demo, or a service on this machine, could already hold.
HOST = "127.0.0.1"
PORT = 0

#: How long to wait for the bind. The app is built in this process, so this bounds a
#: bind and not a handshake.
_READY_TIMEOUT_SECONDS = 5.0
_READY_POLL_SECONDS = 0.02

#: How long `stop` waits for the socket to close before saying it did not.
_STOP_TIMEOUT_SECONDS = 5.0

_STATES = frozenset({"running", "stopped", "error"})


@dataclass(frozen=True)
class DemoApiState:
    """Where the mock is, once it is anywhere at all."""

    host: str
    port: int
    state: str
    log_path: str = ""
    error: dict[str, object] | None = None

    @property
    def enabled(self) -> bool:
        return self.state == "running"

    @property
    def base_url(self) -> str:
        """The origin a descriptor's `base_url` should say, or `""` when it is down."""
        return f"http://{self.host}:{self.port}" if self.port else ""


class DemoRuntime:
    """The demo API on a daemon thread this class can stop.

    Deliberately not idempotent beyond "already running is running": the button that
    starts it can be pressed twice, and an error on the second press would be a lie
    about what happened.
    """

    def __init__(
        self,
        *,
        log_path: Path,
        host: str = HOST,
    ) -> None:
        self._host = host
        self._log_path = Path(log_path)
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._port = 0
        self._error: dict[str, object] | None = None

    def state(self) -> DemoApiState:
        with self._lock:
            return self._state()

    def start(self) -> DemoApiState:
        """Bring the mock up, or report by name why it could not."""
        with self._lock:
            if self._alive():
                return self._state()
            self._error = None
            try:
                assert_local_bind(self._host)
                app = create_app(self._log_path)
            except (HarnessError, RuntimeError) as exc:
                self._error = _as_error(exc)
                return self._state()
            self._server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=self._host,
                    port=PORT,
                    # The window's stdout belongs to the reader; a uvicorn banner in
                    # front of it is noise about something they turned on with a press.
                    log_level="warning",
                )
            )
            self._thread = threading.Thread(
                target=self._server.run,
                name="heimdall-qa-demo",
                daemon=True,
            )
            self._thread.start()
            self._error = self._await_ready()
            return self._state()

    def stop(self) -> DemoApiState:
        """Ask the mock down and wait briefly for the socket to close."""
        with self._lock:
            server, thread = self._server, self._thread
            if server is None or thread is None:
                return self._state()
            server.should_exit = True
            self._server, self._thread = None, None
            thread.join(timeout=_STOP_TIMEOUT_SECONDS)
            self._port = 0
            self._error = None
            return self._state()

    # -- internals ---------------------------------------------------------

    def _alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _await_ready(self) -> dict[str, object] | None:
        """Wait for the bind, then read back the port the kernel actually chose."""
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        server, thread = self._server, self._thread
        assert server is not None and thread is not None
        while not server.started:
            if not thread.is_alive():
                self._server, self._thread = None, None
                return to_dict(
                    HarnessError(
                        code="DEMO_NO_BIND",
                        message=f"the demo API could not bind {self._host}",
                        hint="free a loopback port, or restart the client",
                    )
                )
            if time.monotonic() > deadline:
                return to_dict(
                    HarnessError(
                        code="DEMO_NO_BIND",
                        message=(
                            f"the demo API did not come up on {self._host} in"
                            f" {_READY_TIMEOUT_SECONDS:.0f}s"
                        ),
                        hint="re-run with --verbose for the traceback",
                    )
                )
            time.sleep(_READY_POLL_SECONDS)
        self._port = _bound_port(server)
        if not self._port:
            return to_dict(
                HarnessError(
                    code="DEMO_NO_BIND",
                    message="the demo API started but bound no socket",
                    hint="re-run with --verbose for the traceback",
                )
            )
        return None

    def _state(self) -> DemoApiState:
        if self._error is not None:
            state = "error"
        elif self._alive() and self._server is not None and self._server.started:
            state = "running"
        else:
            state = "stopped"
        return DemoApiState(
            host=self._host,
            port=self._port,
            state=state,
            log_path=str(self._log_path),
            error=self._error,
        )


def _bound_port(server: uvicorn.Server) -> int:
    """The port the OS handed out, read off the listening socket.

    Read here and nowhere else: `config.port` is `0` when the caller asked for an
    ephemeral port, so it is not the answer, and a demo whose descriptor said `:0`
    would be a descriptor nothing could reach.
    """
    servers = getattr(server, "servers", None) or []
    for listener in servers:
        for socket in getattr(listener, "sockets", None) or []:
            try:
                return int(socket.getsockname()[1])
            except (IndexError, OSError, TypeError):
                continue
    return 0


def _as_error(exc: BaseException) -> dict[str, object]:
    if isinstance(exc, HarnessError):
        return to_dict(exc)
    return to_dict(
        HarnessError(
            code="DEMO_NO_LOG",
            message=str(exc),
            hint="the demo API writes its evidence to a file; the path has to be one",
        )
    )
