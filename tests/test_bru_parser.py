from pathlib import Path

from heimdall_qa.bru_parser import parse_bru

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bru" / "post-ingest.bru"


def test_parse_bru_method_url_headers_ignores_scripts():
    parsed = parse_bru(FIXTURE)
    assert parsed.method == "POST"
    assert parsed.url == "{{base_url}}/api/ingest"
    assert parsed.headers["Authorization"] == "Bearer {{api_key}}"
    assert parsed.headers["Content-Type"] == "application/json"
    assert parsed.body == {
        "transaction_id": "{{ingest_transaction_id}}",
        "event_type": "llm_tokens",
    }
    assert "Date.now" not in str(parsed.body)
    assert "bru.setVar" not in str(parsed.headers)
