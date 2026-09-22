from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN = (
    "qa-trilho-a@nokr.dev",
    "admin@acme.com.br",
    "maria@example.com",
    "Acme Corp",
    "Mario Silvio",
    "11222333000181",
    "52998224725",
)


def test_campaign_identity_does_not_invent_documents():
    scanned = list((ROOT / "baselines").glob("*.json"))
    scanned.extend((ROOT / "cases").rglob("*.yaml"))
    offenders: list[str] = []
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert offenders == []


def test_register_and_activate_h01_use_generate():
    from nokr_qa.schema.load import load_case

    register = load_case(ROOT / "cases" / "auth-register" / "register-H01.yaml")
    assert register.generate["email"] == "email"
    assert register.generate["password"] == "password"
    assert register.generate["document_number"] == "cnpj"
    assert register.capture == {"register_email": "email", "register_password": "password"}
    login = load_case(ROOT / "cases" / "auth-login" / "login-H01.yaml")
    assert login.generate["email"] == "secret.email"
    assert login.generate["password"] == "secret.password"
    activate = load_case(ROOT / "cases" / "tenants-activate" / "tenants-activate-H01.yaml")
    assert activate.generate["cpf_cnpj"] == "cnpj"
    assert activate.generate["email"] == "email"
    users = load_case(ROOT / "cases" / "api-users-post" / "api-users-post-H01.yaml")
    assert users.generate["kyc_profile.cpf_cnpj"] == "cpf"
    assert users.generate["external_user_id"] == "uuid"
    assert users.capture == {"external_user_id": "external_user_id"}
    assert users.capture_response == {"nokr_user_id": "nokr_user_id"}
    cpf_forbidden = load_case(
        ROOT / "cases" / "tenants-activate" / "tenants-activate-N-rule-CPF_FORBIDDEN.yaml"
    )
    assert cpf_forbidden.generate["cpf_cnpj"] == "cpf"
