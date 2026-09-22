from dataclasses import dataclass
from time import perf_counter
from typing import Any
from typing import Mapping

import httpx

from nokr_qa.errors import HarnessError


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
            hint="start NokrAPI profile web, or point nokr_web in config.yaml",
        ) from exc
    except httpx.ConnectError as exc:
        raise HarnessError(
            code="HTTP_UNREACHABLE",
            message=f"cannot connect to {url}",
            hint="start NokrAPI profile web, or point nokr_web in config.yaml",
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
