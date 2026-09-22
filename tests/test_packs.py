import json

from heimdall_qa.packs import CatalogRule
from heimdall_qa.packs import PackContext
from heimdall_qa.packs import PackResult
from heimdall_qa.packs import run_all


def _by_id(results: list[PackResult]) -> dict[str, PackResult]:
    return {item.pack_id: item for item in results}


def _ctx(**overrides: object) -> PackContext:
    data: dict[str, object] = {
        "method": "POST",
        "url_path": "/api/ingest",
        "request_headers": {
            "Authorization": "Bearer nk_test_fixture",
            "X-Trace-Id": "nokrqa-phase4",
            "X-Idempotency-Key": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        },
        "request_body": {"event_type": "llm_tokens"},
        "status_code": 202,
        "response_headers": {"x-trace-id": "nokrqa-phase4", "content-type": "application/json"},
        "response_text": json.dumps({"status": "ACCEPTED"}),
        "elapsed_ms": 12.0,
        "expect_status": 202,
        "expect_code": None,
        "trace_sent": "nokrqa-phase4",
        "environment": "sandbox",
        "idempotency_required": True,
        "waives": [],
        "dimensions": [],
        "budget_ms": 50,
        "fail_ms": 1500,
        "case_kind": "H01",
        "business_rules": [],
    }
    data.update(overrides)
    return PackContext(**data)  # type: ignore[arg-type]


def test_status_500_fails_http_baseline_hard():
    results = _by_id(run_all(_ctx(status_code=500, response_text="boom", expect_status=202)))
    assert results["http.baseline"].status == "fail"


def test_200_without_trace_id_fails_http_baseline():
    results = _by_id(
        run_all(
            _ctx(
                status_code=200,
                expect_status=200,
                response_headers={"content-type": "application/json"},
                response_text="{}",
            )
        )
    )
    assert results["http.baseline"].status == "fail"


def test_hibernate_in_body_fails_security_leak():
    results = _by_id(
        run_all(
            _ctx(
                status_code=200,
                expect_status=200,
                response_text="org.hibernate.exception.SQLGrammarException",
            )
        )
    )
    assert results["security.leak"].status == "fail"


def test_issued_raw_key_on_create_key_does_not_fail_security_leak():
    body = json.dumps(
        {
            "id": "e257ce03-c471-4735-bce3-339a095aa94d",
            "name": "lab-2",
            "environment": "SANDBOX",
            "raw_key": "nk_test_viewoncekey",
            "rawKey": "nk_test_viewoncekey",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/platform/api-keys",
                status_code=201,
                expect_status=201,
                request_headers={
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
                    "X-Nokr-Environment": "sandbox",
                    "X-Trace-Id": "nokrqa-phase4",
                },
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
            )
        )
    )
    assert results["security.leak"].status == "pass"


def test_nk_test_in_error_message_still_fails_security_leak():
    results = _by_id(
        run_all(
            _ctx(
                status_code=500,
                expect_status=201,
                response_text=json.dumps({"error": "leaked nk_test_secret"}),
            )
        )
    )
    assert results["security.leak"].status == "fail"


def test_issued_jwt_and_api_key_on_register_do_not_fail_security_leak():
    body = json.dumps(
        {
            "jwt": (
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiIxMjM0In0."
                "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
            "refresh_token": "BLdafboHnyAbp4iWQSl-SYJBhJTYsY8VMYq00-lgQd0",
            "api_key": "nk_test_viewoncekey",
            "tenant_id": "e257ce03-c471-4735-bce3-339a095aa94d",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/auth/register",
                status_code=201,
                expect_status=201,
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=8000,
                budget_ms=8000,
            )
        )
    )
    assert results["security.leak"].status == "pass"


def test_jwt_in_error_message_still_fails_security_leak():
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0In0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    results = _by_id(
        run_all(
            _ctx(
                status_code=500,
                expect_status=201,
                response_text=json.dumps({"error": token}),
            )
        )
    )
    assert results["security.leak"].status == "fail"


def test_400_envelope_aligned_passes_http_error():
    body = json.dumps({"error": "timestamp is required", "traceId": "nokrqa-phase4"})
    results = _by_id(
        run_all(
            _ctx(
                status_code=400,
                expect_status=400,
                response_text=body,
            )
        )
    )
    assert results["http.error"].status == "pass"


