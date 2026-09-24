from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from heimdall_qa.errors import HarnessError

#: What to do when nothing answers. The harness cannot know how a project is
#: started, so it names the two things that are always true: the process is not
#: listening, or the descriptor points somewhere other than where it listens.
_UNREACHABLE_HINT = (
    "start the API, or fix environments[].base_url in the project descriptor"
)


@dataclass(frozen=True)
class HttpExchange:
    method: str
    url: str
    request_headers: dict[str, str]
    request_body: Any
    status_code: int
    response_headers: dict[str, str]
    response_text: str
    elapsed_ms: float


def send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
) -> HttpExchange:
    started = perf_counter()
    try:
        response = client.request(method, url, headers=headers, json=json_body)
    except httpx.ConnectTimeout as exc:
        raise HarnessError(
            code="HTTP_TIMEOUT",
            message=f"timed out connecting to {url}",
            hint=_UNREACHABLE_HINT,
        ) from exc
    except httpx.ConnectError as exc:
        raise HarnessError(
            code="HTTP_UNREACHABLE",
            message=f"cannot connect to {url}",
            hint=_UNREACHABLE_HINT,
        ) from exc
    elapsed_ms = (perf_counter() - started) * 1000.0
    return HttpExchange(
        method=method.upper(),
        url=str(response.request.url),
        request_headers=dict(response.request.headers),
        request_body=json_body,
        status_code=response.status_code,
        response_headers=dict(response.headers),
        response_text=response.text,
        elapsed_ms=elapsed_ms,
    )
