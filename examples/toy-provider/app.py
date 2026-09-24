"""The API the toy provider talks to: three routes, no auth, no dependencies.

Run it before the round::

    python -m uvicorn app:app --port 8110

It exists to prove that Heimdall QA runs against a REST API it has never seen, so
it is deliberately boring: no database, no state beyond two items in a list, no
authentication. If running the harness against this needs a special case anywhere
in `src/`, the harness is not generic yet.
"""

from collections.abc import Awaitable
from collections.abc import Callable

from fastapi import FastAPI
from fastapi import Request
from fastapi import Response

app = FastAPI(title="toy-api", version="1.0.0")

#: Two items, so `total` is a number a case can assert on.
ITEMS: list[dict[str, str]] = [
    {"id": "itm_1", "name": "widget"},
    {"id": "itm_2", "name": "gadget"},
]


@app.middleware("http")
async def echo_trace(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Hand the correlation header back, the way a real API would.

    The harness stamps `X-Trace-Id` on every request; an API that drops it leaves
    the trace pack with nothing to correlate, which is a finding about the API
    rather than about the harness.
    """
    response = await call_next(request)
    trace = request.headers.get("x-trace-id")
    if trace:
        response.headers["X-Trace-Id"] = trace
    return response


@app.get("/toy/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/toy/items")
async def list_items() -> dict[str, object]:
    return {"items": ITEMS, "total": len(ITEMS)}


@app.post("/toy/items", status_code=201)
async def create_item(payload: dict[str, str]) -> dict[str, str]:
    return {"id": f"itm_{len(ITEMS) + 1}", "name": payload.get("name", "widget")}
