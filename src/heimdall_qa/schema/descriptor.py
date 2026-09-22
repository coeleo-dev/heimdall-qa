"""The project descriptor: what the harness must know about an API it did not write.

Design and rationale live in `docs/estudo-heimdall/02-descriptor-projeto.md`. The
shape follows its principle P2 — *role, never product name*: `environments`,
`routes`, `budgets`, never `nokr_web` or a hardcoded `/api/ingest`.

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
TRACE_HEADER_MISSING = "DESCRIPTOR_TRACE_HEADER_MISSING"
DUPLICATE_ROUTE = "DESCRIPTOR_DUPLICATE_ROUTE"
BASE_URL_MISSING = "DESCRIPTOR_BASE_URL_MISSING"
PREFIX_MISSING = "DESCRIPTOR_PREFIX_MISSING"

#: RFC 7230 token: what a header name may contain.
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

#: What a `project.id` may contain. It names the provider directory, so it is not
#: free-form prose.
ID_PATTERN = re.compile(r"^[a-z0-9-]+$")

_NO_AUTH = "none"


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
    #: Credential shape per environment (V6). `nk_test_` is product data, declared.
    prefixes: dict[str, str] = Field(default_factory=dict)


class EnvironmentHeaderSpec(BaseModel):
    """The header that selects the logical environment (V1)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    values: dict[str, str] = Field(default_factory=dict)


class RouteSpec(BaseModel):
    """One prefix, and the two decisions its requests need (V2 + V3)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    prefix: str = ""
    auth: str = _NO_AUTH
    budget: str | None = None
    async_mode: bool = Field(default=False, alias="async")
    require_environment_header: bool = False

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
    """What a validation error looks like, and what must never reach disk."""

    model_config = ConfigDict(extra="forbid")

    validation_status: int = 400
    envelope: Literal["rfc7807", "spring", "code_message", "none"] = "none"
    #: Package roots whose stack frames prove the trace came from the product (V9).
    product_packages: list[str] = Field(default_factory=list)
    redact: list[str] = Field(default_factory=list)


class TraceSpec(BaseModel):
    """The correlation header and the prefix the harness stamps into it (V10)."""

    model_config = ConfigDict(extra="forbid")

    header: str | None = None
    prefix: str = ""
    propagate_to_worker: bool = True


class LogSourceSpec(BaseModel):
    """One place a correlation line can appear. See `docs/estudo-heimdall/05-logs.md`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    #: Relative to the target repo root, never to the harness.
    path: str
    marker: str | None = None
    format: Literal["text", "logback", "json-lines"] = "text"
    marker_field: str | None = None
    async_mode: bool = Field(default=False, alias="async")


class ContractSpec(BaseModel):
    """Where the mechanical truth of the API comes from (V11, read side)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["openapi", "dto", "bru", "postman", "inline"]
    location: str | None = None
    spec_version: str | None = None


class FixturesSpec(BaseModel):
    """Synthetic identity data. `qa.nokr.dev` is data, not a constant (V7)."""

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
            if source.marker_field and not _HEADER_NAME.match(source.marker_field):
                raise ValueError(
                    f"{INVALID_HEADER}: log_sources[{source.id}].marker_field is not"
                    f" a valid field name: {source.marker_field!r}"
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
