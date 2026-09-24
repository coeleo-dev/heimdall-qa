import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from heimdall_qa.config import HarnessConfig
from heimdall_qa.project import ProjectView
from heimdall_qa.runner import _pace_register
from heimdall_qa.runner import execute_step
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import ExpectSpec
from heimdall_qa.schema.models import FieldSpec
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

_DESCRIPTOR = Path(__file__).resolve().parent / "fixtures" / "qa" / "project.yaml"


def _project(tmp_path: Path) -> ProjectView:
    """The descriptor in force, with this test's own (empty) log files."""
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    return project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))


def _config(tmp_path: Path, **settings) -> HarnessConfig:
    return config_for(_project(tmp_path), **settings)


def test_register_gap_sleeps_on_second_call_only(tmp_path: Path):
    slept: list[float] = []
    times = iter([0.0, 0.01, 0.09])
    pacer: dict[str, float] = {}
    config = _config(tmp_path, pace_gap_ms=80)
    url = "http://127.0.0.1:8080/auth/register"
    _pace_register(url, config, pacer, sleeper=slept.append, clock=lambda: next(times))
    _pace_register(url, config, pacer, sleeper=slept.append, clock=lambda: next(times))
    assert slept == pytest.approx([0.07])


def test_register_gap_skips_ingest_and_zero_gap(tmp_path: Path):
    slept: list[float] = []
    pacer: dict[str, float] = {}
    _pace_register(
        "http://127.0.0.1:8080/api/ingest",
        _config(tmp_path, pace_gap_ms=80),
        pacer,
        sleeper=slept.append,
        clock=lambda: 0.0,
    )
    _pace_register(
        "http://127.0.0.1:8080/auth/register",
        _config(tmp_path, pace_gap_ms=0),
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
        dto="com.example.domain.auth.dto.RegisterRequest",
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
            else {"id": f"k{len(captured)}", "raw_key": "test_key_x"}
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
        dto="com.example.domain.auth.dto.CreateApiKeyRequest",
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
