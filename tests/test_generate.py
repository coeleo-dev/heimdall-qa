import json
from pathlib import Path

import httpx
import pytest

from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.runner import _generated
from heimdall_qa.runner import execute_step
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.schema.models import ExpectSpec
from heimdall_qa.schema.models import FieldSpec
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

_BASELINE = {
    "email": "qa-campaign-a@example.dev",
    "password": "Str0ng!Pass123",
    "company_name": "Acme Corp LTDA",
    "document_number": "11222333000181",
    "document_type": "CNPJ",
    "accepted_tos": True,
}


def _contract() -> Contract:
    return Contract(
        endpoint="POST /auth/register",
        dto="com.example.domain.auth.dto.RegisterRequest",
        auth="none",
        idempotency="none",
        baseline="baselines/auth-register.json",
        fields={"email": FieldSpec(required=True, json="email")},
    )


def _case(**kwargs) -> CaseFile:
    payload = {
        "id": "register-H01",
        "contract": "contracts/auth-register.yaml",
        "kind": "H01",
        "expect": ExpectSpec(status=201),
    }
    payload.update(kwargs)
    return CaseFile.model_validate(payload)


_DESCRIPTOR = Path(__file__).resolve().parent / "fixtures" / "qa" / "project.yaml"
_PROJECT = project_at(_DESCRIPTOR)


def _project(tmp_path: Path) -> ProjectView:
    """The descriptor in force, with this test's own (empty) log files."""
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    return project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))


def _config(tmp_path: Path) -> HarnessConfig:
    return config_for(_project(tmp_path))


def _run(
    tmp_path: Path,
    case: CaseFile,
    captured: list[httpx.Request],
    *,
    captures: dict[str, str] | None = None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"id": "u1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=case,
        contract=_contract(),
        baseline=dict(_BASELINE),
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="register",
        captures=captures,
    )


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


def test_generate_uuid_writes_capture_and_body(tmp_path: Path):
    captured: list[httpx.Request] = []
    store: dict[str, str] = {}
    nested_contract = Contract(
        endpoint="POST /api/users",
        dto="com.example.domain.user.dto.CreateUserCommand",
        auth="none",
        idempotency="none",
        baseline="baselines/api-users-post.json",
        fields={"external_user_id": FieldSpec(required=True, json="external_user_id")},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"external_user_id": "u1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=_case(
            generate={"external_user_id": "uuid"},
            capture={"external_user_id": "external_user_id"},
        ),
        contract=nested_contract,
        baseline={"external_user_id": "{{external_user_id}}"},
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run-uuid",
        step_index=1,
        run_id="users",
        captures=store,
    )
    sent = _body(captured[0])["external_user_id"]
    assert sent != "{{external_user_id}}"
    assert store["external_user_id"] == sent


def test_setup_other_uuid_does_not_clobber_session_external_user_id(tmp_path: Path):
    captured: list[httpx.Request] = []
    store = {"external_user_id": "session-user-id"}
    nested_contract = Contract(
        endpoint="POST /api/users",
        dto="com.example.domain.user.dto.CreateUserCommand",
        auth="none",
        idempotency="none",
        unique_json="external_user_id",
        baseline="baselines/api-users-post.json",
        fields={"external_user_id": FieldSpec(required=True, json="external_user_id")},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"external_user_id": "other-1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=_case(
            id="api-users-post-H-setup-other",
            kind="H-setup-other",
            generate={"external_user_id": "uuid"},
            capture={"other_external_user_id": "external_user_id"},
        ),
        contract=nested_contract,
        baseline={"external_user_id": "{{external_user_id}}"},
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run-setup-other",
        step_index=2,
        run_id="users",
        captures=store,
    )
    sent = _body(captured[0])["external_user_id"]
    assert sent != "session-user-id"
    assert store["external_user_id"] == "session-user-id"
    assert store["other_external_user_id"] == sent


