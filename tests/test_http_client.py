from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import load_config
from heimdall_qa.http_client import send


def test_send_records_status_headers_body_and_elapsed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/ingest"
        assert request.headers["x-trace-id"] == "heimdall-run-1"
        return httpx.Response(
            202,
            json={"status": "ACCEPTED"},
            headers={"X-Trace-Id": "heimdall-run-1"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://127.0.0.1:8080")
    exchange = send(
        client,
        "POST",
        "http://127.0.0.1:8080/api/ingest",
        headers={"X-Trace-Id": "heimdall-run-1"},
        json_body={"event_type": "llm_tokens"},
    )
    assert exchange.status_code == 202
    assert exchange.response_headers["x-trace-id"] == "heimdall-run-1"
    assert '"ACCEPTED"' in exchange.response_text
    assert exchange.elapsed_ms >= 0


def test_load_config_reads_harness_infrastructure_only(tmp_path: Path):
    """`config.yaml` holds no fact about an API, and refuses one that tries."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "ui:",
                "  host: 127.0.0.1",
                "  port: 7878",
                "probes:",
                "  ingest_poll_ms: 8000",
                "pace_gap_ms: 2000",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert isinstance(config, HarnessConfig)
    assert config.ui.port == 7878
    assert config.probes.ingest_poll_ms == 8000
    assert config.pace_gap_ms == 2000
    assert config.project.descriptor is None


def test_load_config_refuses_a_product_fact(tmp_path: Path):
    """Silently ignoring `legacy_web` is how a run hits a stale origin."""
    path = tmp_path / "config.yaml"
    path.write_text("legacy_web: http://127.0.0.1:8080\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)
