"""The neutral description of an API surface, and the readers that produce one.

Two halves of one seam.

**The IR.** `ApiSchema` is what a reader returns, and it is deliberately the shape
`coverage.expand` already consumes and nothing more: an endpoint has a method, a
path, whether it takes a body, its fields, and the status axes the source could
name. It names no framework and no file format, so a Spring reader and an OpenAPI
reader produce the same thing and the generator never learns which one spoke.

**The registry.** A reader is resolved from the `heimdall_qa.contract_sources`
entry point group, so the core never imports a plugin. `openapi` and `inline` ship
with the core, because reading an OpenAPI document needs no third party and
`inline` needs nothing at all; anything else — a reader for a project's own source
code, say — registers under the same group and arrives with its own distribution,
so a project installs the reader it needs and no other project pays for it.

**What a source cannot answer is a `Gap`, not a guess.** An invented PII denylist
would produce a green case that verifies nothing, so a reader says "I do not know"
and `discover` writes that down with the axis it affects.
"""

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from importlib.metadata import EntryPoint
from importlib.metadata import entry_points
from types import ModuleType
from typing import Any
from typing import Protocol
from typing import runtime_checkable

from heimdall_qa.errors import HarnessError

#: The entry point group a contract reader registers under. It is the contract
#: between the core and a distribution, so it is part of the public surface.
ENTRY_POINT_GROUP = "heimdall_qa.contract_sources"

#: The reader that needs no distribution, because a human did the reading: the
#: contract file *is* the source, so there is nothing to derive it from.
INLINE = "inline"

#: The reader that ships with the core. Its only dependency is PyYAML, which the
#: core already has, which is why it is not a plugin.
OPENAPI = "openapi"

#: The axis of a gap about the surface and not about one endpoint: a route the
#: reader saw but refused to describe, a controller it could not place. It sorts
#: before every real axis, so a report reads surface first.
SURFACE = "*"

#: What a caller gets instead of a silently empty contract.
CONTRACT_SOURCE_UNAVAILABLE = "CONTRACT_SOURCE_UNAVAILABLE"
#: The source was installed but could not read what it was pointed at.
CONTRACT_SOURCE_FAILED = "CONTRACT_SOURCE_FAILED"


@dataclass(frozen=True)
class FieldSchema:
    """One body field, as a source describes it.

    Every attribute maps onto a `FieldSpec`, and the ones a source cannot answer
    stay `None` rather than becoming a default: "no `maxLength` in the document"
    and "`maxLength` is 3" are different facts, and only the second one generates
    a `B-max` case.
    """

    name: str
    required: bool = False
    #: The wire name when it differs from `name`. A generator that finds none uses
    #: `name`, which is what an OpenAPI property key already is.
    json_name: str | None = None
    json_alias: bool = False
    max_length: int | None = None
    max_keys: int | None = None
    pattern: str | None = None
    denylist: tuple[str, ...] = ()
    example: Any = None
    invalid: Any = None


@dataclass(frozen=True)
class RuleSchema:
    """One product rule a source could name, with the status it answers.

    A rule is the one axis where the status is per rule and not per axis, so a
    reader that cannot see the rule's status must leave it out rather than pick
    one: one API answers 400 for one rule and 422 for the next.
    """

    id: str
    status: int
    code: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class EndpointSchema:
    """One route, as a source describes it."""

    method: str
    path: str
    #: Whether the source says this operation takes a body at all. It is what
    #: separates "no body" from "a body nobody described" — the second one leaves a
    #: gap, because every generated case would then send the empty object and pass or
    #: fail for a reason the contract never stated.
    has_body: bool = True
    fields: tuple[FieldSchema, ...] = ()
    rules: tuple[RuleSchema, ...] = ()
    required_headers: tuple[str, ...] = ()
    path_params: tuple[str, ...] = ()
    #: The 2xx the source declares for the happy path. `None` means "the source
    #: does not say", which generates `status: TODO` and a line in the report —
    #: never a chosen 200.
    success_status: int | None = None
    idempotency: str = "none"
    dedup: str | None = None
    resource_id_in_path: bool = False
    async_mode: bool | None = None
    #: The condition the code puts on the whole route (`@Profile`), when it states
    #: one. It is not a fact about the request: a route behind a profile is not on
    #: the surface a run talks to, so the generator refuses it rather than writing
    #: a contract nobody can reach. `None` means "on the surface, unconditionally".
    conditional: str | None = None

    def endpoint(self) -> str:
        """`METHOD /path`, the spelling a `Contract.endpoint` uses."""
        return f"{self.method.upper()} {self.path}"

    def field(self, name: str) -> FieldSchema | None:
        for candidate in self.fields:
            if candidate.name == name:
                return candidate
        return None


@dataclass(frozen=True)
class ErrorTable:
    """What a source says a failure looks like, per axis.

    The status of a negative case is not one number: a malformed body and a
    product rule are both "the request was refused", and the reference API answers
    400 for the first and 422 for the second. A reader that can see the code that
    decides — a `@RestControllerAdvice`, an error-middleware table — can say which,
    and it says it here, by the axis name the harness asks about.

    `notes` carries what the table cannot hold: a status the code chooses at
    runtime, an axis with two answers, a shape that is not uniform across filters.
    A note is not a failure; it is the part of the answer that stays open.
    """

    statuses: Mapping[str, int] = field(default_factory=dict)
    #: The name of the declared envelope shape (`spring`, `code_message`, ...),
    #: when the source says one. `None` means the source did not say.
    envelope: str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Gap:
    """Something a human still has to decide about one endpoint.

    `axis` is the case axis affected, because a gap that names no axis is not
    actionable: the reader cannot know whether the missing fact costs one case or
    twelve.
    """

    endpoint: str
    axis: str
    why: str
    fix: str