def test_422_when_expect_201_fails_http_baseline_without_http_error():
    body = json.dumps(
        {
            "error": "Email already registered",
            "traceId": "nokrqa-phase4",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/auth/register",
                status_code=422,
                expect_status=201,
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=8000,
                budget_ms=8000,
                case_kind="H01",
            )
        )
    )
    assert results["http.baseline"].status == "fail"
    assert "expected 201" in results["http.baseline"].detail
    assert "http.error" not in results
    assert "http.success" not in results


_DUPLICATE = CatalogRule(
    id="DUPLICATE_EMAIL",
    status=422,
    error="Email already registered",
)
_INVALID_EMAIL = CatalogRule(id="INVALID_EMAIL", status=400, code="INVALID_EMAIL")


def test_duplicate_email_on_h01_fails_business_rule():
    body = json.dumps(
        {"error": "Email already registered", "traceId": "nokrqa-phase4"}
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/auth/register",
                status_code=422,
                expect_status=201,
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=8000,
                budget_ms=8000,
                case_kind="H01",
                business_rules=[_DUPLICATE, _INVALID_EMAIL],
            )
        )
    )
    assert results["business.rule"].status == "fail"
    assert "DUPLICATE_EMAIL" in results["business.rule"].detail
    assert "H01" in results["business.rule"].detail


def test_duplicate_email_on_n_rule_passes_business_rule():
    body = json.dumps(
        {"error": "Email already registered", "traceId": "nokrqa-phase4"}
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/auth/register",
                status_code=422,
                expect_status=422,
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=8000,
                budget_ms=8000,
                case_kind="N-rule-DUPLICATE_EMAIL",
                business_rules=[_DUPLICATE, _INVALID_EMAIL],
            )
        )
    )
    assert results["business.rule"].status == "pass"
    assert results["http.error"].status == "pass"


def test_duplicate_email_on_n_omit_fails_business_rule():
    body = json.dumps(
        {"error": "Email already registered", "traceId": "nokrqa-phase4"}
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/auth/register",
                status_code=422,
                expect_status=400,
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=8000,
                budget_ms=8000,
                case_kind="N-omit-email",
                business_rules=[_DUPLICATE, _INVALID_EMAIL],
            )
        )
    )
    assert results["business.rule"].status == "fail"
    assert "DUPLICATE_EMAIL" in results["business.rule"].detail
    assert "N-omit-email" in results["business.rule"].detail


def test_http_success_and_observability_pass_with_web_line():
    line = (
        "2026-09-01 14:30:00 [vt] INFO  c.n.Foo [SANDBOX] - "
        "trace_id: [nokrqa-phase4] - accepted"
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/api/users/ext/balance",
                web_log_lines=[line],
                require_worker_logs=False,
            )
        )
    )
    assert results["http.success"].status == "pass"
    assert results["observability"].status == "pass"


def test_http_success_fails_without_web_log_lines():
    results = _by_id(run_all(_ctx(url_path="/api/users/ext/balance")))
    assert results["http.success"].status == "fail"


def test_http_success_fails_on_web_error_line():
    line = (
        "2026-09-01 14:30:00 [vt] ERROR c.n.Foo [SANDBOX] - "
        "trace_id: [nokrqa-phase4] - boom"
    )
    results = _by_id(run_all(_ctx(web_log_lines=[line], url_path="/api/users/ext/balance")))
    assert results["http.success"].status == "fail"


def test_observability_fails_on_portuguese_token():
    line = (
        "2026-09-01 14:30:00 [vt] INFO  c.n.Foo [SANDBOX] - "
        "trace_id: [nokrqa-phase4] - erro ao persistir"
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/api/users/ext/balance",
                web_log_lines=[line],
                require_worker_logs=False,
            )
        )
    )
    assert results["observability"].status == "fail"


