from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import httpx

from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.runner import _generated
from nokr_qa.runner import execute_step
from nokr_qa.schema.models import CaseFile
from nokr_qa.schema.models import Contract
from nokr_qa.schema.models import ExpectSpec
from nokr_qa.schema.models import FieldSpec

_FIXED_KEY = "550e8400-e29b-41d4-a716-446655440000"
_BASELINE = {
    "transaction_id": "evt-qa-piloto-001",
    "customer_id": "ext-ta-001",
    "event_type": "llm_tokens",
    "timestamp": "2026-09-01T12:00:00Z",
    "properties": {"model": "gpt-4o", "tokens": 100},
}


def _contract() -> Contract:
    return Contract(
        endpoint="POST /api/ingest",
        dto="com.nokr.domain.metering.ingest.dto.IngestRequest",
        auth="api_key",
        idempotency="header_uuid_v4",
        baseline="baselines/ingest-sum.json",
        fields={
            "transaction_id": FieldSpec(required=True, json="transaction_id"),
        },
    )


def _case(**kwargs) -> CaseFile:
    payload = {
        "id": "ingest-H01",
        "contract": "contracts/api-ingest-post.yaml",
        "kind": "H01",
        "expect": ExpectSpec(status=202),
    }
    payload.update(kwargs)
    return CaseFile.model_validate(payload)


def _config(tmp_path: Path) -> HarnessConfig:
    web_log = tmp_path / "nokr-web.log"
    worker_log = tmp_path / "nokr-worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    return HarnessConfig(log_files=LogFiles(web=str(web_log), worker=str(worker_log)))


def _run(tmp_path: Path, case: CaseFile, captured: list[httpx.Request]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        trace = request.headers.get("x-trace-id", "missing")
        return httpx.Response(
            202,
            json={"status": "ACCEPTED"},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=case,
        contract=_contract(),
        baseline=_BASELINE,
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="piloto",
        secrets={"api_key": "nk_test_secret"},
    )


def _header(request: httpx.Request, name: str) -> str | None:
    wanted = name.lower()
    for key, value in request.headers.items():
        if key.lower() == wanted:
            return value
    return None


def test_omit_idempotency_does_not_send_header(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(
        tmp_path,
        _case(id="ingest-I-missing", kind="I-missing", omit_headers=["X-Idempotency-Key"]),
        captured,
    )
    assert captured
    assert _header(captured[0], "X-Idempotency-Key") is None


def test_omit_authorization_does_not_send_bearer(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(
        tmp_path,
        _case(id="ingest-N-auth", kind="N-auth", omit_headers=["Authorization"]),
        captured,
    )
    assert captured
    assert _header(captured[0], "Authorization") is None


def test_case_headers_keep_invalid_idempotency_key(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(
        tmp_path,
        _case(
            id="ingest-I-format",
            kind="I-format",
            headers={"X-Idempotency-Key": "abc"},
        ),
        captured,
    )
    assert _header(captured[0], "X-Idempotency-Key") == "abc"


def test_case_headers_send_environment_conflict(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(
        tmp_path,
        _case(
            id="ingest-E-conflict",
            kind="E-conflict",
            headers={
                "X-Idempotency-Key": _FIXED_KEY,
                "X-Nokr-Environment": "production",
            },
        ),
        captured,
    )
    assert _header(captured[0], "X-Nokr-Environment") == "production"
    assert _header(captured[0], "Authorization") == "Bearer nk_test_secret"


def test_generated_now_iso_plus_4m_is_about_four_minutes_ahead():
    before = datetime.now(timezone.utc)
    raw = _generated("now_iso_plus_4m")
    parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    after = datetime.now(timezone.utc)
    expected_min = before + timedelta(minutes=4) - timedelta(seconds=2)
    expected_max = after + timedelta(minutes=4) + timedelta(seconds=2)
    assert expected_min <= parsed <= expected_max
