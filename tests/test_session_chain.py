import json
from pathlib import Path

import httpx
import pytest

from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import LogFiles
from heimdall_qa.errors import HarnessError
from heimdall_qa.runner import _should_uniquify
from heimdall_qa.runner import _uniquify_json
from heimdall_qa.runner import execute_step
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import ExpectSpec
from heimdall_qa.schema.models import FieldSpec


def _config(tmp_path: Path) -> HarnessConfig:
    web_log = tmp_path / "nokr-web.log"
    worker_log = tmp_path / "nokr-worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    return HarnessConfig(log_files=LogFiles(web=str(web_log), worker=str(worker_log)))


def _case(**kwargs) -> CaseFile:
    payload = {
        "id": "metrics-H01",
        "contract": "contracts/platform-billable-metrics-post.yaml",
        "kind": "H01",
        "expect": ExpectSpec(status=201),
    }
    payload.update(kwargs)
    return CaseFile.model_validate(payload)


def _post_contract(**kwargs) -> Contract:
    payload = {
        "endpoint": "POST /platform/billable-metrics",
        "dto": "com.nokr.domain.metering.catalog.dto.CreateBillableMetricRequest",
        "auth": "jwt",
        "idempotency": "header_uuid_v4",
        "baseline": "baselines/billable-metrics-post.json",
        "fields": {"name": FieldSpec(required=True, json="name")},
        "unique_json": "name",
        "captures": {"billable_metric_id": "id"},
    }
    payload.update(kwargs)
    return Contract.model_validate(payload)


def _put_contract() -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "PUT /platform/billable-metrics/{{billable_metric_id}}",
            "dto": "com.nokr.domain.metering.catalog.dto.UpdateBillableMetricRequest",
            "auth": "jwt",
            "idempotency": "header_uuid_v4",
            "baseline": "baselines/billable-metrics-put.json",
            "fields": {"name": FieldSpec(required=True, json="name")},
            "resource_id_in_path": True,
        }
    )


