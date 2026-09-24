"""The project descriptor: what the harness must know about an API it did not write.

Design and rationale live in `contrib/architecture.md`, `## 2. The project
descriptor`. The shape follows its principle P2 — *role, never product name*:
`environments`, `routes`, `budgets`, never a product's base_url or a hardcoded
`/api/ingest`.

Every model is `extra="forbid"`. A descriptor that silently ignores a key the
author believed was doing something is worse than one that fails to load, which is
the same reasoning that made `SuiteStep` reject unregistered step kinds.
"""

import re
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

#: Rule codes from F2 §6. They are part of the CLI contract, so they are constants
#: instead of inline strings: a test can assert on the code, not on the prose.
ID_INVALID = "DESCRIPTOR_ID_INVALID"
NO_ENVIRONMENT = "DESCRIPTOR_NO_ENVIRONMENT"
UNKNOWN_AUTH = "DESCRIPTOR_UNKNOWN_AUTH"
INVALID_HEADER = "DESCRIPTOR_INVALID_HEADER"
UNKNOWN_BUDGET = "DESCRIPTOR_UNKNOWN_BUDGET"
UNKNOWN_TARGET = "DESCRIPTOR_UNKNOWN_TARGET"
TRACE_HEADER_MISSING = "DESCRIPTOR_TRACE_HEADER_MISSING"
DUPLICATE_ROUTE = "DESCRIPTOR_DUPLICATE_ROUTE"
BASE_URL_MISSING = "DESCRIPTOR_BASE_URL_MISSING"
PREFIX_MISSING = "DESCRIPTOR_PREFIX_MISSING"

#: RFC 7230 token: what a header name may contain.
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

#: What a `project.id` may contain. It names the provider directory, so it is not
#: free-form prose.
ID_PATTERN = re.compile(r"^[a-z0-9-]+$")

#: A `provider` id names an installed distribution, so it obeys the same slug
#: rule as `project.id` — and for the same reason.
PROVIDER_INVALID = "DESCRIPTOR_PROVIDER_INVALID"

#: The framework-level failure axes a project may give a status to. A product rule
#: is deliberately absent: its status is declared per rule, in the contract's
#: `rules[].status`, because one API answers 400 for one rule and 422 for the next
#: (measured in the reference corpus: 21 rules at 400, 27 at 422, 7 at 409).
DEFAULT_ERROR_STATUSES: dict[str, int] = {
    # A malformed body, a missing required field, an over-long or off-pattern one.
    "validation": 400,
    # A required `@RequestHeader` that never arrived.
    "missing_header": 400,
    # A credential that is absent, expired or wrong.
    "auth": 401,
    # A path segment or body field naming a resource that does not exist.
    "not_found": 404,
    # A replayed `X-Idempotency-Key` whose body contradicts the first request.
    "idempotency_conflict": 409,
    # Two environments colliding over the same natural key (`E-conflict`).
    "environment_conflict": 400,
    # A request reaching across the environment boundary (`E-isolate`).
    "environment_isolation": 403,
}

#: Declaring a status for an axis nobody asks about is dead configuration, so it
#: is refused at load time instead of being silently ignored.
UNKNOWN_ERROR_AXIS = "DESCRIPTOR_UNKNOWN_ERROR_AXIS"
ERROR_STATUS_INVALID = "DESCRIPTOR_ERROR_STATUS_INVALID"
CONTRACT_SOURCE_MISSING = "DESCRIPTOR_CONTRACT_SOURCE_MISSING"

#: `marker_field` names a path into a log record, not an HTTP header. Validating it
#: with the header rule refused `data.trace.id` — the one shape the field exists for.
INVALID_FIELD_PATH = "DESCRIPTOR_INVALID_FIELD_PATH"

#: A source that declares two ways to carry the id, or none that its format can use.
LOG_SOURCE_AMBIGUOUS = "DESCRIPTOR_LOG_SOURCE_AMBIGUOUS"

_NO_AUTH = "none"

#: A dotted path into a JSON object: `traceId`, `data.trace.id`.
_FIELD_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class ProjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str | None = None

    @model_validator(mode="after")
    def id_is_a_slug(self) -> "ProjectSpec":
        """Checked here, not with `Field(pattern=...)`, so the failure has a code."""
        if not ID_PATTERN.match(self.id):
            raise ValueError(
                f"{ID_INVALID}: project.id must be present and match"
                f" [a-z0-9-]+, got {self.id!r}"
            )
        return self


