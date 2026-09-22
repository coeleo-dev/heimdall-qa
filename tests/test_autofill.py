from pathlib import Path

from heimdall_qa.autofill import mechanical_diff
from heimdall_qa.autofill import mechanical_payload
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import FieldSpec


def _register_contract() -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "POST /auth/register",
            "dto": "com.nokr.domain.auth.dto.RegisterRequest",
            "auth": "none",
            "baseline": "baselines/auth-register.json",
            "fields": {
                "password": {
                    "required": True,
                    "json": "password",
                    "max_length": 64,
                    "example": "Aa1xxxxx",
                },
                "email": {"required": True, "json": "email"},
            },
            "rules": [
                {
                    "id": "RATE_LIMIT",
                    "status": 429,
                    "error": "Too many requests",
                    "burst": 4,
                },
                {
                    "id": "SANDBOX_LIMIT",
                    "status": 409,
                    "error": "already have",
                    "saturate": {
                        "until_status": 409,
                        "max": 6,
                        "unique_json": "name",
                    },
                },
            ],
        }
    )


def test_n_over_password_repeats_example_to_max_plus_one():
    value = mechanical_diff("N-over-password", _register_contract())["set"]["password"]
    assert len(value) == 65
    assert value.startswith("Aa1xxxxx")
    assert any(char.isupper() for char in value)
    assert any(char.isdigit() for char in value)


def test_n_over_password_without_example_uses_strong_seed():
    contract = Contract(
        endpoint="POST /auth/register",
        dto="com.nokr.domain.auth.dto.RegisterRequest",
        baseline="baselines/auth-register.json",
        fields={
            "password": FieldSpec(required=True, json="password", max_length=64),
        },
    )
    value = mechanical_diff("N-over-password", contract)["set"]["password"]
    assert len(value) == 65
    assert any(char.isupper() for char in value)
    assert any(char.isdigit() for char in value)


def test_b_max_password_uses_example_at_max_length():
    value = mechanical_diff("B-max-password", _register_contract())["set"]["password"]
    assert len(value) == 64
    assert value.startswith("Aa1xxxxx")


def test_n_rule_payload_copies_burst_and_saturate():
    contract = _register_contract()
    rate = mechanical_payload("N-rule-RATE_LIMIT", contract, 201)
    assert rate["burst"] == 4
    assert "saturate" not in rate
    limit = mechanical_payload("N-rule-SANDBOX_LIMIT", contract, 201)
    assert limit["saturate"] == {
        "until_status": 409,
        "max": 6,
        "unique_json": "name",
    }
    assert "burst" not in limit


def test_e_isolate_expects_403():
    contract = Contract(
        endpoint="GET /platform/api-keys",
        dto="com.nokr.domain.auth.controller.B2BApiKeyController",
        auth="jwt",
        baseline="baselines/empty.json",
        fields={},
    )
    payload = mechanical_payload("E-isolate", contract, 200)
    assert payload["expect"] == {"status": 403}
    assert payload["headers"]["X-Nokr-Environment"] == "production"


def test_e_conflict_expects_400():
    contract = Contract(
        endpoint="GET /api/users/{{external_user_id}}",
        dto="com.nokr.domain.user.controller.UserController",
        auth="api_key",
        baseline="baselines/empty.json",
        fields={},
        p_gaps=["P-GAP-6"],
    )
    payload = mechanical_payload("E-conflict", contract, 200)
    assert payload["expect"] == {"status": 400}
    assert payload["headers"]["X-Nokr-Environment"] == "production"


def test_h01_emits_contract_captures():
    contract = Contract(
        endpoint="POST /platform/billable-metrics",
        dto="com.nokr.domain.metering.catalog.dto.CreateBillableMetricRequest",
        auth="jwt",
        baseline="baselines/billable-metrics-post.json",
        fields={"name": FieldSpec(required=True, json="name")},
        captures={"billable_metric_id": "id"},
        unique_json="name",
    )
    payload = mechanical_payload("H01", contract, 201)
    assert payload["capture_response"] == {"billable_metric_id": "id"}
    omit = mechanical_payload("N-omit-name", contract, 201)
    assert "capture_response" not in omit


def _metrics_contract() -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "POST /platform/billable-metrics",
            "dto": "com.nokr.domain.metering.catalog.dto.CreateBillableMetricRequest",
            "auth": "jwt",
            "baseline": "baselines/billable-metrics-post.json",
            "fields": {
                "allowed_properties": {
                    "required": False,
                    "json": "allowed_properties",
                    "max_keys": 32,
                }
            },
        }
    )


def _rate_card_contract() -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "POST /platform/rate-cards",
            "dto": "com.nokr.domain.metering.rating.dto.CreateRateCardRequest",
            "auth": "jwt",
            "baseline": "baselines/rate-cards-post.json",
            "fields": {
                "dimension_keys": {
                    "required": False,
                    "json": "dimension_keys",
                    "max_keys": 8,
                }
            },
        }
    )


def test_n_over_allowed_properties_keys_emits_max_plus_one_list():
    payload = mechanical_diff("N-over-allowed_properties-keys", _metrics_contract())
    keys = payload["set"]["allowed_properties"]
    assert isinstance(keys, list)
    assert len(keys) == 33


def test_b_max_allowed_properties_keys_emits_max_list():
    payload = mechanical_diff("B-max-allowed_properties-keys", _metrics_contract())
    keys = payload["set"]["allowed_properties"]
    assert isinstance(keys, list)
    assert len(keys) == 32


def test_n_over_dimension_keys_emits_max_plus_one_list():
    payload = mechanical_diff("N-over-dimension_keys-keys", _rate_card_contract())
    keys = payload["set"]["dimension_keys"]
    assert isinstance(keys, list)
    assert len(keys) == 9


def test_n_over_flat_properties_keys_emits_map():
    contract = Contract.model_validate(
        {
            "endpoint": "POST /api/ingest",
            "dto": "com.nokr.domain.metering.ingest.dto.IngestRequest",
            "auth": "api_key",
            "baseline": "baselines/ingest-sum.json",
            "fields": {
                "properties": {
                    "required": True,
                    "json": "properties",
                    "flat": True,
                    "max_keys": 32,
                }
            },
        }
    )
    payload = mechanical_diff("N-over-properties-keys", contract)
    props = payload["set"]["properties"]
    assert isinstance(props, dict)
    assert len(props) == 33