def _run(
    tmp_path: Path,
    *,
    case: CaseFile,
    contract: Contract,
    baseline: dict,
    captured: list[httpx.Request],
    captures: dict[str, str],
    status: int = 201,
    body: dict | None = None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        trace = request.headers.get("x-trace-id", "missing")
        payload = body if body is not None else {"id": "metric-1"}
        return httpx.Response(
            status,
            json=payload,
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=case,
        contract=contract,
        baseline=dict(baseline),
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=len(captured) + 1,
        run_id="session",
        secrets={"jwt": "header.payload.sig"},
        captures=captures,
    )


def _header(request: httpx.Request, name: str) -> str | None:
    wanted = name.lower()
    for key, value in request.headers.items():
        if key.lower() == wanted:
            return value
    return None


def test_url_interpolates_capture(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {"billable_metric_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}
    _run(
        tmp_path,
        case=_case(id="metrics-put-H01", kind="H01", expect=ExpectSpec(status=200)),
        contract=_put_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures=store,
        status=200,
        body={"id": store["billable_metric_id"]},
    )
    assert store["billable_metric_id"] in str(captured[0].url)


def test_body_interpolates_capture(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {"billable_metric_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}
    _run(
        tmp_path,
        case=_case(id="rate-cards-H01", kind="H01"),
        contract=_post_contract(unique_json=None, captures={}),
        baseline={"billable_metric_id": "{{billable_metric_id}}", "name": "flat"},
        captured=captured,
        captures=store,
    )
    payload = json.loads(captured[0].content.decode("utf-8"))
    assert payload["billable_metric_id"] == store["billable_metric_id"]


def test_unresolved_url_placeholder_fails(tmp_path: Path):
    captured: list[httpx.Request] = []
    with pytest.raises(HarnessError) as err:
        _run(
            tmp_path,
            case=_case(id="metrics-put-H01"),
            contract=_put_contract(),
            baseline={"name": "llm tokens"},
            captured=captured,
            captures={},
        )
    assert err.value.code == "PLACEHOLDER_UNRESOLVED"
    assert captured == []


def test_i_replay_reuses_last_idempotency_key(tmp_path: Path):
    captured: list[httpx.Request] = []
    store: dict[str, str] = {}
    _run(
        tmp_path,
        case=_case(),
        contract=_post_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures=store,
    )
    first_key = _header(captured[0], "X-Idempotency-Key")
    assert first_key
    assert store["last_idempotency_key"] == first_key
    _run(
        tmp_path,
        case=_case(id="metrics-I-replay", kind="I-replay"),
        contract=_post_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures=store,
    )
    assert _header(captured[1], "X-Idempotency-Key") == first_key
    first_name = json.loads(captured[0].content.decode("utf-8"))["name"]
    replay_name = json.loads(captured[1].content.decode("utf-8"))["name"]
    assert first_name == replay_name
    assert first_name != "llm tokens"


def test_i_replay_without_prior_key_fails(tmp_path: Path):
    with pytest.raises(HarnessError) as err:
        _run(
            tmp_path,
            case=_case(id="metrics-I-replay", kind="I-replay"),
            contract=_post_contract(),
            baseline={"name": "llm tokens"},
            captured=[],
            captures={},
        )
    assert err.value.code == "CAPTURE_MISSING"
    assert "last_idempotency_key" in err.value.message


def test_i_new_key_uniquifies_name(tmp_path: Path):
    captured: list[httpx.Request] = []
    store: dict[str, str] = {}
    _run(
        tmp_path,
        case=_case(),
        contract=_post_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures=store,
    )
    _run(
        tmp_path,
        case=_case(id="metrics-I-new-key", kind="I-new-key"),
        contract=_post_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures=store,
    )
    first = json.loads(captured[0].content.decode("utf-8"))["name"]
    second = json.loads(captured[1].content.decode("utf-8"))["name"]
    assert first != second
    assert _header(captured[0], "X-Idempotency-Key") != _header(
        captured[1], "X-Idempotency-Key"
    )


def test_uniquify_feature_key_uses_underscore_charset():
    payload = _uniquify_json({"featureKey": "gpt4o_access"}, "featureKey", 0)
    assert isinstance(payload, dict)
    value = payload["featureKey"]
    assert value != "gpt4o_access"
    assert value.startswith("gpt4o_access_0_")
    assert all(ch.islower() or ch.isdigit() or ch == "_" for ch in value)


def test_should_not_uniquify_b_max_when_axis_is_unique_json():
    contract = _post_contract(unique_json="name")
    assert _should_uniquify("B-max-name", contract) is False
    assert _should_uniquify("B-max-featureKey", contract) is True
    assert _should_uniquify("H01", contract) is True


def test_should_uniquify_b_max_name_when_unique_json_is_feature_key():
    contract = _post_contract(unique_json="featureKey")
    assert _should_uniquify("B-max-name", contract) is True
    assert _should_uniquify("B-max-featureKey", contract) is False


def test_b_max_name_keeps_exact_name_when_unique_json_is_name(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(
        tmp_path,
        case=_case(id="metrics-B-max-name", kind="B-max-name"),
        contract=_post_contract(),
        baseline={"name": "x" * 50},
        captured=captured,
        captures={},
    )
    payload = json.loads(captured[0].content.decode("utf-8"))
    assert payload["name"] == "x" * 50


def test_b_max_name_uniquifies_feature_key(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(
        tmp_path,
        case=_case(id="keys-B-max-name", kind="B-max-name"),
        contract=_post_contract(unique_json="featureKey"),
        baseline={"featureKey": "gpt4o_access", "name": "x" * 50},
        captured=captured,
        captures={},
    )
    payload = json.loads(captured[0].content.decode("utf-8"))
    assert payload["name"] == "x" * 50
    assert payload["featureKey"] != "gpt4o_access"
    assert payload["featureKey"].startswith("gpt4o_access_0_")


def _webhook_patch_contract() -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "PATCH /platform/webhooks/{{webhook_id}}",
            "dto": "com.nokr.domain.notification.outbound.dto.UpdateWebhookRequest",
            "auth": "jwt",
            "baseline": "baselines/webhooks-patch.json",
            "fields": {"url": FieldSpec(required=False, json="url")},
            "resource_id_in_path": True,
        }
    )


def test_n_notfound_does_not_use_session_path_id(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {"webhook_id": "wh_real"}
    _run(
        tmp_path,
        case=_case(
            id="webhooks-patch-N-notfound",
            kind="N-notfound",
            expect=ExpectSpec(status=404),
        ),
        contract=_webhook_patch_contract(),
        baseline={"url": "https://hooks.example/nokr"},
        captured=captured,
        captures=store,
        status=404,
        body={"error": "Webhook not found"},
    )
    assert "wh_real" not in str(captured[0].url)
    assert "/platform/webhooks/" in str(captured[0].url)
    assert store["webhook_id"] == "wh_real"


def test_s_bola_does_not_use_session_path_id(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {"webhook_id": "wh_real"}
    _run(
        tmp_path,
        case=_case(
            id="webhooks-patch-S-bola",
            kind="S-bola",
            expect=ExpectSpec(status=404),
        ),
        contract=_webhook_patch_contract(),
        baseline={"url": "https://hooks.example/nokr"},
        captured=captured,
        captures=store,
        status=404,
        body={"error": "Webhook not found"},
    )
    assert "wh_real" not in str(captured[0].url)


def test_n_notfound_keeps_body_captures_that_are_not_in_path(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {
        "webhook_id": "wh_real",
        "billable_metric_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    }
    _run(
        tmp_path,
        case=_case(
            id="webhooks-patch-N-notfound",
            kind="N-notfound",
            expect=ExpectSpec(status=404),
        ),
        contract=_webhook_patch_contract(),
        baseline={"note": "{{billable_metric_id}}"},
        captured=captured,
        captures=store,
        status=404,
        body={"error": "Webhook not found"},
    )
    payload = json.loads(captured[0].content.decode("utf-8"))
    assert payload["note"] == store["billable_metric_id"]
    assert "wh_real" not in str(captured[0].url)


def test_should_not_uniquify_n_rule_when_set_is_unique_json():
    contract = _post_contract(
        unique_json="name",
        rules=[{"id": "NAME_TOO_SHORT", "status": 400, "error": "size", "set": {"name": "ab"}}],
    )
    assert _should_uniquify("N-rule-NAME_TOO_SHORT", contract) is False
    assert _should_uniquify("N-rule-COUNT_WITH_PATH", contract) is True


def test_path_values_override_url_id(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {
        "billable_metric_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "active_rate_card_id": "bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    }
    _run(
        tmp_path,
        case=_case(
            id="metrics-put-N-rule-ACTIVE_CARD",
            kind="N-rule-ACTIVE_CARD",
            expect=ExpectSpec(status=422),
            path_values={"billable_metric_id": "{{active_rate_card_id}}"},
        ),
        contract=_put_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures=store,
        status=422,
        body={"error": "PUT is only allowed before active_from"},
    )
    assert store["active_rate_card_id"] in str(captured[0].url)
    assert store["billable_metric_id"] not in str(captured[0].url)


def test_should_not_uniquify_o_omit_of_unique_json_field():
    contract = _post_contract(unique_json="external_user_id")
    assert _should_uniquify("O-omit-external_user_id", contract) is False
    assert _should_uniquify("O-set-external_user_id", contract) is True
    assert _should_uniquify("O-omit-plan_id", contract) is True


def test_should_uniquify_n_rule_when_unique_json_exists():
    contract = _post_contract(unique_json="name")
    assert _should_uniquify("N-rule-COUNT_WITH_PATH", contract) is True
    assert (
        _should_uniquify(
            "N-rule-DUPLICATE_NAME",
            contract,
            generate={"name": "captured.last_unique_json"},
        )
        is False
    )


def test_n_rule_uniquifies_name(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(
        tmp_path,
        case=_case(
            id="metrics-N-rule-COUNT_WITH_PATH",
            kind="N-rule-COUNT_WITH_PATH",
            expect=ExpectSpec(status=422),
        ),
        contract=_post_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures={},
        status=422,
        body={"error": "COUNT must not have property_path"},
    )
    payload = json.loads(captured[0].content.decode("utf-8"))
    assert payload["name"] != "llm tokens"
    assert payload["name"].startswith("llm tokens_0_")


def test_n_rule_skips_uniquify_when_generate_sets_unique_field(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {"last_unique_json": "llm tokens_0_deadbeef"}
    _run(
        tmp_path,
        case=_case(
            id="metrics-N-rule-DUPLICATE_NAME",
            kind="N-rule-DUPLICATE_NAME",
            generate={"name": "captured.last_unique_json"},
            expect=ExpectSpec(status=422),
        ),
        contract=_post_contract(),
        baseline={"name": "llm tokens"},
        captured=captured,
        captures=store,
        status=422,
        body={"error": "Billable metric already exists"},
    )
    payload = json.loads(captured[0].content.decode("utf-8"))
    assert payload["name"] == "llm tokens_0_deadbeef"
