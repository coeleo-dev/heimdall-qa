import json
from pathlib import Path

from validate_docbr import CNPJ
from validate_docbr import CPF

from heimdall_qa.cli import main
from heimdall_qa.errors import HarnessError
from heimdall_qa.fixtures import build_payload
from heimdall_qa.fixtures import build_value
from heimdall_qa.fixtures import make_faker
from heimdall_qa.testing import project_at

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: A project that declares an identity domain and two document generators. The
#: kernel knows neither of those things on its own.
_PROJECT = project_at(_FIXTURES / "qa" / "project.yaml")


def test_email_is_unique_and_shaped():
    first = build_value("email", project=_PROJECT)
    second = build_value("email", project=_PROJECT)
    assert first != second
    assert first.endswith("@qa.example.dev")
    assert "@" in first


def test_email_without_a_project_lands_on_the_reserved_domain():
    # RFC 2606: a fixture nobody declared a domain for must not be deliverable.
    assert build_value("email").endswith("@example.com")


def test_cpf_and_cnpj_validate():
    assert CPF().validate(build_value("cpf", project=_PROJECT))
    assert CNPJ().validate(build_value("cnpj", project=_PROJECT))


def test_register_payload_has_identity_fields():
    payload = build_payload("register", fake=make_faker(seed=1), project=_PROJECT)
    assert CNPJ().validate(payload["document_number"])
    assert payload["document_type"] == "CNPJ"
    assert "@" in payload["email"]
    assert payload["company_name"]
    assert payload["password"].startswith("Aa1!")


def test_unknown_kind_raises():
    try:
        build_value("iban", project=_PROJECT)
    except HarnessError as err:
        assert err.code == "FIXTURE_UNKNOWN"
        return
    raise AssertionError("expected HarnessError")


def test_a_kind_the_project_never_declared_raises():
    try:
        build_value("cnpj")
    except HarnessError as err:
        assert err.code == "FIXTURE_UNKNOWN"
        return
    raise AssertionError("expected HarnessError")


def test_fixture_cli_prints_json(capsys):
    """The CLI resolves the descriptor from `--root` (ADR-01)."""
    assert main(["fixture", "cnpj", "--root", str(_FIXTURES)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert CNPJ().validate(payload["cnpj"])