class EnvironmentSpec(BaseModel):
    """One deployment the cases may talk to. `base_url` is the whole point."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = ""

    @model_validator(mode="after")
    def base_url_is_required(self) -> "EnvironmentSpec":
        if not self.base_url.strip():
            raise ValueError(
                f"{BASE_URL_MISSING}: an environment without base_url cannot be"
                " exercised; declare `base_url: http://host:port`"
            )
        return self


class AuthSchemeSpec(BaseModel):
    """How to build one credential header. The value itself never lives here."""

    model_config = ConfigDict(extra="forbid")

    header: str
    scheme: Literal["bearer", "raw", "basic", "hmac"] = "raw"
    #: Credential shape per environment (V6). The prefix is product data, declared.
    prefixes: dict[str, str] = Field(default_factory=dict)


class EnvironmentHeaderSpec(BaseModel):
    """The header that selects the logical environment (V1)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    values: dict[str, str] = Field(default_factory=dict)
    #: Which environment the isolation cases cross *into*: `E-isolate` and
    #: `E-conflict` exist to prove two namespaces do not see each other, and the
    #: generated case has to say which one it collides with. Declared, because a
    #: project may call its second environment anything.
    isolation: str | None = None


class RouteSpec(BaseModel):
    """One prefix, and the decisions its requests need (V2 + V3)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    prefix: str = ""
    auth: str = _NO_AUTH
    budget: str | None = None
    async_mode: bool = Field(default=False, alias="async")
    require_environment_header: bool = False
    #: Which declared `targets` entry serves this prefix. Absent means the
    #: environment's own `base_url`, so the common case declares nothing.
    target: str | None = None
    #: A POST here creates a resource whose *name* the API refuses to duplicate,
    #: so the case needs `unique_json` to stay repeatable. Declared per route
    #: because it is a fact about the API, and inferring it from the path is
    #: exactly the string matching this descriptor exists to delete.
    catalog_unique: bool = False
    #: Requests on this route are throttled in sequence, sharing a pacer under
    #: this key. It exists because an onboarding endpoint that rate-limits by
    #: e-mail is a fact about the API, not about the harness.
    pace_key: str | None = None

    @model_validator(mode="after")
    def prefix_is_required(self) -> "RouteSpec":
        if not self.prefix.strip():
            raise ValueError(
                f"{PREFIX_MISSING}: a route needs a path prefix, e.g. `prefix: /api/ingest`"
            )
        return self


class BudgetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: int = Field(ge=1)
    fail: int = Field(ge=1)


class ErrorSpec(BaseModel):
    """How a failure looks, per axis, and what must never reach disk.

    `statuses` replaced a single `validation_status`. One scalar could not answer
    the question a generated case asks — "what does *this* axis return" — and the
    value the reference descriptor declared (422) contradicted its own corpus,
    where a malformed body is a 400 and only a product rule reaches 422.
    """

    model_config = ConfigDict(extra="forbid")

    #: Axis -> HTTP status. An axis left out keeps its default.
    #:
    #: Empty means "declared nothing", which is not the same fact as declaring the
    #: default: a project that says nothing lets the contract source's own table
    #: answer (see `ProjectView.error_status`), and a project that declares a value
    #: keeps it even when a reader disagrees — the disagreement is reported, never
    #: silently resolved.
    statuses: dict[str, int] = Field(default_factory=dict)
    envelope: Literal["rfc7807", "spring", "code_message", "none"] = "none"
    #: Package roots whose stack frames prove the trace came from the product (V9).
    product_packages: list[str] = Field(default_factory=list)
    redact: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def every_axis_is_one_the_harness_asks_about(self) -> "ErrorSpec":
        for axis in self.statuses:
            if axis not in DEFAULT_ERROR_STATUSES:
                known = ", ".join(sorted(DEFAULT_ERROR_STATUSES))
                raise ValueError(
                    f"{UNKNOWN_ERROR_AXIS}: errors.statuses.{axis} is not an axis the"
                    f" harness asks about ({known})"
                )
        return self

    @model_validator(mode="after")
    def every_status_is_a_failure(self) -> "ErrorSpec":
        for axis, status in self.statuses.items():
            if not 400 <= status <= 599:
                raise ValueError(
                    f"{ERROR_STATUS_INVALID}: errors.statuses.{axis} must be a 4xx or"
                    f" 5xx status, got {status}"
                )
        return self


class TraceSpec(BaseModel):
    """The correlation header and the prefix the harness stamps into it (V10)."""

    model_config = ConfigDict(extra="forbid")

    header: str | None = None
    prefix: str = ""


class LogTimestampSpec(BaseModel):
    """How to read the moment a log line was written.

    Declared, never inferred (F5 §2): the harness used to guess at one format and
    silently drop every line that did not match it. The format is `strptime`'s, and
    the value is used to **order** lines from different services inside one step —
    never to select them, which is what the removed ±1s window did (ADR-03).
    """

    model_config = ConfigDict(extra="forbid")

    #: `strptime` format of the stamp, e.g. `%Y-%m-%dT%H:%M:%S.%f%z`.
    format: str = "%Y-%m-%d %H:%M:%S"
    #: `local` reads a stamp that carries no offset in the host's zone — which is
    #: the assumption `local` names, and the one to state when the service's clock
    #: is known to be elsewhere. A stamp that carries its own offset keeps it.
    timezone: Literal["local", "utc"] = "local"
    #: For `json-lines`: the dotted path of the stamp inside the object.
    field: str | None = None


class LogMultilineSpec(BaseModel):
    """Which line starts a new entry, so a stack trace stays with its header."""

    model_config = ConfigDict(extra="forbid")

    #: A regex. A line that matches opens an entry; the following lines that do
    #: not match belong to it.
    start: str


class LogSourceSpec(BaseModel):
    """One place a correlation line can appear.

    See `contrib/architecture.md`, `## 7. Context propagation`, for what
    `propagate: false` means and why a source that says nothing is reported rather
    than counted.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    #: Relative to this descriptor's own directory, like `contract.location`.
    path: str
    #: A regex that matches the trace line. `{trace_id}` is replaced by the id
    #: literally, so `'trace_id: \[{trace_id}\]'` is the Logback spelling of one.
    #: The default is the id itself, which correlates any structured log.
    marker: str | None = None
    #: For `json-lines`: the dotted path of the id inside the object, instead of a
    #: regex over the text. `traceId`, or `data.trace.id`.
    marker_field: str | None = None
    format: Literal["text", "logback", "json-lines"] = "text"
    #: What the source means to the round: `sync` logs the request itself, `async`
    #: logs what a consumer did with it afterwards. An async source is read (and a
    #: missing line is a finding) on a route the contract declares asynchronous.
    role: Literal["sync", "async"] = "sync"
    #: Whether the trace reaches this source at all. `false` is the declaration
    #: that separates "the service never logged it" from "nobody asked it to": a
    #: missing line is a **product** failure when the source propagates and a
    #: declared, explained skip when it does not. It is also what the harness will
    #: not wait for — there is nothing coming.
    propagate: bool = True
    timestamp: LogTimestampSpec | None = None
    multiline: LogMultilineSpec | None = None
    #: A ceiling on what one read takes from the file, in bytes. A step that writes
    #: megabytes of log must not be re-read whole every 20 ms.
    max_tail_bytes: int = Field(default=2 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def the_marker_is_a_path_or_a_regex_but_not_both(self) -> "LogSourceSpec":
        if self.marker_field and not _FIELD_PATH.match(self.marker_field):
            raise ValueError(
                f"{INVALID_FIELD_PATH}: log_sources[{self.id}].marker_field must be"
                f" a dotted field path, e.g. `traceId` or `data.trace.id`, got"
                f" {self.marker_field!r}"
            )
        if self.marker and self.marker_field:
            raise ValueError(
                f"{LOG_SOURCE_AMBIGUOUS}: log_sources[{self.id}] declares both"
                " `marker` and `marker_field`; a source has one way to carry the id"
            )
        if self.format == "json-lines" and self.marker and not self.marker_field:
            raise ValueError(
                f"{LOG_SOURCE_AMBIGUOUS}: log_sources[{self.id}] is `json-lines`,"
                " so its id is a field: declare `marker_field` (or drop `format`)"
            )
        return self


class ContractSpec(BaseModel):
    """Where the mechanical truth of the API comes from (V11, read side).

    `source` is one reader or an ordered list of them, and the harness tries them
    in that order, taking the first *installed* one. That ordering is the fallback
    policy: a target which publishes no OpenAPI can be read from its own source
    code without making every other target install that reader.
    """

    model_config = ConfigDict(extra="forbid")

    source: str | list[str]
    location: str | None = None
    spec_version: str | None = None

    @model_validator(mode="after")
    def source_names_at_least_one_reader(self) -> "ContractSpec":
        if not self.attempts():
            raise ValueError(
                f"{CONTRACT_SOURCE_MISSING}: contract.source must name at least one"
                " reader (`source: openapi`, or an ordered list)"
            )
        return self

    def attempts(self) -> tuple[str, ...]:
        """The readers to try, in the declared order, with blanks dropped."""
        declared = self.source if isinstance(self.source, list) else [self.source]
        return tuple(
            item.strip()
            for item in declared
            if isinstance(item, str) and item.strip()
        )


class FixturesSpec(BaseModel):
    """Synthetic identity data. An e-mail domain is data, not a constant (V7)."""

    model_config = ConfigDict(extra="forbid")

    locale: str = "en_US"
    email_domain: str | None = None
    generators: dict[str, dict[str, str]] = Field(default_factory=dict)
    field_kinds: dict[str, str] = Field(default_factory=dict)


class TargetSpec(BaseModel):
    """A second origin a round may also touch, e.g. an admin port."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    environment_storage_key: str | None = None
    primary_response_prefixes: list[str] = Field(default_factory=list)


class RequestSourceSpec(BaseModel):
    """Where request shapes are read from (V11, write side)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["openapi", "bru", "postman", "inline"] = "inline"
    collection: str | None = None


class SecretRefSpec(BaseModel):
    """A reference to a secret, never the secret (P3).

    Resolution order is env var, then local gitignored file, then failure — there
    is no silent default, because a placeholder that "works" is how a sandbox
    case ends up running against production.
    """

    model_config = ConfigDict(extra="forbid")

    from_env: str | None = None
    from_file: str | None = None
    key: str | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> "SecretRefSpec":
        declared = [value for value in (self.from_env, self.from_file) if value]
        if len(declared) != 1:
            raise ValueError(
                "DESCRIPTOR_SECRET_INVALID: a secret takes exactly one of"
                " `from_env` or `from_file`"
            )
        if self.from_file and not self.key:
            raise ValueError(
                "DESCRIPTOR_SECRET_INVALID: `from_file` also needs `key` to name"
                " the entry inside that file"
            )
        return self


class ProjectDescriptor(BaseModel):
    """The root document. Every block past `environments` is optional (P4)."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    project: ProjectSpec
    #: The provider that owns this project's domain logic — the price model today,
    #: packs and step kinds later. The core resolves the id through the
    #: `heimdall_qa.providers` entry point group, so naming one here never becomes
    #: an import in the core. Absent means the neutral defaults.
    provider: str | None = None
    #: Where this project's content (cases, contracts, rounds, suites, baselines)
    #: lives, relative to the harness root. Declaring the *location* is setup; the
    #: content itself is the provider's and never enters this file. A project whose
    #: content sits at the harness root leaves the default in place.
    content_root: str = "."
    environments: dict[str, EnvironmentSpec]
    auth: dict[str, AuthSchemeSpec] = Field(default_factory=dict)
    environment_header: EnvironmentHeaderSpec | None = None
    routes: list[RouteSpec] = Field(default_factory=list)
    budgets: dict[str, BudgetSpec] = Field(default_factory=dict)
    errors: ErrorSpec = Field(default_factory=ErrorSpec)
    trace: TraceSpec = Field(default_factory=TraceSpec)
    log_sources: list[LogSourceSpec] = Field(default_factory=list)
    contract: ContractSpec | None = None
    fixtures: FixturesSpec = Field(default_factory=FixturesSpec)
    targets: dict[str, TargetSpec] = Field(default_factory=dict)
    request_source: RequestSourceSpec | None = None
    secrets: dict[str, SecretRefSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def at_least_one_environment(self) -> "ProjectDescriptor":
        if not self.environments:
            raise ValueError(
                f"{NO_ENVIRONMENT}: a descriptor with no environment has no base_url"
                " to talk to"
            )
        return self

    @model_validator(mode="after")
    def provider_id_is_a_slug(self) -> "ProjectDescriptor":
        """A provider id is looked up by name, so a typo must fail at load time.

        Without this, `provider: Acme` would only surface when a run asked for an
        oracle, as an installed-distribution error that names the wrong thing.
        """
        if self.provider is None:
            return self
        if not ID_PATTERN.match(self.provider):
            raise ValueError(
                f"{PROVIDER_INVALID}: provider must match [a-z0-9-]+, got"
                f" {self.provider!r}"
            )
        return self

    @model_validator(mode="after")
    def every_auth_is_declared(self) -> "ProjectDescriptor":
        known = set(self.auth) | {_NO_AUTH}
        for route in self.routes:
            if route.auth not in known:
                raise ValueError(
                    f"{UNKNOWN_AUTH}: route {route.prefix} declares auth"
                    f" '{route.auth}', which is not in auth"
                    f" ({', '.join(sorted(known))})"
                )
        return self

    @model_validator(mode="after")
    def every_budget_is_declared(self) -> "ProjectDescriptor":
        for route in self.routes:
            if route.budget is not None and route.budget not in self.budgets:
                raise ValueError(
                    f"{UNKNOWN_BUDGET}: route {route.prefix} declares budget"
                    f" '{route.budget}', which is not in budgets"
                    f" ({', '.join(sorted(self.budgets)) or 'none declared'})"
                )
        return self

    @model_validator(mode="after")
    def every_target_is_declared(self) -> "ProjectDescriptor":
        for route in self.routes:
            if route.target is not None and route.target not in self.targets:
                raise ValueError(
                    f"{UNKNOWN_TARGET}: route {route.prefix} points at target"
                    f" '{route.target}', which is not in targets"
                    f" ({', '.join(sorted(self.targets)) or 'none declared'})"
                )
        return self

    @model_validator(mode="after")
    def header_names_are_identifiers(self) -> "ProjectDescriptor":
        for name, scheme in self.auth.items():
            if not _HEADER_NAME.match(scheme.header):
                raise ValueError(
                    f"{INVALID_HEADER}: auth.{name}.header is not a valid HTTP"
                    f" header name: {scheme.header!r}"
                )
        header = self.environment_header
        if header is not None and not _HEADER_NAME.match(header.name):
            raise ValueError(
                f"{INVALID_HEADER}: environment_header.name is not a valid HTTP"
                f" header name: {header.name!r}"
            )
        for source in self.log_sources:
            if source.marker_field and not _FIELD_PATH.match(source.marker_field):
                raise ValueError(
                    f"{INVALID_FIELD_PATH}: log_sources[{source.id}].marker_field is not"
                    f" a dotted field path: {source.marker_field!r}"
                )
        return self

    @model_validator(mode="after")
    def no_route_declares_two_answers(self) -> "ProjectDescriptor":
        """Two entries for one prefix with different auth is a contradiction.

        The longest prefix wins at match time, so an exact duplicate silently
        resolves by document order — the reader cannot tell which one is in force.
        Refusing it is the only answer that does not depend on YAML ordering.
        """
        decided: dict[str, str] = {}
        for route in self.routes:
            previous = decided.setdefault(route.prefix, route.auth)
            if previous != route.auth:
                raise ValueError(
                    f"{DUPLICATE_ROUTE}: prefix {route.prefix} is declared twice with"
                    f" different auth ('{previous}' and '{route.auth}')"
                )
        return self

    @model_validator(mode="after")
    def trace_header_required_for_logs(self) -> "ProjectDescriptor":
        if self.log_sources and not (self.trace.header or "").strip():
            raise ValueError(
                f"{TRACE_HEADER_MISSING}: log_sources are declared, so the harness"
                " needs trace.header to correlate them; without it every log pack"
                " would report 'not measured' and never fail"
            )
        return self