def test_generate_email_replaces_baseline(tmp_path: Path):
    captured: list[httpx.Request] = []
    _run(tmp_path, _case(generate={"email": "email"}), captured)
    email = _body(captured[0])["email"]
    assert email != _BASELINE["email"]
    assert email.endswith("@qa.example.dev")


def test_unknown_generate_kind_fails(tmp_path: Path):
    with pytest.raises(HarnessError) as err:
        _run(tmp_path, _case(generate={"email": "iban"}), [])
    assert err.value.code == "GENERATE_UNKNOWN"


def test_generated_unknown_kind_fails():
    with pytest.raises(HarnessError) as err:
        _generated("iban", _PROJECT)
    assert err.value.code == "GENERATE_UNKNOWN"


def test_generated_email_is_not_literal():
    value = _generated("email", _PROJECT)
    assert value != "email"
    assert "@" in value


def test_capture_then_reuse_email(tmp_path: Path):
    captured: list[httpx.Request] = []
    store: dict[str, str] = {}
    _run(
        tmp_path,
        _case(generate={"email": "email"}, capture={"register_email": "email"}),
        captured,
        captures=store,
    )
    assert "register_email" in store
    saved = json.loads((tmp_path / "run" / "captures.json").read_text(encoding="utf-8"))
    assert saved["register_email"] == store["register_email"]
    _run(
        tmp_path,
        _case(
            id="register-N-rule-DUPLICATE_EMAIL",
            kind="N-rule-DUPLICATE_EMAIL",
            expect=ExpectSpec(status=422),
            generate={"email": "captured.register_email"},
        ),
        captured,
        captures=store,
    )
    assert _body(captured[1])["email"] == store["register_email"]


def test_missing_capture_fails_clearly(tmp_path: Path):
    with pytest.raises(HarnessError) as err:
        _run(
            tmp_path,
            _case(generate={"email": "captured.register_email"}),
            [],
            captures={},
        )
    assert err.value.code == "CAPTURE_MISSING"
    assert err.value.message == "captured.register_email missing"


def test_generate_nested_kyc_fields(tmp_path: Path):
    captured: list[httpx.Request] = []
    nested_contract = Contract(
        endpoint="POST /api/users",
        dto="com.example.domain.user.dto.CreateUserCommand",
        auth="none",
        idempotency="none",
        baseline="baselines/api-users-post.json",
        fields={"kyc_profile": FieldSpec(required=True, json="kyc_profile")},
    )
    case = _case(
        generate={
            "kyc_profile.name": "person_name",
            "kyc_profile.email": "email",
            "kyc_profile.cpf_cnpj": "cpf",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"id": "u1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=case,
        contract=nested_contract,
        baseline={
            "kyc_profile": {
                "name": "replace-with-generate",
                "email": "replace-with-generate",
                "cpf_cnpj": "replace-with-generate",
            }
        },
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="users",
    )
    body = _body(captured[0])
    assert body["kyc_profile"]["name"] != "replace-with-generate"
    assert body["kyc_profile"]["email"].endswith("@qa.example.dev")
    assert body["kyc_profile"]["cpf_cnpj"] != "replace-with-generate"


def test_unresolved_placeholder_fails(tmp_path: Path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"id": "u1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    with pytest.raises(HarnessError) as err:
        execute_step(
            case=_case(),
            contract=_contract(),
            baseline={"external_user_id": "{{external_user_id}}"},
            config=_config(tmp_path),
            client=client,
            run_dir=tmp_path / "run",
            step_index=1,
            run_id="login",
        )
    assert err.value.code == "PLACEHOLDER_UNRESOLVED"
    assert captured == []


def test_replace_with_generate_fills_email_and_password(tmp_path: Path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"id": "u1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=_case(),
        contract=_contract(),
        baseline={"email": "replace-with-generate", "password": "replace-with-generate"},
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="register",
    )
    body = _body(captured[0])
    assert body["email"].endswith("@qa.example.dev")
    assert body["password"].startswith("Aa1!")
    stored = json.loads((tmp_path / "run" / "steps" / "001-register-H01" / "request.json").read_text())
    assert stored["body"]["email"].endswith("@qa.example.dev")
    assert stored["body"]["password"] == "[REDACTED]"


def test_replace_with_generate_uses_secrets_without_generate_block(tmp_path: Path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=_case(),
        contract=_contract(),
        baseline={"email": "{{email}}", "password": "replace-with-generate"},
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="login",
        secrets={"email": "ops@example.dev", "password": "Aa1!tenant"},
    )
    body = _body(captured[0])
    assert body["email"] == "ops@example.dev"
    assert body["password"] == "Aa1!tenant"


def test_secret_email_and_password_replace_placeholders(tmp_path: Path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=_case(
            generate={"email": "secret.email", "password": "secret.password"}
        ),
        contract=_contract(),
        baseline={"email": "replace-with-generate", "password": "replace-with-generate"},
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="login",
        secrets={"email": "ops@example.dev", "password": "Aa1!tenant"},
    )
    body = _body(captured[0])
    assert body["email"] == "ops@example.dev"
    assert body["password"] == "Aa1!tenant"


def test_missing_secret_email_fails(tmp_path: Path):
    with pytest.raises(HarnessError) as err:
        execute_step(
            case=_case(generate={"email": "secret.email"}),
            contract=_contract(),
            baseline={"email": "replace-with-generate"},
            config=_config(tmp_path),
            client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200)),
                timeout=10.0,
            ),
            run_dir=tmp_path / "run",
            step_index=1,
            run_id="login",
            secrets={},
        )
    assert err.value.code == "SECRET_MISSING"


