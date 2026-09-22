from urllib.parse import urlparse
import json

import httpx
import pytest

from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.runner import _pace_register
from nokr_qa.runner import execute_step
from nokr_qa.schema.models import CaseFile
from nokr_qa.schema.models import Contract
from nokr_qa.schema.models import ExpectSpec
from nokr_qa.schema.models import FieldSpec


def _config(tmp_path, **kwargs) -> HarnessConfig:
    web_log = tmp_path / "nokr-web.log"
    worker_log = tmp_path / "nokr-worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    payload = {"log_files": LogFiles(web=str(web_log), worker=str(worker_log))}
    payload.update(kwargs)
    return HarnessConfig(**payload)


def test_register_gap_sleeps_on_second_call_only():
    slept: list[float] = []
    times = iter([0.0, 0.01, 0.09])
    pacer: dict[str, float] = {}
    config = HarnessConfig(register_gap_ms=80)
    url = "http://127.0.0.1:8080/auth/register"
    _pace_register(url, config, pacer, sleeper=slept.append, clock=lambda: next(times))
    _pace_register(url, config, pacer, sleeper=slept.append, clock=lambda: next(times))
    assert slept == pytest.approx([0.07])


def test_register_gap_skips_ingest_and_zero_gap():
    slept: list[float] = []
    pacer: dict[str, float] = {}
    _pace_register(
        "http://127.0.0.1:8080/api/ingest",
        HarnessConfig(register_gap_ms=80),
        pacer,
        sleeper=slept.append,
        clock=lambda: 0.0,
    )
    _pace_register(
        "http://127.0.0.1:8080/auth/register",
        HarnessConfig(register_gap_ms=0),
        pacer,
        sleeper=slept.append,
        clock=lambda: 0.0,
    )
    assert slept == []


def test_register_burst_sends_n_times(tmp_path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        status = 429 if len(captured) >= 4 else 201
        return httpx.Response(status, json={"error": "Too many requests. Please try again later."} if status == 429 else {"id": "u1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    case = CaseFile.model_validate(
        {
            "id": "register-N-rule-RATE_LIMIT",
            "contract": "contracts/auth-register.yaml",
            "kind": "N-rule-RATE_LIMIT",
            "expect": ExpectSpec(status=429),
            "burst": 4,
        }
    )
    contract = Contract(
        endpoint="POST /auth/register",
        dto="com.nokr.domain.auth.dto.RegisterRequest",
        auth="none",
        idempotency="none",
        baseline="baselines/auth-register.json",
        fields={"email": FieldSpec(required=True, json="email")},
    )
    execute_step(
        case=case,
        contract=contract,
        baseline={"email": "a@b.c", "password": "Str0ng!Pass123"},
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="register",
    )
    assert len(captured) == 4
    assert urlparse(str(captured[0].url)).path == "/auth/register"


def test_saturate_stops_on_until_status_and_uniquifies_json(tmp_path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        status = 409 if len(captured) >= 3 else 201
        body = (
            {"error": "You already have 5 active sandbox keys.", "traceId": "t"}
            if status == 409
            else {"id": f"k{len(captured)}", "raw_key": "nk_test_x"}
        )
        return httpx.Response(
            status,
            json=body,
            headers={"X-Trace-Id": request.headers.get("x-trace-id", "t")},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    case = CaseFile.model_validate(
        {
            "id": "api-keys-post-N-rule-SANDBOX_LIMIT",
            "contract": "contracts/platform-api-keys-post.yaml",
            "kind": "N-rule-SANDBOX_LIMIT",
            "expect": ExpectSpec(status=409),
            "saturate": {"until_status": 409, "max": 6, "unique_json": "name"},
        }
    )
    contract = Contract(
        endpoint="POST /platform/api-keys",
        dto="com.nokr.domain.auth.dto.CreateApiKeyRequest",
        auth="none",
        idempotency="none",
        baseline="baselines/api-keys-post.json",
        fields={"name": FieldSpec(required=True, json="name")},
    )
    result = execute_step(
        case=case,
        contract=contract,
        baseline={"name": "lab-2", "environment": "SANDBOX"},
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="keys",
    )
    assert len(captured) == 3
    names = [json.loads(request.content)["name"] for request in captured]
    assert result.status_code == 409
    assert all(name.startswith("lab-2_") for name in names)
    assert len(set(names)) == 3
