import json
from pathlib import Path

from heimdall_qa.diff import apply_diff

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASELINE = json.loads(
    (FIXTURES / "baselines" / "ingest-sum.json").read_text(encoding="utf-8")
)


def test_omit_timestamp_removes_key():
    body = apply_diff(BASELINE, {"omit": ["timestamp"]})
    assert "timestamp" not in body
    assert body["transaction_id"] == "evt-ta-001"
    assert BASELINE["timestamp"] == "2026-09-01T12:00:00Z"


def test_set_overwrites_field():
    body = apply_diff(BASELINE, {"set": {"event_type": "bad_type"}})
    assert body["event_type"] == "bad_type"
    assert BASELINE["event_type"] == "llm_tokens"


def test_omit_dotted_path_removes_nested_key():
    body = apply_diff(BASELINE, {"omit": ["properties.tokens"]})
    assert "tokens" not in body["properties"]
    assert body["properties"]["model"] == "gpt-4o"