def test_login_reuses_shared_register_capture_without_secrets(tmp_path: Path):
    http_calls: list[httpx.Request] = []
    _run(
        tmp_path,
        _case(
            generate={"email": "email", "password": "password"},
            capture={"register_email": "email", "register_password": "password"},
        ),
        http_calls,
        captures={},
    )
    registered = _body(http_calls[0])
    shared = json.loads((tmp_path / "shared-captures.json").read_text(encoding="utf-8"))
    assert shared["register_email"] == registered["email"]
    assert shared["register_password"] == registered["password"]

    def handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request)
        return httpx.Response(200, json={"ok": True})

    execute_step(
        case=_case(
            id="login-H01",
            kind="H01",
            expect=ExpectSpec(status=200),
            generate={"email": "secret.email", "password": "secret.password"},
        ),
        contract=_contract(),
        baseline={"email": "replace-with-generate", "password": "replace-with-generate"},
        config=_config(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0),
        run_dir=tmp_path / "login-run",
        step_index=1,
        run_id="login",
        secrets={},
        captures={},
    )
    login_body = _body(http_calls[1])
    assert login_body["email"] == registered["email"]
    assert login_body["password"] == registered["password"]


def test_secrets_win_over_register_capture(tmp_path: Path):
    http_calls: list[httpx.Request] = []
    _run(
        tmp_path,
        _case(
            generate={"email": "email", "password": "password"},
            capture={"register_email": "email", "register_password": "password"},
        ),
        http_calls,
        captures={},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request)
        return httpx.Response(200, json={"ok": True})

    execute_step(
        case=_case(generate={"email": "secret.email", "password": "secret.password"}),
        contract=_contract(),
        baseline={"email": "replace-with-generate", "password": "replace-with-generate"},
        config=_config(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0),
        run_dir=tmp_path / "login-run",
        step_index=1,
        run_id="login",
        secrets={"email": "ops@example.dev", "password": "Aa1!tenant"},
        captures={},
    )
    body = _body(http_calls[1])
    assert body["email"] == "ops@example.dev"
    assert body["password"] == "Aa1!tenant"


