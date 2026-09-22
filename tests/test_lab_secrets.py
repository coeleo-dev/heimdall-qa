import json
from pathlib import Path

import httpx
import pytest

from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import LogFiles
from heimdall_qa.errors import HarnessError
from heimdall_qa.runner import _auto_verdict
from heimdall_qa.runner import _prepare_case
from heimdall_qa.runner import execute_step
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_contract
from heimdall_qa.session import RoundSession

ROOT = Path(__file__).resolve().parents[1]
USERS_POST = ROOT / "rounds" / "api-users-post.yaml"
FREEZE_H01 = ROOT / "cases" / "users-freeze" / "users-freeze-H01.yaml"
SETUP_FROZEN = ROOT / "cases" / "api-users-post" / "api-users-post-H-setup-frozen.yaml"
KEYS_H01 = ROOT / "cases" / "api-keys-post" / "api-keys-post-H01.yaml"
DELETE_CONTRACT = ROOT / "contracts" / "platform-api-keys-delete.yaml"
KEYS_POST_ROUND = ROOT / "rounds" / "platform-api-keys-post.yaml"
DELETE_ROUND = ROOT / "rounds" / "platform-api-keys-delete.yaml"


def _config(tmp_path: Path) -> HarnessConfig:
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    return HarnessConfig(log_files=LogFiles(web=str(web), worker=str(worker)))


def _shared_api_key(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "shared-captures.json").write_text(
        json.dumps(
            {
                "api_key": "nk_test_sessionkey",
                "jwt": "captured-jwt-token",
                "other_api_key": "nk_test_other",
                "api_key_id": "11111111-1111-4111-8111-111111111111",
            }
        ),
        encoding="utf-8",
    )
    return runs


def test_prepare_freeze_case_does_not_require_admin_secret(tmp_path: Path):
    runs = _shared_api_key(tmp_path)
    case = load_case(FREEZE_H01)
    _prepare_case(case, ROOT, {}, runs_dir=runs)


def test_api_users_post_round_constructs_without_admin_secret(tmp_path: Path):
    runs = _shared_api_key(tmp_path)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(201, json={})),
        timeout=10.0,
    )
    RoundSession(
        USERS_POST,
        root=ROOT,
        config=_config(tmp_path),
        client=client,
        runs_dir=runs,
        secrets={},
    )


def test_freeze_h01_skips_without_admin_secret_and_does_not_call_http(tmp_path: Path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    case = load_case(FREEZE_H01)
    contract = load_contract(ROOT / case.contract)
    result = execute_step(
        case=case,
        contract=contract,
        baseline={},
        config=_config(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0),
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="freeze",
        secrets={},
        captures={"frozen_user_id": "22222222-2222-4222-8222-222222222222"},
    )
    assert calls == []
    assert result.skipped is True
    assert "admin_secret" in result.skip_reason
    verdict = _auto_verdict(case, result)
    assert verdict["status"] == "skip"
    assert verdict["cause"] == "lab"


def test_setup_frozen_skips_without_admin_secret(tmp_path: Path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"nokr_user_id": "u1"})

    case = load_case(SETUP_FROZEN)
    contract = load_contract(ROOT / case.contract)
    result = execute_step(
        case=case,
        contract=contract,
        baseline={"kyc_profile": {}},
        config=_config(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0),
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="users",
        secrets={},
        captures={"api_key": "nk_test_sessionkey"},
    )
    assert calls == []
    assert result.skipped is True


def test_api_key_secret_missing_hint_mentions_shared_captures(tmp_path: Path):
    case = load_case(KEYS_H01)
    with pytest.raises(HarnessError) as caught:
        _prepare_case(case, ROOT, {}, runs_dir=tmp_path / "empty-runs")
    assert caught.value.code == "SECRET_MISSING"
    assert "jwt" in caught.value.hint.lower() or "api_key" in caught.value.hint.lower()
    assert "admin :9090" not in caught.value.hint


def test_delete_round_revokes_sacrificial_key_not_session_api_key():
    delete = DELETE_CONTRACT.read_text(encoding="utf-8")
    assert "{{revocable_api_key_id}}" in delete
    assert "{{api_key_id}}" not in delete
    post_round = KEYS_POST_ROUND.read_text(encoding="utf-8")
    assert "api-keys-post-H-setup-revocable.yaml" in post_round
    setup = load_case(
        ROOT / "cases" / "api-keys-post" / "api-keys-post-H-setup-revocable.yaml"
    )
    assert setup.capture_response == {"revocable_api_key_id": "id"}
    assert "api_key" not in (setup.capture_response or {})
    delete_round = DELETE_ROUND.read_text(encoding="utf-8")
    assert "api-keys-post-H01.yaml" not in delete_round