@dataclass(frozen=True)
class ApiSchema:
    """Everything one reader could see, plus what it could not."""

    source: str
    endpoints: tuple[EndpointSchema, ...] = ()
    gaps: tuple[Gap, ...] = field(default_factory=tuple)
    #: The failure side of the surface. A reader that reads code can usually see
    #: it; one that reads a document usually cannot, and leaves it empty rather
    #: than guessing a status the API may not answer.
    errors: ErrorTable = field(default_factory=ErrorTable)

    def names(self) -> tuple[str, ...]:
        return tuple(endpoint.endpoint() for endpoint in self.endpoints)

    def find(self, route: str) -> EndpointSchema | None:
        """The endpoint spelled `METHOD /path`, or `None`.

        A route the reader never saw is `None` and not an error: the caller asked
        about one endpoint, and "this API has no such route" is an answer.
        """
        wanted = _normalise(route)
        for endpoint in self.endpoints:
            if _normalise(endpoint.endpoint()) == wanted:
                return endpoint
        return None


@runtime_checkable
class ContractSource(Protocol):
    """What a reader must do to serve one `contract.source` name.

    It is a `Protocol` and not a base class so that a plugin distribution does not
    have to install the core's type: the core asks for one method, and anything
    that has it is a reader.
    """

    name: str

    def read(self, location: str | None) -> ApiSchema:
        """The schema at `location`, or the failure that names why it is unreadable.

        Returning an empty schema for a document that was not found would make
        "nothing to read" look like "nothing declared", which is the one confusion
        the whole seam exists to prevent.
        """
        ...


@dataclass(frozen=True)
class ResolvedContractSource:
    """The reader that won, and where it should read from."""

    source: str
    location: str | None
    spec_version: str | None


def resolve_contract_source(attempts: tuple[str, ...]) -> str:
    """The first *installed* reader among `attempts`, or the named failure.

    Order is the whole point: a target that publishes no OpenAPI can be read from
    its own source code without making every other target install that reader.
    """
    installed = available_contract_sources()
    for name in attempts:
        if name in installed:
            return name
    raise HarnessError(
        code=CONTRACT_SOURCE_UNAVAILABLE,
        message=(
            "no installed reader serves any declared contract.source"
            f" ({', '.join(attempts)})"
        ),
        hint=(
            "install the distribution that provides one of them, or declare"
            " one of: " + ", ".join(sorted(installed))
        ),
    )


def available_contract_sources() -> dict[str, str]:
    """Installed reader name -> what provides it, including the two built-ins.

    The value is only ever quoted back in an error message, so it names the
    distribution to install rather than the module that happens to export it.
    """
    found = {INLINE: "heimdall-qa", OPENAPI: "heimdall-qa"}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        found.setdefault(entry.name, _provider(entry))
    return found


def load_contract_source(name: str) -> ContractSource:
    """The reader registered under `name`, or the failure that names what is not.

    A built-in needs no entry point: it lives in `heimdall_qa.sources`, so a source
    checkout resolves it exactly as an installed wheel does.
    """
    builtin = _builtin_factories().get(name)
    if builtin is not None:
        return builtin()
    loaded = _from_entry_points(name)
    if loaded is not None:
        return loaded
    raise HarnessError(
        code=CONTRACT_SOURCE_UNAVAILABLE,
        message=f"no installed reader serves contract.source '{name}'",
        hint=(
            "install the distribution that provides it, or declare one of: "
            + ", ".join(sorted(available_contract_sources()))
        ),
    )


def _builtin_factories() -> dict[str, Callable[[], ContractSource]]:
    """The readers that ship with the core, imported where they are used.

    The import is local because `sources/` imports this module for the IR, and a
    module-level import here would close the cycle.
    """
    from heimdall_qa.sources import BUILT_IN

    return dict(BUILT_IN)


def _from_entry_points(name: str) -> ContractSource | None:
    """The plugin registered under `name`, built from a module or a factory.

    Both spellings are legal, the way the provider group allows them: an entry
    point may name the module that exposes `reader`, or the factory itself.
    """
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name != name:
            continue
        return _as_reader(entry, entry.load())
    return None


def _as_reader(entry: EntryPoint, loaded: Any) -> ContractSource:
    if isinstance(loaded, ModuleType):
        factory = getattr(loaded, "reader", None)
        if callable(factory):
            return factory()
        raise _invalid(entry, "module exposes no reader()")
    if callable(loaded):
        return loaded()
    return loaded


def _invalid(entry: EntryPoint, why: str) -> HarnessError:
    return HarnessError(
        code=CONTRACT_SOURCE_FAILED,
        message=f"contract source '{entry.name}' is invalid: {why}",
        hint=(
            f"the {ENTRY_POINT_GROUP} entry point must point at a module with"
            " reader(), or at the factory itself"
        ),
    )


def _provider(entry: EntryPoint) -> str:
    """The distribution behind an entry point, falling back to its target."""
    distribution = getattr(entry, "dist", None)
    if distribution is not None:
        return distribution.name
    return entry.value


def _normalise(route: str) -> str:
    """`post   /toy/items/` and `POST /toy/items` are the same route."""
    method, _, path = route.strip().partition(" ")
    path = path.strip().rstrip("/")
    return f"{method.upper()} {path or '/'}"
