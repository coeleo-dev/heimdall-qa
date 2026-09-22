import json

from validate_docbr import CNPJ
from validate_docbr import CPF

from heimdall_qa.cli import main
from heimdall_qa.errors import HarnessError
from heimdall_qa.fixtures import build_payload
from heimdall_qa.fixtures import build_value
from heimdall_qa.fixtures import make_faker


def test_email_is_unique_and_shaped():
    first = build_value("email")
    second = build_value("email")
    assert first != second
    assert first.endswith("@qa.nokr.dev")
    assert "@" in first


def test_cpf_and_cnpj_validate():
    assert CPF().validate(build_value("cpf"))
    assert CNPJ().validate(build_value("cnpj"))


def test_register_payload_has_identity_fields():
    payload = build_payload("register", fake=make_faker(seed=1))
    assert CNPJ().validate(payload["document_number"])
    assert payload["document_type"] == "CNPJ"
    assert "@" in payload["email"]
    assert payload["company_name"]
    assert payload["password"].startswith("Aa1!")


def test_unknown_kind_raises():
    try:
        build_value("iban")
    except HarnessError as err:
        assert err.code == "FIXTURE_UNKNOWN"
        return
    raise AssertionError("expected HarnessError")


def test_fixture_cli_prints_json(capsys):
    assert main(["fixture", "cnpj"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert CNPJ().validate(payload["cnpj"])
