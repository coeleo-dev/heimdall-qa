"""The descriptor as a run asks questions of it.

`schema/descriptor.py` says what may be declared. This module is the reading side,
and it is the only place in the core that turns a declaration into a decision:
which origin serves a path, how long a request may take, which header carries the
credential, where the logs are, which strings must never reach evidence.

Every one of those used to be a product literal in the core — a web base_url, an
environment header, an API key prefix, a Java package. A `ProjectView` with no
descriptor answers none of them and says so with a named error, which is the
honest outcome: not being able to measure is never a `pass`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from heimdall_qa.contract_source import ErrorTable
from heimdall_qa.contract_source import ResolvedContractSource
from heimdall_qa.contract_source import resolve_contract_source
from heimdall_qa.errors import HarnessError
from heimdall_qa.logs.collector import LogSourceTarget
from heimdall_qa.oracle import NeutralOracle
from heimdall_qa.oracle import Oracle
from heimdall_qa.provider import oracle_factory
from heimdall_qa.schema.descriptor import DEFAULT_ERROR_STATUSES
from heimdall_qa.schema.descriptor import UNKNOWN_ERROR_AXIS
from heimdall_qa.schema.descriptor import AuthSchemeSpec
from heimdall_qa.schema.descriptor import BudgetSpec
from heimdall_qa.schema.descriptor import LogSourceSpec
from heimdall_qa.schema.descriptor import ProjectDescriptor
from heimdall_qa.schema.descriptor import RouteSpec

#: What `routes[].auth` means when a route declares no credential at all.
NO_AUTH = "none"

#: The header the harness stamps the trace into when a project declares none.
DEFAULT_TRACE_HEADER = "X-Trace-Id"

#: The budget used when the descriptor declares none. The kernel owns this number
#: the way it owns the review bind port: a run with nothing declared still has to
#: fail somewhere, and 1500ms is the slowest a synchronous request may reasonably
#: take before the harness should stop waiting.
FALLBACK_BUDGET = BudgetSpec(budget=1500, fail=1500)

#: How long an async source is given when the case declares no `wait_logs_ms`. The
#: kernel owns this number the way it owns the fallback budget: a contract that says
#: `async: worker` is a promise that a line is coming, and waiting 0 ms for it is
#: how the worker's absence became "nothing to check" on 208 steps (F5 §3.6). It
#: costs a step nothing when the line does arrive, because the wait ends at the
#: first poll that answers.
FALLBACK_ASYNC_WAIT_MS = 2000


@dataclass(frozen=True)
class ProjectView:
    """The project in force, or the absence of one."""

    descriptor: ProjectDescriptor | None = None
    #: Source id -> file. A caller that knows better than the declaration — a test
    #: writing logs into `tmp_path`, or a future `--log-path` flag — overrides it
    #: here instead of rewriting the descriptor.
    log_paths: Mapping[str, str] = field(default_factory=dict)
    #: The directory the descriptor was read from, when a caller knows it. Relative
    #: paths inside the descriptor resolve against it, which is what the person who
    #: wrote `location: ../openapi.json` next to `qa/project.yaml` meant.
    descriptor_dir: Path | None = None
    #: What the contract source said about failures, when a caller read one. It is
    #: the middle answer of `error_status`: weaker than a declaration, stronger than
    #: the kernel's default, because a reader that read the code knows this API
    #: while the default only knows APIs in general.
    source_errors: Mapping[str, int] = field(default_factory=dict)
    #: The envelope the contract source read. Consulted only when the descriptor
    #: declares none, for the same reason.
    source_envelope: str | None = None

    def with_log_paths(self, **paths: str) -> "ProjectView":
        """Returns a view whose log sources point somewhere else."""
        return ProjectView(
            descriptor=self.descriptor,
            log_paths={**self.log_paths, **paths},
            descriptor_dir=self.descriptor_dir,
            source_errors=self.source_errors,
            source_envelope=self.source_envelope,
        )

    def with_source_errors(self, table: ErrorTable) -> "ProjectView":
        """Returns a view that also knows what a reader read about failures.

        This is how a project with a fresh descriptor gets the right statuses
        without anyone editing `errors.statuses`: the code already said them, and
        the declaration stays for the cases where a human knows better.
        """
        return ProjectView(
            descriptor=self.descriptor,
            log_paths=self.log_paths,
            descriptor_dir=self.descriptor_dir,
            source_errors={**self.source_errors, **table.statuses},
            source_envelope=table.envelope or self.source_envelope,
        )

    # -- provider ----------------------------------------------------------

    def provider_id(self) -> str | None:
        """The provider whose domain logic this project runs, if it declares one."""
        if self.descriptor is None:
            return None
        return self.descriptor.provider

    def oracle(self) -> Oracle:
        """A fresh book for one run.

        No provider means the neutral default, which is the honest answer for a
        project that never declared a price model: it measures the shapes of the
        API and has no opinion about what the numbers mean.
        """
        provider_id = self.provider_id()
        if not provider_id:
            return NeutralOracle()
        return oracle_factory(provider_id)()

    # -- origins -----------------------------------------------------------

    def content_root(self, root: Path) -> Path:
        """Where this project's content lives, given the harness root.

        Absolute declarations win; a relative one hangs off the harness root. The
        harness itself owns no content, so a project that declares nothing gets
        the root it was handed — which is what a target repo with its cases at
        the top level wants.
        """
        if self.descriptor is None:
            return root
        raw = (self.descriptor.content_root or ".").strip()
        if not raw or raw == ".":
            return root
        declared = Path(raw)
        return declared if declared.is_absolute() else root / declared

    def require(self) -> ProjectDescriptor:
        """The descriptor, or the failure that names why there is none."""
        if self.descriptor is None:
            raise HarnessError(
                code="DESCRIPTOR_MISSING",
                message="no project descriptor is in force, so no base_url is known",
                hint=(
                    "declare one at qa/project.yaml in the target repo, "
                    "or pass --descriptor PATH"
                ),
            )
        return self.descriptor

    def base_url(self, environment: str) -> str:
        """The origin of one declared environment."""
        declared = self.require().environments.get(environment)
        if declared is None:
            known = ", ".join(sorted(self.require().environments))
            raise HarnessError(
                code="DESCRIPTOR_UNKNOWN_ENVIRONMENT",
                message=(
                    f"environment '{environment}' is not declared"
                    f" (declared: {known})"
                ),
                hint="the round names an environment the descriptor does not describe",
            )
        return declared.base_url

    def route(self, path: str) -> RouteSpec | None:
        """The longest declared prefix that matches, or `None`.

        Longest wins, and not document order, so a specific route
        (`/api/ingest`) beats the catch-all (`/api/`) no matter how the file is
        ordered. An exact duplicate is refused at load time, so there is never a
        tie to break.

        With no descriptor every answer is `None`, which makes the policy
        questions below answer "nothing was declared" instead of failing: being
        unable to *measure* is an error, being unable to *ask* is just absence.
        """
        descriptor = self.descriptor
        if descriptor is None:
            return None
        best: RouteSpec | None = None
        for route in descriptor.routes:
            if not path.startswith(route.prefix):
                continue
            if best is None or len(route.prefix) > len(best.prefix):
                best = route
        return best

    def origin(self, path: str, environment: str) -> str:
        """Which origin serves `path` in `environment`."""
        route = self.route(path)
        if route is not None and route.target is not None:
            return self.require().targets[route.target].base_url
        return self.base_url(environment)

    def url(self, path: str, environment: str) -> str:
        """`path` against its origin. An absolute url passes through."""
        if str(path).startswith("http"):
            return str(path)
        return f"{self.origin(path, environment).rstrip('/')}{path}"

    # -- routing policy ----------------------------------------------------

    def budget(self, path: str) -> BudgetSpec:
        """The SLA `path` is held to: its route's, the default's, or the fallback."""
        route = self.route(path)
        descriptor = self.descriptor
        if descriptor is None:
            return FALLBACK_BUDGET
        if route is not None and route.budget is not None:
            declared = descriptor.budgets.get(route.budget)
            if declared is not None:
                return declared
        default = descriptor.budgets.get("default")
        return default if default is not None else FALLBACK_BUDGET

    def waits_for_async_worker(self, path: str) -> bool:
        """Whether a request here is settled by a worker rather than inline."""
        route = self.route(path)
        return bool(route is not None and route.async_mode)

    def catalog_unique(self, path: str) -> bool:
        """Whether a POST here creates a resource whose name must be unique."""
        route = self.route(path)
        return bool(route is not None and route.catalog_unique)

    def pace_key(self, path: str) -> str | None:
        """The pacer a request here shares, or `None` when it is not throttled."""
        route = self.route(path)
        if route is None:
            return None
        return route.pace_key

    # -- auth --------------------------------------------------------------

    def auth_for(self, path: str) -> AuthSchemeSpec | None:
        """The credential `path` requires, or `None` when it requires nothing."""
        name = self.auth_name_for(path)
        if name is None:
            return None
        return self.require().auth[name]

    def auth_name_for(self, path: str) -> str | None:
        """The *name* of the credential `path` requires, or `None`.

        The name is what a secret is found under; the spec is how it is worn.
        """
        route = self.route(path)
        if route is None or route.auth == NO_AUTH:
            return None
        return route.auth

    def auth_named(self, name: str) -> AuthSchemeSpec | None:
        """The credential declared under `name`, if any."""
        if self.descriptor is None:
            return None
        return self.descriptor.auth.get(name)

    def environment_header_name(self) -> str | None:
        """The header that selects the logical environment, if declared."""
        if self.descriptor is None or self.descriptor.environment_header is None:
            return None
        return self.descriptor.environment_header.name

    def environment_values(self) -> frozenset[str]:
        """Every value the environment header may carry."""
        if self.descriptor is None or self.descriptor.environment_header is None:
            return frozenset()
        return frozenset(self.descriptor.environment_header.values.values())

    def environment_value(self, environment: str) -> str:
        """What the environment header must say for `environment`."""
        if self.descriptor is None or self.descriptor.environment_header is None:
            return environment
        values = self.descriptor.environment_header.values
        return values.get(environment, environment)

    def isolation_environment(self) -> str | None:
        """The environment an `E-isolate`/`E-conflict` case crosses into."""
        if self.descriptor is None or self.descriptor.environment_header is None:
            return None
        return self.descriptor.environment_header.isolation

    def requires_environment_header(self, path: str) -> bool:
        """Whether this route reads the logical environment from a header."""
        route = self.route(path)
        return bool(route is not None and route.require_environment_header)

    def other_auth_names(self, scheme: AuthSchemeSpec) -> tuple[str, ...]:
        """Every declared scheme that is not `scheme`, by name."""
        if self.descriptor is None:
            return ()
        return tuple(
            name
            for name, other in self.descriptor.auth.items()
            if not _same_scheme(other, scheme)
        )

    # -- contract source ---------------------------------------------------

    def contract_source(self) -> ResolvedContractSource | None:
        """Which installed reader should read this API's contract, or `None`.

        The declaration is an ordered list, so a target that publishes no OpenAPI
        can fall back to its own source code without every other target paying for
        that reader. The first *installed* one wins.

        A declaration no installed reader can serve is the named
        `CONTRACT_SOURCE_UNAVAILABLE` and never a quietly empty contract: "I could
        not read it" must not look like "there is nothing to read".
        """
        spec = None if self.descriptor is None else self.descriptor.contract
        if spec is None:
            return None
        return ResolvedContractSource(
            source=resolve_contract_source(spec.attempts()),
            location=self.contract_location(),
            spec_version=spec.spec_version,
        )

    def contract_location(self) -> str | None:
        """Where the declared contract source reads from, resolved.

        A relative path is read against the descriptor's own directory, because
        that is where the person who wrote it was standing: `qa/project.yaml` saying
        `../openapi.json` means the repository root, not the shell's cwd. An
        absolute path or a URL passes through untouched.

        With no descriptor directory to anchor to — a view built in a test, or a
        caller that only had the model — the declaration is returned as written,
        which keeps the old behaviour for everyone who never depended on this.
        """
        spec = None if self.descriptor is None else self.descriptor.contract
        if spec is None or not spec.location:
            return None
        raw = spec.location.strip()
        if not raw or raw.startswith(("http://", "https://")) or Path(raw).is_absolute():
            return raw or None
        if self.descriptor_dir is None:
            return raw
        return str((self.descriptor_dir / raw).resolve())

    # -- error envelope, trace, fixtures -----------------------------------

    def error_status(self, axis: str) -> int:
        """The status a failure on `axis` returns: declared, read, or its default.

        `validation` is not one number in the wild — the reference corpus answers
        400 for a malformed body and 422 for a product rule — so the axis is named
        and a project may override the default. An axis with neither a declaration
        nor a default is a caller's typo, and answering 400 for it would hide that.

        Precedence, in that order: what the descriptor declares, what the contract
        source read, and the kernel's default. A declaration wins over the read even
        when they disagree, because a human who wrote a number meant it; `discover`
        is where that disagreement is reported.
        """
        declared = self.declared_error_status(axis)
        if declared is not None:
            return declared
        if axis in self.source_errors:
            return self.source_errors[axis]
        default = DEFAULT_ERROR_STATUSES.get(axis)
        if default is None:
            raise HarnessError(
                code=UNKNOWN_ERROR_AXIS,
                message=f"no status is known for error axis '{axis}'",
                hint=(
                    f"declare errors.statuses.{axis}, or use one of: "
                    + ", ".join(sorted(DEFAULT_ERROR_STATUSES))
                ),
            )
        return default

    def declared_error_status(self, axis: str) -> int | None:
        """What the descriptor itself says for `axis`, or `None` when it is silent."""
        if self.descriptor is None:
            return None
        return self.descriptor.errors.statuses.get(axis)

    def declared_error_statuses(self) -> dict[str, int]:
        """Every axis the descriptor answers itself, sorted by axis.

        `discover` prints these next to the axes the source could not answer: they
        are the middle rung of the staircase, and a report that showed an axis as
        unanswered while the descriptor answered it would send a reader to fix
        something that is already fixed.
        """
        if self.descriptor is None:
            return {}
        return dict(sorted(self.descriptor.errors.statuses.items()))

    def validation_status(self) -> int:
        """The status a malformed body or a missing required field returns."""
        return self.error_status("validation")

    def error_envelope(self) -> str:
        """The shape of the error body: declared, read, or `none`.

        `none` on both sides is the honest answer for a project that declared no
        envelope and whose source says nothing: it is what the `http.error` pack
        checks against when it verifies that a 2xx body is not an error shape.
        """
        if self.descriptor is not None and self.descriptor.errors.envelope != "none":
            return self.descriptor.errors.envelope
        return self.source_envelope or "none"

    def product_packages(self) -> tuple[str, ...]:
        if self.descriptor is None:
            return ()
        return tuple(self.descriptor.errors.product_packages)

    def redact_patterns(self) -> tuple[str, ...]:
        """Glob patterns that must never appear in evidence, as regexes."""
        if self.descriptor is None:
            return ()
        return tuple(_glob_to_regex(item) for item in self.descriptor.errors.redact)

    def trace_header(self) -> str:
        """The header the harness stamps the trace into.

        `X-Trace-Id` is the harness's own convention and not a product name, the
        same way `--verbose` is; a project that uses another one declares
        `trace.header` and every pack follows it.
        """
        if self.descriptor is None:
            return DEFAULT_TRACE_HEADER
        return self.descriptor.trace.header or DEFAULT_TRACE_HEADER

    def trace_prefix(self) -> str:
        if self.descriptor is None:
            return ""
        return self.descriptor.trace.prefix

    def email_domain(self) -> str:
        if self.descriptor is None or not self.descriptor.fixtures.email_domain:
            return "example.com"
        return self.descriptor.fixtures.email_domain

    def locale(self) -> str:
        if self.descriptor is None:
            return "en_US"
        return self.descriptor.fixtures.locale

    def field_kinds(self) -> dict[str, str]:
        """Field name -> fixture kind, declared by the project (V7)."""
        if self.descriptor is None:
            return {}
        return dict(self.descriptor.fixtures.field_kinds)

    def generator_kinds(self) -> dict[str, str]:
        """Fixture kind -> generator implementation, declared by the project.

        The core knows only built-in scalars, so a declared kind the core cannot
        build is a descriptor mistake, and it is reported as one rather than
        silently falling back to a placeholder that would pass a weaker check.
        """
        if self.descriptor is None:
            return {}
        return {
            kind: entry.get("kind", "")
            for kind, entry in self.descriptor.fixtures.generators.items()
        }

    # -- logs and request sources ------------------------------------------

    def log_sources(self) -> tuple[LogSourceTarget, ...]:
        """Every declared source, with the file it points at already resolved.

        The runner reads **all** of them, not the two ids it used to know by name:
        a project that declares a third service had it silently ignored, which is
        how `admin` came to be declared and never collected.
        """
        if self.descriptor is None:
            return ()
        return tuple(
            LogSourceTarget(spec=source, path=self._log_file(source))
            for source in self.descriptor.log_sources
        )

    def log_path(self, source_id: str) -> Path | None:
        """Where `source_id` writes, or `None` when nothing declares it."""
        override = self.log_paths.get(source_id)
        if override is not None:
            return _resolve(override, self.descriptor_dir)
        if self.descriptor is None:
            return None
        for source in self.descriptor.log_sources:
            if source.id == source_id:
                return self._log_file(source)
        return None

    def _log_file(self, source: LogSourceSpec) -> Path:
        override = self.log_paths.get(source.id)
        return _resolve(override if override is not None else source.path, self.descriptor_dir)

    def required_log_path(self, source_id: str) -> Path:
        """`log_path` or the failure that names the missing declaration."""
        path = self.log_path(source_id)
        if path is None:
            raise HarnessError(
                code="DESCRIPTOR_LOG_SOURCE_MISSING",
                message=f"no log source '{source_id}' is declared",
                hint="add it to log_sources in the project descriptor",
            )
        return path

    def request_collection(self) -> Path | None:
        """Where the declared request source reads its files from, if it has one."""
        if self.descriptor is None or self.descriptor.request_source is None:
            return None
        collection = self.descriptor.request_source.collection
        return Path(collection) if collection else None

    def required_request_collection(self) -> Path:
        collection = self.request_collection()
        if collection is None:
            raise HarnessError(
                code="DESCRIPTOR_REQUEST_SOURCE_MISSING",
                message="no request_source collection is declared",
                hint="declare request_source.collection in the project descriptor",
            )
        return collection


def _resolve(raw: str, anchor: Path | None = None) -> Path:
    """A declared path, anchored where the descriptor's own paths are anchored.

    `contract.location` resolves against the descriptor's directory; a log source
    has to resolve the same way, or `path: ../logs/web.log` means one file when the
    run starts from `qa/` and another when it starts from the repository root.
    """
    path = Path(raw)
    if path.is_absolute():
        return path
    return (anchor or Path.cwd()) / path


def _same_scheme(left: AuthSchemeSpec, right: AuthSchemeSpec) -> bool:
    return left.header == right.header and left.scheme == right.scheme


def _glob_to_regex(pattern: str) -> str:
    """`key_*` means "the `key_` prefix", and the regex has to say that."""
    return re.escape(pattern).replace(r"\*", ".*")