def test_observability_skips_when_worker_logs_missing():
    line = (
        "2026-09-01 14:30:00 [vt] INFO  c.n.Foo [SANDBOX] - "
        "trace_id: [nokrqa-phase4] - accepted"
    )
    results = _by_id(
        run_all(
            _ctx(
                web_log_lines=[line],
                worker_log_lines=[],
                require_worker_logs=True,
                logs_incomplete=True,
            )
        )
    )
    assert results["observability"].status == "skipped"


def test_expect_409_actual_201_skips_http_error():
    results = _by_id(
        run_all(
            _ctx(
                url_path="/platform/api-keys",
                status_code=201,
                expect_status=409,
                request_headers={
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
                    "X-Nokr-Environment": "sandbox",
                    "X-Trace-Id": "nokrqa-phase4",
                },
                response_text=json.dumps({"id": "k1", "raw_key": "nk_test_viewoncekey"}),
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
                case_kind="N-rule-SANDBOX_LIMIT",
            )
        )
    )
    assert results["http.baseline"].status == "fail"
    assert "http.error" not in results


def test_n_auth_platform_skips_auth_surface_and_fails_baseline_without_trace():
    results = _by_id(
        run_all(
            _ctx(
                url_path="/platform/api-keys",
                status_code=401,
                expect_status=401,
                request_headers={"X-Nokr-Environment": "sandbox", "X-Trace-Id": "nokrqa-phase4"},
                response_headers={"content-type": "application/json"},
                response_text=json.dumps(
                    {"error": "Missing JWT in Authorization: Bearer header"}
                ),
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
                case_kind="N-auth",
                omit_headers=["Authorization"],
            )
        )
    )
    assert results["auth.surface"].status == "pass"
    assert results["http.baseline"].status == "fail"
    assert "X-Trace-Id" in results["http.baseline"].detail


def test_platform_h01_without_bearer_fails_auth_surface():
    results = _by_id(
        run_all(
            _ctx(
                url_path="/platform/api-keys",
                status_code=401,
                expect_status=201,
                request_headers={"X-Nokr-Environment": "sandbox", "X-Trace-Id": "nokrqa-phase4"},
                response_headers={"x-trace-id": "nokrqa-phase4", "content-type": "application/json"},
                response_text=json.dumps(
                    {
                        "error": "Missing JWT in Authorization: Bearer header",
                        "traceId": "nokrqa-phase4",
                    }
                ),
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
                case_kind="H01",
            )
        )
    )
    assert results["auth.surface"].status == "fail"


def test_http_error_fails_on_portuguese_bean_validation_message():
    body = json.dumps(
        {
            "error": "tamanho deve ser entre 3 e 50",
            "traceId": "nokrqa-phase4",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/platform/api-keys",
                status_code=400,
                expect_status=400,
                request_headers={
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
                    "X-Nokr-Environment": "sandbox",
                    "X-Trace-Id": "nokrqa-phase4",
                },
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
                case_kind="N-rule-NAME_TOO_SHORT",
            )
        )
    )
    assert results["http.error"].status == "fail"
    assert "Portuguese" in results["http.error"].detail


def test_mutation_passes_on_i_missing_without_uuid():
    results = _by_id(
        run_all(
            _ctx(
                status_code=400,
                expect_status=400,
                request_headers={"X-Trace-Id": "nokrqa-phase4"},
                response_text=json.dumps(
                    {
                        "error": "Required header 'X-Idempotency-Key' is missing",
                        "traceId": "nokrqa-phase4",
                    }
                ),
                case_kind="I-missing",
                omit_headers=["X-Idempotency-Key"],
            )
        )
    )
    assert results["mutation"].status == "pass"


def test_mutation_passes_on_i_format_without_uuid():
    results = _by_id(
        run_all(
            _ctx(
                status_code=400,
                expect_status=400,
                request_headers={
                    "X-Trace-Id": "nokrqa-phase4",
                    "X-Idempotency-Key": "not-a-uuid-v4",
                },
                response_text=json.dumps(
                    {"error": "X-Idempotency-Key must be UUID v4", "traceId": "nokrqa-phase4"}
                ),
                case_kind="I-format",
            )
        )
    )
    assert results["mutation"].status == "pass"


def test_mutation_still_fails_h01_without_uuid():
    results = _by_id(
        run_all(
            _ctx(
                request_headers={"X-Trace-Id": "nokrqa-phase4"},
                case_kind="H01",
            )
        )
    )
    assert results["mutation"].status == "fail"


