from collections.abc import Callable
from uuid import uuid4

from faker import Faker
from validate_docbr import CNPJ
from validate_docbr import CPF

from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView

#: Kinds the harness can synthesise on its own, with no declared generator.
#: Everything else a project needs — a national tax id, a bank account, a licence
#: number — arrives as a `fixtures.generators` entry, so the core never has to
#: guess what an unnamed project considers a valid document.
SCALAR_KINDS = frozenset(
    {"email", "password", "person_name", "company_name", "address"}
)
_PAYLOAD_KINDS = frozenset({"register"})

#: The kinds whose value is a national document, and the check digits that tell a
#: real one from a broken one. `validate` needs the difference: a document a case
#: deliberately breaks (`'11111111111'` under an `N-rule-INVALID_CPF`) is the
#: subject of that case, and no re-run will ever collide with it.
_DOCUMENT_CHECKS: dict[str, Callable[[str], bool]] = {
    "cpf": CPF().validate,
    "cnpj": CNPJ().validate,
}
DOCUMENT_KINDS = frozenset(_DOCUMENT_CHECKS)


def is_valid_document(value: str, kind: str) -> bool:
    """Whether `value` is a document of `kind` the API would accept.

    A kind the harness has no check for is not judged: the project declared it, and
    inventing an opinion about its format is how a validator starts refusing
    correct content.
    """
    check = _DOCUMENT_CHECKS.get(kind)
    return True if check is None else bool(check(value))

#: Implementations a descriptor may name in `fixtures.generators[].kind` (V7).
#: They are plug-in points rather than product knowledge: `validate_docbr_cpf`
#: generates a CPF the way `faker_name` generates a name, and a project that
#: needs neither never reaches this table.
_GENERATORS: dict[str, Callable[[Faker], str]] = {
    "validate_docbr_cpf": lambda _fake: CPF().generate(),
    "validate_docbr_cnpj": lambda _fake: CNPJ().generate(),
    "faker_name": lambda fake: fake.name(),
    "faker_company": lambda fake: fake.company(),
    "faker_address": lambda fake: fake.address().replace("\n", ", "),
}

#: RFC 2606 reserves `example.com`, which can never be delivered anywhere. It is
#: the floor, not the recommendation: a project whose API validates the domain
#: declares `fixtures.email_domain` and gets its own.
_DEFAULT_DOMAIN = "example.com"

#: The wrong answer to "which kinds exist": listing what the core knows keeps
#: callers from finding out that the project declared one they cannot see.
_UNKNOWN_KIND_HINT = (
    "use email, password, person_name, company_name or address, "
    "or declare fixtures.generators.<kind> in the project descriptor"
)


def make_faker(seed: int | None = None, locale: str = "en_US") -> Faker:
    fake = Faker(locale)
    if seed is not None:
        fake.seed_instance(seed)
    return fake


def is_known_kind(kind: str, project: ProjectView | None = None) -> bool:
    """Whether `build_value` can build `kind` for this project."""
    if kind in SCALAR_KINDS:
        return True
    return project is not None and kind in project.generator_kinds()


def build_value(
    kind: str,
    *,
    fake: Faker | None = None,
    project: ProjectView | None = None,
) -> str:
    faker = fake or make_faker(locale=_locale_of(project))
    if kind == "email":
        return f"{faker.user_name()}.{uuid4().hex[:8]}@{_domain_of(project)}"
    if kind == "password":
        return f"Aa1!{uuid4().hex[:10]}"
    if kind == "person_name":
        return faker.name()
    if kind == "company_name":
        return faker.company()
    if kind == "address":
        return faker.address().replace("\n", ", ")
    return _build_declared(kind, faker, project)


def build_payload(
    kind: str,
    *,
    fake: Faker | None = None,
    project: ProjectView | None = None,
) -> dict[str, str]:
    faker = fake or make_faker(locale=_locale_of(project))
    if kind == "register":
        document = _register_document(project)
        return {
            "email": build_value("email", fake=faker, project=project),
            "password": build_value("password", fake=faker, project=project),
            "company_name": build_value("company_name", fake=faker, project=project),
            "document_type": document.upper(),
            "document_number": build_value(document, fake=faker, project=project),
            "person_name": build_value("person_name", fake=faker, project=project),
            "address": build_value("address", fake=faker, project=project),
        }
    if kind in SCALAR_KINDS or is_known_kind(kind, project):
        return {kind: build_value(kind, fake=faker, project=project)}
    raise HarnessError(
        code="FIXTURE_UNKNOWN",
        message=f"unknown fixture kind: {kind}",
        hint=_UNKNOWN_KIND_HINT,
    )


def _build_declared(
    kind: str,
    faker: Faker,
    project: ProjectView | None,
) -> str:
    """A kind the project declared, built by the implementation it named."""
    declared = project.generator_kinds().get(kind) if project is not None else None
    if declared is None:
        raise HarnessError(
            code="FIXTURE_UNKNOWN",
            message=f"unknown fixture kind: {kind}",
            hint=_UNKNOWN_KIND_HINT,
        )
    implementation = _GENERATORS.get(declared)
    if implementation is None:
        known = ", ".join(sorted(_GENERATORS))
        raise HarnessError(
            code="FIXTURE_GENERATOR_UNKNOWN",
            message=f"fixture kind '{kind}' names generator '{declared}', which is unknown",
            hint=f"use one of: {known}",
        )
    return implementation(faker)


def _register_document(project: ProjectView | None) -> str:
    """Which declared kind an onboarding payload uses as its tax document."""
    if project is not None and "cnpj" in project.generator_kinds():
        return "cnpj"
    if project is not None and "cpf" in project.generator_kinds():
        return "cpf"
    return "cnpj"


def _locale_of(project: ProjectView | None) -> str:
    return project.locale() if project is not None else "en_US"


def _domain_of(project: ProjectView | None) -> str:
    return project.email_domain() if project is not None else _DEFAULT_DOMAIN
