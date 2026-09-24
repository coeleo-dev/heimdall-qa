from pathlib import Path

from heimdall_qa.autofill import mechanical_diff
from heimdall_qa.autofill import mechanical_payload
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.descriptor import ProjectDescriptor
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import FieldSpec
from heimdall_qa.testing import project_at

_PROJECT = project_at(
    Path(__file__).resolve().parent / "fixtures" / "qa" / "project.yaml"
)


def _register_contract() -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "POST /auth/register",
            "dto": "com.example.domain.auth.dto.RegisterRequest",
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
        dto="com.example.domain.auth.dto.RegisterRequest",
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
    rate = mechanical_payload("N-rule-RATE_LIMIT", contract, 201, _PROJECT)
    assert rate["burst"] == 4
    assert "saturate" not in rate
    limit = mechanical_payload("N-rule-SANDBOX_LIMIT", contract, 201, _PROJECT)
    assert limit["saturate"] == {
        "until_status": 409,
        "max": 6,
        "unique_json": "name",
    }
    assert "burst" not in limit


def test_e_isolate_expects_403():
    contract = Contract(
        endpoint="GET /platform/api-keys",
        dto="com.example.domain.auth.controller.B2BApiKeyController",
        auth="jwt",
        baseline="baselines/empty.json",
        fields={},
    )
    payload = mechanical_payload("E-isolate", contract, 200, _PROJECT)
    assert payload["expect"] == {"status": 403}
    assert payload["headers"]["X-Environment"] == "production"


def test_e_conflict_expects_400():
    contract = Contract(
        endpoint="GET /api/users/{{external_user_id}}",
        dto="com.example.domain.user.controller.UserController",
        auth="api_key",
        baseline="baselines/empty.json",
        fields={},
        p_gaps=["P-GAP-6"],
    )
    payload = mechanical_payload("E-conflict", contract, 200, _PROJECT)
    assert payload["expect"] == {"status": 400}
    assert payload["headers"]["X-Environment"] == "production"


def test_h01_emits_contract_captures():
    contract = Contract(
        endpoint="POST /platform/billable-metrics",
        dto="com.example.domain.metering.catalog.dto.CreateBillableMetricRequest",
        auth="jwt",
        baseline="baselines/billable-metrics-post.json",
        fields={"name": FieldSpec(required=True, json="name")},
        captures={"billable_metric_id": "id"},
        unique_json="name",
    )
    payload = mechanical_payload("H01", contract, 201, _PROJECT)
    assert payload["capture_response"] == {"billable_metric_id": "id"}
    omit = mechanical_payload("N-omit-name", contract, 201, _PROJECT)
    assert "capture_response" not in omit


