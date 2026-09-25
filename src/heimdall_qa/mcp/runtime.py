"""The MCP server, embedded in the client and switched on from its own UI.

Driving the harness from a model used to mean a second process: `heimdall-qa-mcp` on
stdio, launched by whatever MCP client was in play, with its own working directory and
its own idea of which project it was looking at. That is the right shape for a
headless agent and the wrong one for a reviewer who has the window open — the two
sides would each index the collection, and neither would see the other's runs.

So the server can be run in the same process as the window, over
`streamable-http` on loopback, and the client gets a switch for it. Three things make
that safe to do:

**It is loopback and it says so.** `assert_local_bind` is the same gate `serve` and
`desktop` pass through. An MCP server exposes *execution* — `run_round` goes to the
network and writes evidence — so it is not something to hand to a LAN.

**The HTTP app is built here, not by the SDK's `run`.** `MCPServer.run` constructs its
own `uvicorn.Server` internally and keeps no reference to it, which leaves no way to
stop it: the switch would turn on and never turn off. Building
`streamable_http_app()` and serving it under a `uvicorn.Server` this class owns costs
twenty lines and buys a real `stop()`.

**A missing optional dependency is a state, not a crash.** The `mcp` extra is not
installed by default — a project that never drives the harness from a model should not
carry the SDK — so the switch has to be able to answer "not installed, here is the
command" without taking the window down with it. `build_server()` already produces
exactly that refusal; this turns it into the payload the dialog renders.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import uvicorn

from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import to_dict
from heimdall_qa.mcp.server import build_server
from heimdall_qa.serve.bind import assert_local_bind

#: Loopback, and the port the CLI's own MCP server already documents. A port *number*
#: rather than an ephemeral one: the whole point of the config snippet is that the
#: reader pastes it into a client that will still be there after this process ends, so
#: a port that changed every launch would be a snippet that is always one restart stale.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: What a client appends to the base URL. The SDK's own default, spelled out because
#: it also appears in the snippet and in the refusal messages.
STREAMABLE_HTTP_PATH = "/mcp"

#: How long to wait for the socket to bind. The app is built in this process, so this
#: bounds a bind and not a handshake; five seconds is a slow machine, not a slow network.
_READY_TIMEOUT_SECONDS = 5.0
_READY_POLL_SECONDS = 0.05

#: How long `stop` waits for the server to come down before saying it did not.
_STOP_TIMEOUT_SECONDS = 5.0

_STATES = frozenset({"running", "stopped", "error"})


@dataclass(frozen=True)
class McpState:
    """What the switch and the dialog need to draw themselves."""

    host: str
    port: int
    path: str
    state: str
    error: dict[str, object] | None = None

    @property
    def enabled(self) -> bool:
        """Whether the server is up. The one thing the switch is a question about."""
        return self.state == "running"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    def config_snippet(self) -> str:
        """The block to paste into an MCP client, with the URL already filled in.

        Written as JSON and not as YAML: both Cursor and Claude Desktop read a JSON
        `mcpServers` block, and a snippet the reader has to translate is a snippet they
        will get wrong once and then stop trusting.
        """
        return json.dumps(
            {
                "mcpServers": {
                    "heimdall-qa": {
                        "type": "http",
                        "url": self.url,
                    }
                }
            },
            indent=2,
        )


class McpRuntime:
    """A `uvicorn.Server` around the MCP app, on a thread this class can stop.

    Deliberately not idempotent beyond "already running is running": `start` on a live
    server answers with the state rather than raising, because the switch can be
    flipped twice by a double click and an error there would be a lie about what
    happened.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._error: dict[str, object] | None = None

    def state(self) -> McpState:
        with self._lock:
            return self._state()

    def start(self) -> McpState:
        """Bring the server up, or report by name why it could not."""
        with self._lock:
            if self._alive():
                return self._state()
            self._error = None
            app, failure = self._build()
            if app is None:
                self._error = failure
                return self._state()
            self._server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=self._host,
                    port=self._port,
                    # The window's stdout belongs to the person who launched it, and a
                    # uvicorn banner in front of it is noise about a subsystem they
                    # turned on with a switch.
                    log_level="warning",
                )
            )
            self._thread = threading.Thread(
                target=self._server.run,
                name="heimdall-qa-mcp",
                daemon=True,
            )
            self._thread.start()
            failure = self._await_ready()
            if failure is not None:
                self._error = failure
            return self._state()

    def stop(self) -> McpState:
        """Ask the server down and wait briefly for the socket to close.

        `should_exit` is uvicorn's own cooperative stop, which is why the app had to be
        served under a server this class owns. The wait is bounded on purpose: a
        shutdown that hangs must not hang the window that asked for it.
        """
        with self._lock:
            server, thread = self._server, self._thread
            if server is None or thread is None:
                return self._state()
            server.should_exit = True
            self._server, self._thread = None, None
            thread.join(timeout=_STOP_TIMEOUT_SECONDS)
            self._error = None
            return self._state()

    # -- internals ---------------------------------------------------------

    def _alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _build(self) -> tuple[object | None, dict[str, object] | None]:
        """The ASGI app, or the harness error that says why there is none.

        The two failures are different and the caller must not have to guess: an
        uninstalled SDK raises `MCP_EXTRA_MISSING` with a `pip install` hint, and a
        loopback violation is the harness refusing to be a service. Both are turned
        into the payload rather than raised, because this runs on the request that
        flipped the switch and a 500 there would tell the reader nothing.
        """
        try:
            assert_local_bind(self._host)
            server = build_server()
            return (
                server.streamable_http_app(
                    streamable_http_path=STREAMABLE_HTTP_PATH,
                    host=self._host,
                ),
                None,
            )
        except HarnessError as err:
            return None, to_dict(err)

    def _await_ready(self) -> dict[str, object] | None:
        """Wait for the bind, and turn a failed one into a named error.

        A port already in use is the common failure and uvicorn reports it by logging
        and returning, so the thread dying before `started` is the signal. The message
        names the port, because the fix is either to turn off the other server or to
        change this one's.
        """
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        server, thread = self._server, self._thread
        assert server is not None and thread is not None
        while not server.started:
            if not thread.is_alive():
                self._server, self._thread = None, None
                return to_dict(
                    HarnessError(
                        code="MCP_NO_BIND",
                        message=(
                            f"the MCP server could not bind {self._host}:{self._port}"
                        ),
                        hint=(
                            "another process may hold the port; close it, or start the"
                            " harness with a free --mcp-port"
                        ),
                    )
                )
            if time.monotonic() > deadline:
                return to_dict(
                    HarnessError(
                        code="MCP_NO_BIND",
                        message=(
                            f"the MCP server did not come up on"
                            f" {self._host}:{self._port} in"
                            f" {_READY_TIMEOUT_SECONDS:.0f}s"
                        ),
                        hint="re-run with --verbose for the traceback",
                    )
                )
            time.sleep(_READY_POLL_SECONDS)
        return None

    def _state(self) -> McpState:
        if self._error is not None:
            state = "error"
        elif self._alive() and self._server is not None and self._server.started:
            state = "running"
        else:
            state = "stopped"
        return McpState(
            host=self._host,
            port=self._port,
            path=STREAMABLE_HTTP_PATH,
            state=state,
            error=self._error,
        )