def test_capture_response_stores_jwt_after_http(tmp_path: Path):
    store: dict[str, str] = {}
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            json={
                "jwt": "issued-jwt",
                "refresh_token": "issued-refresh",
                "api_key": "test_key_issued",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=_case(
            generate={"email": "email", "password": "password"},
            capture={"register_email": "email", "register_password": "password"},
            capture_response={
                "jwt": "jwt",
                "refresh_token": "refresh_token",
                "api_key": "api_key",
            },
        ),
        contract=_contract(),
        baseline=dict(_BASELINE),
        config=_config(tmp_path),
        client=client,
        run_dir=tmp_path / "run",
        step_index=1,
        run_id="register",
        captures=store,
    )
    assert store["jwt"] == "issued-jwt"
    assert store["refresh_token"] == "issued-refresh"
    assert store["api_key"] == "test_key_issued"
    assert store["register_email"] == _body(captured[0])["email"]
    saved = json.loads((tmp_path / "run" / "captures.json").read_text(encoding="utf-8"))
    assert saved["jwt"] == "issued-jwt"
    shared = json.loads((tmp_path / "shared-captures.json").read_text(encoding="utf-8"))
    assert shared["jwt"] == "issued-jwt"


def test_capture_response_missing_field_writes_step_without_abort(tmp_path: Path):
    store: dict[str, str] = {}
    run_dir = tmp_path / "run"
    result = execute_step(
        case=_case(capture_response={"jwt": "jwt"}),
        contract=_contract(),
        baseline=dict(_BASELINE),
        config=_config(tmp_path),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, json={"error": "INTERNAL_ERROR"})
            ),
            timeout=10.0,
        ),
        run_dir=run_dir,
        step_index=1,
        run_id="register",
        captures=store,
    )
    assert "jwt" not in store
    response = json.loads((result.step_dir / "response.json").read_text(encoding="utf-8"))
    assert response["status"] == 500
    saved = json.loads((run_dir / "captures.json").read_text(encoding="utf-8"))
    assert "jwt" not in saved


def test_capture_response_missing_field_does_not_block_next_case(tmp_path: Path):
    store: dict[str, str] = {}
    seen: list[httpx.Request] = []
    run_dir = tmp_path / "run"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(500, json={"error": "INTERNAL_ERROR"})
        return httpx.Response(401, json={"error": "Missing JWT"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    execute_step(
        case=_case(capture_response={"jwt": "jwt"}),
        contract=_contract(),
        baseline=dict(_BASELINE),
        config=_config(tmp_path),
        client=client,
        run_dir=run_dir,
        step_index=1,
        run_id="register",
        captures=store,
    )
    execute_step(
        case=_case(
            id="register-N-auth",
            kind="N-auth",
            expect=ExpectSpec(status=401),
            omit_headers=["Authorization"],
        ),
        contract=_contract(),
        baseline=dict(_BASELINE),
        config=_config(tmp_path),
        client=client,
        run_dir=run_dir,
        step_index=2,
        run_id="register",
        captures=store,
    )
    assert len(seen) == 2
    assert (run_dir / "steps" / "002-register-N-auth" / "response.json").is_file()


def test_jwt_auth_uses_shared_capture_without_secrets(tmp_path: Path):
    (tmp_path / "shared-captures.json").write_text(
        json.dumps({"jwt": "captured-jwt-token"}),
        encoding="utf-8",
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    execute_step(
        case=_case(id="keys-H01", expect=ExpectSpec(status=200)),
        contract=Contract(
            endpoint="POST /platform/api-keys",
            dto="com.example.domain.auth.dto.CreateApiKeyRequest",
            auth="jwt",
            idempotency="none",
            baseline="baselines/platform-api-keys-post.json",
            fields={"name": FieldSpec(required=True, json="name")},
        ),
        baseline={"name": "qa"},
        config=_config(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0),
        run_dir=tmp_path / "keys-run",
        step_index=1,
        run_id="keys",
        secrets={},
        captures={},
        environment="sandbox",
    )
    assert seen[0].headers["Authorization"] == "Bearer captured-jwt-token"
    assert seen[0].headers["X-Environment"] == "sandbox"

