from uuid import uuid4

from faker import Faker
from validate_docbr import CNPJ
from validate_docbr import CPF

from heimdall_qa.errors import HarnessError

SCALAR_KINDS = frozenset(
    {"email", "password", "person_name", "company_name", "address", "cpf", "cnpj"}
)
_PAYLOAD_KINDS = frozenset({"register"})
KNOWN_KINDS = SCALAR_KINDS | _PAYLOAD_KINDS


def make_faker(seed: int | None = None) -> Faker:
    fake = Faker("pt_BR")
    if seed is not None:
        fake.seed_instance(seed)
    return fake


def build_value(kind: str, *, fake: Faker | None = None) -> str:
    generator = fake or make_faker()
    if kind == "email":
        return f"{generator.user_name()}.{uuid4().hex[:8]}@qa.nokr.dev"
    if kind == "password":
        return f"Aa1!{uuid4().hex[:10]}"
    if kind == "person_name":
        return generator.name()
    if kind == "company_name":
        return generator.company()
    if kind == "address":
        return generator.address().replace("\n", ", ")
    if kind == "cpf":
        return CPF().generate()
    if kind == "cnpj":
        return CNPJ().generate()
    raise HarnessError(
        code="FIXTURE_UNKNOWN",
        message=f"unknown fixture kind: {kind}",
        hint="use email, password, person_name, company_name, address, cpf, cnpj, or register",
    )


def build_payload(kind: str, *, fake: Faker | None = None) -> dict[str, str]:
    generator = fake or make_faker()
    if kind == "register":
        return {
            "email": build_value("email", fake=generator),
            "password": build_value("password", fake=generator),
            "company_name": build_value("company_name", fake=generator),
            "document_number": build_value("cnpj", fake=generator),
            "document_type": "CNPJ",
            "person_name": build_value("person_name", fake=generator),
            "address": build_value("address", fake=generator),
        }
    if kind in SCALAR_KINDS:
        return {kind: build_value(kind, fake=generator)}
    raise HarnessError(
        code="FIXTURE_UNKNOWN",
        message=f"unknown fixture kind: {kind}",
        hint="use email, password, person_name, company_name, address, cpf, cnpj, or register",
    )