def test_list_prefix_nk_test_does_not_fail_security_leak():
    body = json.dumps(
        [
            {
                "id": "e257ce03-c471-4735-bce3-339a095aa94d",
                "name": "lab-2",
                "environment": "SANDBOX",
                "prefix": "nk_test_...17vv",
                "status": "ACTIVE",
            }
        ]
    )
    results = _by_id(
        run_all(
            _ctx(
                method="GET",
                url_path="/platform/api-keys",
                status_code=200,
                expect_status=200,
                request_headers={
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
                    "X-Nokr-Environment": "sandbox",
                    "X-Trace-Id": "nokrqa-phase4",
                },
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
                case_kind="H01",
                web_log_lines=[
                    "2026-09-01 14:30:00 [vt] INFO  c.n.Foo [SANDBOX] - "
                    "trace_id: [nokrqa-phase4] - listed"
                ],
            )
        )
    )
    assert results["security.leak"].status == "pass"


def test_n_omit_events_matching_rule_omit_passes_business_rule():
    events_empty = CatalogRule(
        id="EVENTS_EMPTY",
        status=400,
        error="At least one event type is required",
        omit=["events"],
    )
    body = json.dumps(
        {
            "error": "At least one event type is required",
            "traceId": "nokrqa-phase4",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/platform/webhooks",
                status_code=400,
                expect_status=400,
                request_headers={
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
                    "X-Nokr-Environment": "sandbox",
                    "X-Trace-Id": "nokrqa-phase4",
                },
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
                case_kind="N-omit-events",
                business_rules=[events_empty],
            )
        )
    )
    assert results["business.rule"].status == "pass"


def test_n_pattern_matching_rule_set_passes_business_rule():
    webhook_http = CatalogRule(
        id="WEBHOOK_HTTP",
        status=400,
        error="Webhook URL must use HTTPS protocol",
        set={"webhook_url": "!!!"},
    )
    body = json.dumps(
        {
            "error": "Webhook URL must use HTTPS protocol",
            "traceId": "nokrqa-phase4",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/platform/tenants/settings",
                status_code=400,
                expect_status=400,
                request_headers={
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
                    "X-Nokr-Environment": "sandbox",
                    "X-Trace-Id": "nokrqa-phase4",
                },
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=2000,
                budget_ms=2000,
                idempotency_required=False,
                case_kind="N-pattern-webhook_url",
                business_rules=[webhook_http],
            )
        )
    )
    assert results["business.rule"].status == "pass"


def test_n_omit_password_weak_password_still_fails_business_rule():
    weak = CatalogRule(
        id="WEAK_PASSWORD",
        status=400,
        error="Password must be at least 8 characters",
    )
    body = json.dumps(
        {
            "error": "Password must be at least 8 characters and contain uppercase",
            "traceId": "nokrqa-phase4",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/auth/register",
                status_code=400,
                expect_status=400,
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=8000,
                budget_ms=8000,
                idempotency_required=False,
                case_kind="N-omit-password",
                business_rules=[weak],
            )
        )
    )
    assert results["business.rule"].status == "fail"
    assert "WEAK_PASSWORD" in results["business.rule"].detail


def test_metering_402_status_classifies_business_rule():
    denied = CatalogRule(id="ENTITLEMENT_DENIED", status=402, code="ENTITLEMENT_DENIED")
    body = json.dumps(
        {
            "status": "ENTITLEMENT_DENIED",
            "balance_remaining": None,
            "payment_url": None,
            "reason": "not_enrolled",
        }
    )
    results = _by_id(
        run_all(
            _ctx(
                url_path="/api/metering",
                status_code=402,
                expect_status=402,
                expect_code="ENTITLEMENT_DENIED",
                response_text=body,
                elapsed_ms=12.0,
                fail_ms=8000,
                budget_ms=8000,
                case_kind="N-rule-ENTITLEMENT_DENIED",
                business_rules=[denied],
                idempotency_required=True,
            )
        )
    )
    assert results["business.rule"].status == "pass"
    assert results["http.error"].status == "pass"
