"""The mock API the bundled demo project reviews.

Six routes, no database and no auth, and every one of them exists to make a status
the review screen can draw:

| Route | What it is for |
| --- | --- |
| `GET /demo/health` | The happy path. 200, and one INFO line in the log. |
| `POST /demo/items` | A create. 201 and a fixed id, so a case can assert on it. |
| `GET /demo/items/{id}` | A lookup. 200 for the two ids that exist, 404 in the harness envelope for anything else. |
| `GET /demo/count` | How many items the process has created. **Numeric**, so a `probe` surface can compare it. |
| `GET /demo/boom` | An unhandled failure. Always 500, so `http_5xx` is reachable. |
| `GET /demo/warn` | 200, and a **WARN** line for the trace, which is what turns `http.success` into a `warn`. |
| `GET /demo/slow` | 200, but after 120 ms — past the 40 ms budget the descriptor declares. |

Two details are load-bearing and easy to lose:

**Every response echoes `X-Trace-Id`.** The harness stamps it on the request and the
`http.error` pack compares it to the `traceId` in the body, so a mock that dropped
either one would make its own 404 look like a contract violation.

**Every request writes one line carrying the trace id.** The demo descriptor declares
this file as its one log source, so `http.success` is *measured* rather than skipped —
and a route that wrote nothing would be reported as a missing trace, which is a
finding about the API and would drown the ones the demo is trying to show.

The log path is a parameter and not a constant: the app is started on a thread by
`runtime.py`, which owns where the file goes, and a second demo run must not append to
the first one's evidence.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse

#: Where the log goes when nothing said otherwise. An environment variable rather
#: than a default path, because the honest default is "the caller must say" and a
#: constant here would eventually write into a repository.
LOG_ENV = "HEIMDALL_QA_DEMO_LOG"

#: The ids that exist. Two, so a case can ask for one that does and one that does not.
ITEMS = {"itm_1": "widget", "itm_2": "gadget"}

#: How long `/demo/slow` waits. Declared once, because the descriptor's budget and a
#: test's expectation both have to agree with it.
SLOW_SECONDS = 0.12


def create_app(log_path: Path | None = None) -> FastAPI:
    """The mock, writing its log to `log_path`.

    `log_path` may be omitted only when `$HEIMDALL_QA_DEMO_LOG` names one. The refusal
    is deliberate: an app that picked a path itself would write into whatever the
    working directory happened to be, and a demo's evidence belongs in the demo's
    data directory.
    """
    resolved = _resolve_log(log_path)
    app = FastAPI(title="heimdall-qa-demo", version="1.0.0")
    #: Per-app and not module-level: the count is a fact about this process's life,
    #: and a test that builds two apps must not have the second one inherit the
    #: first one's. It is what makes `/demo/count` a surface a probe can move.
    created = {"n": 0}

    @app.middleware("http")
    async def echo_trace(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Hand the correlation header back, the way an ordinary service would."""
        response = await call_next(request)
        trace = request.headers.get("x-trace-id")
        if trace:
            response.headers["X-Trace-Id"] = trace
        return response

    @app.get("/demo/health")
    async def health(request: Request) -> dict[str, str]:
        _log(resolved, "INFO", request, "health ok")
        return {"status": "ok"}

    @app.post("/demo/items", status_code=201)
    async def create_item(payload: dict[str, str], request: Request) -> dict[str, str]:
        created["n"] += 1
        _log(resolved, "INFO", request, "item created")
        return {"id": "itm_3", "name": payload.get("name", "widget")}

    @app.get("/demo/items/{item_id}")
    async def read_item(item_id: str, request: Request) -> Response:
        name = ITEMS.get(item_id)
        if name is None:
            _log(resolved, "INFO", request, f"item {item_id} not found")
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"no item with id {item_id}",
                    "traceId": _trace(request),
                },
            )
        _log(resolved, "INFO", request, f"item {item_id} read")
        return JSONResponse(content={"id": item_id, "name": name})

    @app.get("/demo/count")
    async def count(request: Request) -> dict[str, int]:
        _log(resolved, "INFO", request, "count read")
        return {"count": created["n"]}

    @app.get("/demo/boom")
    async def boom(request: Request) -> Response:
        _log(resolved, "ERROR", request, "unhandled failure")
        return JSONResponse(
            status_code=500,
            content={
                "error": "the demo failed on purpose",
                "traceId": _trace(request),
            },
        )

    @app.get("/demo/warn")
    async def warn(request: Request) -> dict[str, str]:
        _log(resolved, "WARN", request, "the demo warns on purpose")
        return {"status": "ok"}

    @app.get("/demo/slow")
    async def slow(request: Request) -> dict[str, str]:
        await asyncio.sleep(SLOW_SECONDS)
        _log(resolved, "INFO", request, "slow route answered")
        return {"status": "ok"}

    return app


def _resolve_log(log_path: Path | None) -> Path:
    raw = log_path or os.environ.get(LOG_ENV) or ""
    if not str(raw):
        raise RuntimeError(
            f"the demo API needs a log path; pass one, or set ${LOG_ENV}"
        )
    resolved = Path(raw).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _trace(request: Request) -> str:
    return request.headers.get("x-trace-id", "")


def _log(log_path: Path, level: str, request: Request, message: str) -> None:
    """One line carrying the trace, in a shape `format: text` already reads.

    `trace_id: [...]` is the same spelling `examples/spring-fixture` uses, and the
    default marker is the id itself, so no `marker:` is needed in the descriptor —
    which is the point: a demo should not need a regex to be read.
    """
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{stamp} [vt] {level:<5} c.d.DemoApplication - "
        f"trace_id: [{_trace(request)}] - {message}\n"
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