def _metrics_contract() -> Contract:
    return Contract.model_validate(
        {
            "endpoint": "POST /platform/billable-metrics",
            "dto": "com.example.domain.metering.catalog.dto.CreateBillableMetricRequest",
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
            "dto": "com.example.domain.metering.rating.dto.CreateRateCardRequest",
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
            "dto": "com.example.domain.metering.ingest.dto.IngestRequest",
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


# ── the `errors` block decides the generated status (fase 2.1a) ───────────────


def _view(statuses: dict[str, int]) -> ProjectView:
    descriptor = ProjectDescriptor.model_validate(
        {
            "version": 1,
            "project": {"id": "demo"},
            "environments": {"local": {"base_url": "http://localhost:9000"}},
            "environment_header": {
                "name": "X-Environment",
                "values": {"local": "local", "other": "other"},
                "isolation": "other",
            },
            "errors": {"statuses": statuses},
        }
    )
    return ProjectView(descriptor=descriptor)


def _view_with_auth(auth: dict) -> ProjectView:
    """A view whose descriptor declares credentials, for the `N-auth` cases."""
    descriptor = ProjectDescriptor.model_validate(
        {
            "version": 1,
            "project": {"id": "demo"},
            "environments": {"local": {"base_url": "http://localhost:9000"}},
            "auth": auth,
        }
    )
    return ProjectView(descriptor=descriptor)


def test_the_default_validation_status_is_400():
    """The status used to be a literal in this module; now it comes from the project."""
    payload = mechanical_payload("N-omit-email", _register_contract(), 201, ProjectView())
    assert payload["expect"] == {"status": 400}


def test_a_declared_validation_status_reaches_the_generated_case():
    payload = mechanical_payload(
        "N-omit-email", _register_contract(), 201, _view({"validation": 422})
    )
    assert payload["expect"] == {"status": 422}


def test_a_declared_axis_moves_only_that_axis():
    project = _view({"validation": 422})
    contract = _register_contract()
    assert mechanical_payload("N-auth", contract, 201, project)["expect"] == {"status": 401}
    assert mechanical_payload("I-missing", contract, 201, project)["expect"] == {"status": 400}
    assert mechanical_payload("E-isolate", contract, 200, project)["expect"] == {"status": 403}


def test_a_product_rule_status_comes_from_the_rule_not_the_descriptor():
    """One API answers 400 for one rule and 422 for the next, so `rules[].status` wins."""
    payload = mechanical_payload(
        "N-rule-RATE_LIMIT", _register_contract(), 201, _view({"validation": 422})
    )
    assert payload["expect"]["status"] == 429


def test_n_auth_omits_the_header_the_descriptor_declares():
    """`Authorization` was a literal here until a target put its token elsewhere.

    The header belongs to the scheme the contract names, which is the same scheme
    the runner attaches the credential from: a case that omitted a different header
    would send a request the API accepts and call it an auth failure.
    """
    project = _view_with_auth(
        {"api_key": {"header": "X-Acme-Token", "scheme": "raw"}}
    )
    contract = Contract.model_validate(
        {
            "endpoint": "POST /v1/things",
            "auth": "api_key",
            "baseline": "baselines/empty.json",
            "fields": {},
        }
    )
    payload = mechanical_payload("N-auth", contract, 201, project)
    assert payload["omit_headers"] == ["X-Acme-Token"]


def test_n_auth_falls_back_to_authorization_when_the_contract_names_no_scheme():
    """A contract whose `auth` the descriptor does not declare is a broken pair.

    Sending nothing at all would make the case pass for the wrong reason, so the
    harness keeps its own default and lets the request fail where it will.
    """
    contract = Contract.model_validate(
        {
            "endpoint": "POST /v1/things",
            "auth": "none",
            "baseline": "baselines/empty.json",
            "fields": {},
        }
    )
    assert mechanical_payload("N-auth", contract, 201, ProjectView())["omit_headers"] == [
        "Authorization"
    ]


# ── the headers a case adds, which nothing exercised until the fixture ───────


def test_i_format_sends_the_key_it_promises_is_malformed():
    """`I-format` expects 400, so it has to send something that can answer 400.

    This arm was unreachable — the environment arm above it returned early — and
    the case still carried its 400 because the *expect* came from the same
    module. A generated case whose request contradicts its own expectation is the
    one kind of green a generator must never produce, and the reference corpus
    could not see it: those files were written by hand, with the header.
    """
    payload = mechanical_payload("I-format", _register_contract(), 201, _PROJECT)

    assert payload["headers"] == {"X-Idempotency-Key": "not-a-uuid-v4"}
    assert payload["expect"] == {"status": 400}


def test_a_rule_that_fails_on_a_header_is_sent_that_header():
    contract = Contract.model_validate(
        {
            "endpoint": "POST /v1/things",
            "auth": "none",
            "baseline": "baselines/empty.json",
            "fields": {},
            "rules": [
                {
                    "id": "TENANT_MISMATCH",
                    "status": 403,
                    "error": "the tenant does not own this",
                    "headers": {"X-Tenant": "another-tenant"},
                }
            ],
        }
    )
    payload = mechanical_payload("N-rule-TENANT_MISMATCH", contract, 201, _PROJECT)

    assert payload["headers"] == {"X-Tenant": "another-tenant"}


def test_a_live_only_rule_that_fails_on_a_header_is_sent_that_header():
    contract = Contract.model_validate(
        {
            "endpoint": "POST /v1/things",
            "auth": "none",
            "baseline": "baselines/empty.json",
            "fields": {},
            "live_only_rules": [
                {
                    "id": "PROD_ONLY",
                    "status": 402,
                    "headers": {"X-Environment": "production"},
                }
            ],
        }
    )
    payload = mechanical_payload("P-PROD_ONLY", contract, 201, _PROJECT)

    assert payload["headers"] == {"X-Environment": "production"}


def test_the_environment_header_still_wins_for_the_environment_kinds():
    """The arm that used to swallow every other one has to keep its own case."""
    contract = Contract(
        endpoint="GET /platform/api-keys",
        auth="jwt",
        baseline="baselines/empty.json",
        fields={},
    )
    payload = mechanical_payload("E-isolate", contract, 200, _PROJECT)

    assert payload["headers"] == {"X-Environment": "production"}
