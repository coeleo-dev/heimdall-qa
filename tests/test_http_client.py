from pathlib import Path

import httpx

from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import load_config
from heimdall_qa.http_client import send


def test_send_records_status_headers_body_and_elapsed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/ingest"
        assert request.headers["x-trace-id"] == "nokrqa-run-1"
        return httpx.Response(
            202,
            json={"status": "ACCEPTED"},
            headers={"X-Trace-Id": "nokrqa-run-1"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://127.0.0.1:8080")
    exchange = send(
        client,
        "POST",
        "http://127.0.0.1:8080/api/ingest",
        headers={"X-Trace-Id": "nokrqa-run-1"},
        json_body={"event_type": "llm_tokens"},
    )
    assert exchange.status_code == 202
    assert exchange.response_headers["x-trace-id"] == "nokrqa-run-1"
    assert '"ACCEPTED"' in exchange.response_text
    assert exchange.elapsed_ms >= 0


def test_load_config_reads_nokr_web_and_budgets(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "bruno_collection: /tmp/bruno",
                "nokr_web: http://127.0.0.1:8080",
                "nokr_admin: http://127.0.0.1:9090",
                "budgets_ms:",
                "  hot_path:",
                "    budget: 50",
                "    fail: 1500",
                "  default:",
                "    budget: 1500",
                "    fail: 1500",
                "register_gap_ms: 2000",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert isinstance(config, HarnessConfig)
    assert config.nokr_web == "http://127.0.0.1:8080"
    assert config.nokr_admin == "http://127.0.0.1:9090"
    assert config.budgets_ms.hot_path.fail == 1500
    assert config.budgets_ms.hot_path.budget == 50
    assert config.register_gap_ms == 2000
