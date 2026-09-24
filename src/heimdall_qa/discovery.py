"""The generator that reads an API and writes down what it could not read.

`scaffold-endpoint` starts from a contract a human already wrote. `discover` starts
from the API itself: it asks the resolved reader for a schema, writes one contract
per endpoint, generates the cases `coverage.expand` derives from each, and writes a
report of everything the source could not say. The report is the point as much as the
generated files are — a discovered tree is only trustworthy if its holes are named.

Three rules shape the whole module.

**Nothing is invented.** A field with no example produces a case whose seed stays a
`TODO` rather than a fabricated value; an operation with no declared 2xx produces
`status: TODO`; a path parameter with no known value produces `path_values: TODO`.
`validate` and the runner both refuse a `TODO`, so a hole in the source becomes a
loud, named failure instead of a green case that verifies nothing.

**Nothing is overwritten.** An existing contract is kept, and an existing case file
is kept, so a second `discover` after a human has filled in the gaps changes nothing.
`--force` is the explicit way to say "I meant to replace that".

**A gap names the axis it affects.** `Gap.axis` is what makes the report actionable:
it says whether the missing fact costs one case or twelve, and which family of cases
stays uncovered until someone answers.
"""

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any

from heimdall_qa.contract_source import SURFACE
from heimdall_qa.contract_source import ApiSchema
from heimdall_qa.contract_source import ContractSource
from heimdall_qa.contract_source import EndpointSchema
from heimdall_qa.contract_source import ErrorTable
from heimdall_qa.contract_source import FieldSchema
from heimdall_qa.contract_source import Gap
from heimdall_qa.contract_source import RuleSchema
from heimdall_qa.contract_source import available_contract_sources
from heimdall_qa.contract_source import load_contract_source
from heimdall_qa.coverage import area_from_endpoint
from heimdall_qa.coverage import expand
from heimdall_qa.document import dump
from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.scaffold import scaffold_endpoint
from heimdall_qa.schema.load import existing_case_ids
from heimdall_qa.schema.models import Contract

#: The baseline every discovered contract points at. A generated tree has to be
#: runnable, and "the diff starts from nothing" is the only baseline a generator can
#: honestly assume.
EMPTY_BASELINE = "baselines/empty.json"

#: The sentinel written where the source could not supply a value. `validate` and the
#: runner refuse it, which is the entire point of writing it down.
PLACEHOLDER = "TODO"

#: One path parameter, as a source spells it: `/items/{id}`. Matched narrowly so a
#: brace that is not a parameter — a literal in a path, a glob — is left alone.
_PATH_PARAMETER = re.compile(r"\{([^{}/]+)\}")

#: Two endpoints landing on one area would mean one silently replacing the other, so
#: it is a failure that names both routes instead.
DISCOVERY_AREA_COLLISION = "DISCOVERY_AREA_COLLISION"

#: `--route` named a route the source does not describe. It is an error and not an
#: empty report, because "the API has no such route" and "I misread the spelling"
#: must not look the same.
DISCOVERY_ROUTE_UNKNOWN = "DISCOVERY_ROUTE_UNKNOWN"

#: A `discover` with no declaration to read and no `--source` to fall back on.
CONTRACT_SOURCE_MISSING = "DESCRIPTOR_CONTRACT_SOURCE_MISSING"


@dataclass(frozen=True)
class DiscoveredEndpoint:
    """One route: the contract it produced, its cases, and what it left open."""

    endpoint: str
    area: str
    contract_path: Path
    case_ids: tuple[str, ...]
    written_case_ids: tuple[str, ...]
    gaps: tuple[Gap, ...]
    #: What was left as `TODO` in the generated cases, by where it sits. It is a
    #: separate list from `gaps` because it is a different kind of block: a gap is
    #: coverage nobody has written yet, a placeholder stops the cases that exist.
    placeholders: tuple[tuple[str, str], ...]
    #: Headers the source says the route requires. The contract has no place to keep
    #: them — `auth` and `idempotency` are the only two the harness wears — so they
    #: are reported rather than dropped: a reader that saw them and said nothing
    #: would make the silence look like "this route needs no header".
    required_headers: tuple[str, ...] = ()
    #: The condition the reader put on the route (`@Profile`), when it stated one. It
    #: is only ever set on an endpoint that *was* generated, which happens when
    #: `--route` named it: the report says so instead of listing the route under "not
    #: generated", where it would be a lie.
    conditional: str | None = None

    @property
    def kept_case_ids(self) -> tuple[str, ...]:
        written = set(self.written_case_ids)
        return tuple(case_id for case_id in self.case_ids if case_id not in written)


@dataclass(frozen=True)
class DiscoveryReport:
    """Everything one `discover` produced, in the order the source described it."""

    source: str
    provider: str
    location: str | None
    contracts_dir: Path
    cases_dir: Path
    report_path: Path
    endpoints: tuple[DiscoveredEndpoint, ...] = ()
    #: What the source said about failures, whether or not anything read it.
    errors: ErrorTable = ErrorTable()
    #: Axes where the descriptor declares a status the source contradicts, as
    #: `(axis, declared, read)`. The declaration still wins — a human who wrote a
    #: number meant it — so this is the line that says the two disagree.
    disagreements: tuple[tuple[str, int, int], ...] = ()
    #: Axes the descriptor answers that the source did not, as `(axis, status)`.
    #: The middle rung of the staircase: showing them as unanswered would send a
    #: reader to fix a fact that is already written down.
    declared_only: tuple[tuple[str, int], ...] = ()
    #: Gaps about the surface itself (`Gap.endpoint == SURFACE`): the `auth` no
    #: source can be asked, a denylist read from a constant. They belong to no route,
    #: so a report that filed them under one would lose them — and `*` is not a route
    #: anyone would look up.
    surface_gaps: tuple[Gap, ...] = ()
    #: Routes the reader saw but would not put on the surface, as
    #: `(endpoint, condition)`. They are kept out of `endpoints` because a generated
    #: contract for a route the deployment does not serve is a case that fails for a
    #: reason the product never stated — and they are still reported, because a route
    #: that vanished from the report is a route nobody remembers to cover. Naming one
    #: with `--route` empties this: the route was generated after all, and the
    #: condition moves onto the endpoint.
    off_surface: tuple[tuple[str, str], ...] = ()
    #: True when the run was asked to read and report without writing a file.
    dry_run: bool = False

    def gap_count(self) -> int:
        return sum(len(endpoint.gaps) for endpoint in self.endpoints)

    def written_case_count(self) -> int:
        return sum(len(endpoint.written_case_ids) for endpoint in self.endpoints)

    def kept_case_count(self) -> int:
        return sum(len(endpoint.kept_case_ids) for endpoint in self.endpoints)

    def blocked(self) -> tuple[DiscoveredEndpoint, ...]:
        return tuple(endpoint for endpoint in self.endpoints if endpoint.placeholders)


def discover(
        project: ProjectView,
        *,
        contracts_dir: Path,
        cases_dir: Path,
        report_path: Path,
        source: str | None = None,
        location: str | None = None,
        route: str | None = None,
        dry_run: bool = False,
        force: bool = False,
) -> DiscoveryReport:
    """Read the API, write one contract per endpoint, and write the report.

    `source` and `location` override the descriptor's declaration for one run, which
    is how a project tries the next reader on the staircase — or a document it has
    not committed to yet — without editing the descriptor first.

    `route` narrows the run to one endpoint, which is how a reader is checked
    against a contract that already exists: read `POST /api/ingest`, diff the
    fields, and change nothing else. `dry_run` reads and reports without writing,
    so the answer to "what would you generate" never costs a checkout.
    """
    reader, where = _reader(project, source, location)
    schema = reader.read(where)
    if route is not None:
        schema = _only(schema, route)
    schema, off_surface = _on_the_surface(schema, explicit=route is not None)
    # The source's own table moves into the view before anything is generated, so
    # a project that declares nothing still gets the statuses its code answered.
    project = project.with_source_errors(schema.errors)
    report = _walk(
        schema=schema,
        reader=reader,
        location=where,
        contracts_dir=contracts_dir,
        cases_dir=cases_dir,
        report_path=report_path,
        project=project,
        force=force,
        dry_run=dry_run,
        off_surface=off_surface,
    )
    if dry_run:
        return report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(report), encoding="utf-8")
    return report


def _on_the_surface(
    schema: ApiSchema,
    *,
    explicit: bool,
) -> tuple[ApiSchema, tuple[tuple[str, str], ...]]:
    """The schema without its conditional routes, and the routes taken off it.

    A reader marks a route conditional when the deployment may not serve it — a
    `@Profile`-gated controller, a flag. Generating its cases would produce failures
    that say nothing about the product, so it stays out of the walk and the report
    names it.

    `explicit` is the one exception: naming the route with `--route` is a human saying
    "this profile is on where I am running", and the reader does not get to overrule
    an instruction it was given. Nothing is reported as "not generated" then, because
    it was generated — the endpoint carries its own condition instead.
    """
    if explicit:
        return schema, ()
    off = tuple(
        (endpoint.endpoint(), endpoint.conditional)
        for endpoint in schema.endpoints
        if endpoint.conditional is not None
    )
    if not off:
        return schema, off
    dropped = {route for route, _ in off}
    return (
        replace(
            schema,
            endpoints=tuple(
                endpoint
                for endpoint in schema.endpoints
                if endpoint.conditional is None
            ),
            gaps=tuple(gap for gap in schema.gaps if gap.endpoint not in dropped),
        ),
        off,
    )


def harness_endpoint(endpoint: EndpointSchema) -> str:
    """The endpoint as the harness spells it, which is not how a source spells it.

    A Spring handler declares `/items/{id}` and an OpenAPI path item declares the
    same, because that is the URI template of the *protocol*. The harness's own
    spelling is `{{id}}`, because that is what a case interpolates: a contract
    written with single braces produces a request for a literal `/items/{id}`, and
    the run fails on a URL the source never promised.

    The translation lives here, at the one place a source becomes harness YAML, and
    not in `EndpointSchema`: the IR describes the API, and an API's paths have one
    brace. A reader that emitted `{{...}}` would be writing the harness's dialect
    from behind the seam that exists to hide it.
    """
    return _PATH_PARAMETER.sub(r"{{\1}}", endpoint.endpoint())


def contract_payload(
        endpoint: EndpointSchema,
        area: str,
        project: ProjectView,
) -> dict[str, Any]:
    """The contract YAML for one endpoint, keyed in the order a reader reads it.

    `auth` is the one attribute no source can be asked and no reader may invent: a
    controller declares *that* a credential is required, never which scheme the
    harness should wear. So it comes from the descriptor's route table, and a route
    the descriptor does not cover becomes a gap in the report rather than a silent
    `none`.
    """
    payload: dict[str, Any] = {
        "endpoint": harness_endpoint(endpoint),
        # Declared and not derived, because `expand` names every case id after it:
        # `{id}` as a last path segment is not a usable case prefix.
        "area": area,
        "auth": project.auth_name_for(endpoint.path) or "none",
        "baseline": EMPTY_BASELINE,
    }
    if endpoint.idempotency != "none":
        payload["idempotency"] = endpoint.idempotency
    if endpoint.dedup:
        payload["dedup"] = endpoint.dedup
    if endpoint.async_mode is not None:
        payload["async"] = "worker" if endpoint.async_mode else "sync"
    if endpoint.resource_id_in_path:
        payload["resource_id_in_path"] = True
    payload["fields"] = {
        field_schema.name: _field_payload(field_schema) for field_schema in endpoint.fields
    }
    if endpoint.rules:
        payload["rules"] = [_rule_payload(rule) for rule in endpoint.rules]
    return payload


def _only(schema: ApiSchema, route: str) -> ApiSchema:
    """The same schema with one endpoint, or the failure that names the spelling."""
    found = schema.find(route)
    if found is None:
        known = ", ".join(schema.names()) or "none"
        raise HarnessError(
            code=DISCOVERY_ROUTE_UNKNOWN,
            message=f"the contract source describes no route '{route.strip()}'",
            hint=f"it describes: {known}",
        )
    return ApiSchema(
        source=schema.source,
        endpoints=(found,),
        gaps=tuple(
            gap
            for gap in schema.gaps
            if gap.endpoint == SURFACE or _same_route(gap.endpoint, route)
        ),
        errors=schema.errors,
    )


def _same_route(left: str, right: str) -> bool:
    return left.strip().upper().split() == right.strip().upper().split()


def area_for(endpoint: str) -> str:
    """The area one endpoint's contract and cases are filed under.

    It reuses `area_from_endpoint` — the last path segment — and departs from it
    only when that segment is a parameter: `/toy/items` becomes `items`, and
    `/toy/items/{id}` becomes `toy-items-id` rather than `{id}`, which would name a
    case file `{id}-H01.yaml`.
    """
    area = area_from_endpoint(endpoint)
    if "{" not in area:
        return area
    return path_area(endpoint)


def path_area(endpoint: str) -> str:
    """The whole path as an area: `/platform/dashboard/metrics` -> the joined parts.

    It is the fallback for two routes that would otherwise share a file, and it is
    the *whole* path rather than a longer suffix so that the name never depends on
    which route was read first.
    """
    path = endpoint.strip().split()[-1].strip("/")
    return "-".join(part.strip("{}") for part in path.split("/") if part)


def _reader(
        project: ProjectView,
        override: str | None,
        location: str | None,
) -> tuple[ContractSource, str | None]:
    """The reader in force and where it reads, for this project or these overrides."""
    where = location if location is not None else project.contract_location()
    if override is not None:
        return load_contract_source(override), where
    resolved = project.contract_source()
    if resolved is None:
        raise HarnessError(
            code=CONTRACT_SOURCE_MISSING,
            message="the project describes no contract source to discover from",
            hint=(
                "declare `contract: {source: openapi, location: ./openapi.json}` in"
                " the project descriptor, or pass --source NAME for one run"
            ),
        )
    return load_contract_source(resolved.source), resolved.location


def _walk(
        *,
        schema: ApiSchema,
        reader: ContractSource,
        location: str | None,
        contracts_dir: Path,
        cases_dir: Path,
        report_path: Path,
        project: ProjectView,
        force: bool,
        dry_run: bool,
        off_surface: tuple[tuple[str, str], ...] = (),
) -> DiscoveryReport:
    if not dry_run:
        contracts_dir.mkdir(parents=True, exist_ok=True)
        _ensure_baseline(contracts_dir.parent)
    areas = _areas(schema)
    gaps_by_route: dict[str, list[Gap]] = {}
    for gap in schema.gaps:
        gaps_by_route.setdefault(gap.endpoint, []).append(gap)
    return DiscoveryReport(
        source=schema.source,
        provider=available_contract_sources().get(reader.name, reader.name),
        location=location,
        contracts_dir=contracts_dir,
        cases_dir=cases_dir,
        report_path=report_path,
        errors=schema.errors,
        disagreements=_disagreements(schema.errors, project),
        declared_only=_declared_only(schema.errors, project),
        surface_gaps=tuple(gaps_by_route.pop(SURFACE, ())),
        off_surface=off_surface,
        dry_run=dry_run,
        endpoints=tuple(
            _endpoint(
                endpoint=endpoint,
                area=areas[endpoint.endpoint()],
                gaps=tuple(gaps_by_route.get(endpoint.endpoint(), ())),
                contracts_dir=contracts_dir,
                cases_dir=cases_dir,
                project=project,
                force=force,
                dry_run=dry_run,
            )
            for endpoint in schema.endpoints
        ),
    )


def _disagreements(
        table: ErrorTable,
        project: ProjectView,
) -> tuple[tuple[str, int, int], ...]:
    """Axes where the descriptor declares a status the source contradicts.

    Only a *declaration* counts. Comparing against the kernel's default would fire
    on every project that never wrote an `errors` block, which is not a disagreement
    — it is the default doing its job, and the source's answer wins there anyway.
    """
    found: list[tuple[str, int, int]] = []
    for axis, read in sorted(table.statuses.items()):
        declared = project.declared_error_status(axis)
        if declared is not None and declared != read:
            found.append((axis, declared, read))
    return tuple(found)


def _declared_only(
        table: ErrorTable,
        project: ProjectView,
) -> tuple[tuple[str, int], ...]:
    """Axes the descriptor answers that the source did not read at all.

    `auth` is the recurring one: a filter refuses the credential before any
    `@ExceptionHandler` sees the request, so no source can read the status, and the
    descriptor is where a target has to write it. Reporting it as unanswered while
    the descriptor answered it is the kind of stale line a reader learns to ignore.
    """
    return tuple(
        (axis, status)
        for axis, status in project.declared_error_statuses().items()
        if axis not in table.statuses
    )


def _areas(schema: ApiSchema) -> dict[str, str]:
    """Route -> area, disambiguated by method when one path hosts several operations.

    A path with a single operation keeps the bare area — `items`, `health` — because
    that is the name a human would have chosen. A path hosting two or more gets the
    method prefixed for all of them, which is decided from the document and not from
    the order it is walked: adding a `DELETE` to a path must not rename the `GET`'s
    contract.

    Two disambiguated names can still collide, and on a real API they do: two `GET`s
    whose last segment is the same word (`.../users/{id}/metrics` and
    `/dashboard/metrics`). Then every route in the colliding group takes its whole
    path instead — all of them at once, because renaming one of the pair would make
    the result depend on which was walked first.

    Whatever is left is checked rather than assumed.
    """
    by_base: dict[str, list[str]] = {}
    for endpoint in schema.endpoints:
        by_base.setdefault(area_for(endpoint.endpoint()), []).append(endpoint.endpoint())
    found: dict[str, str] = {}
    for base, routes in by_base.items():
        for route in routes:
            if len(routes) == 1:
                found[route] = base
                continue
            method = route.strip().split()[0].lower()
            found[route] = f"{method}-{base}"
    disputed = {area for area, count in Counter(found.values()).items() if count > 1}
    for route, area in found.items():
        if area in disputed:
            found[route] = path_area(route)
    return _unique(found)


def _unique(areas: dict[str, str]) -> dict[str, str]:
    """Refuses two routes that would share one file, naming both."""
    taken: dict[str, str] = {}
    for route, area in areas.items():
        other = taken.get(area)
        if other is not None:
            raise HarnessError(
                code=DISCOVERY_AREA_COLLISION,
                message=(
                    f"'{route}' and '{other}' would both be written to"
                    f" contracts/{area}.yaml"
                ),
                hint=(
                    "rename one path, or split the document: an area names the"
                    " contract file and the prefix of every case id"
                ),
            )
        taken[area] = route
    return areas


def _endpoint(
        *,
        endpoint: EndpointSchema,
        area: str,
        gaps: tuple[Gap, ...],
        contracts_dir: Path,
        cases_dir: Path,
        project: ProjectView,
        force: bool,
        dry_run: bool,
) -> DiscoveredEndpoint:
    contract_path = contracts_dir / f"{area}.yaml"
    # The contract is built in memory first, so `--dry-run` answers "which cases
    # would this produce" without a checkout paying for a file it must not keep.
    contract = Contract.model_validate(contract_payload(endpoint, area, project))
    expected = [item.case_id for item in expand(contract)]
    cases_file = cases_dir / f"{area}.yaml"
    if dry_run:
        return DiscoveredEndpoint(
            endpoint=endpoint.endpoint(),
            area=area,
            contract_path=contract_path,
            case_ids=tuple(expected),
            written_case_ids=tuple(expected),
            gaps=gaps,
            placeholders=_placeholder_report(endpoint),
            required_headers=endpoint.required_headers,
            conditional=endpoint.conditional,
        )
    _write_contract(contract_path, contract_payload(endpoint, area, project), force=force)
    already = set(existing_case_ids(cases_file))
    written = [case_id for case_id in expected if case_id not in already]
    ids = scaffold_endpoint(
        contract_path,
        cases_dir,
        project=project,
        content_base=contracts_dir.parent,
        force=force,
        h01_status=endpoint.success_status,
        extra=_placeholders(endpoint),
    )
    return DiscoveredEndpoint(
        endpoint=endpoint.endpoint(),
        area=area,
        contract_path=contract_path,
        case_ids=tuple(ids),
        written_case_ids=tuple(written),
        gaps=gaps,
        placeholders=_placeholder_report(endpoint),
        required_headers=endpoint.required_headers,
        conditional=endpoint.conditional,
    )


def _write_contract(path: Path, payload: Mapping[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.write_text(dump(payload), encoding="utf-8")


def _ensure_baseline(content_root: Path) -> None:
    """Write the empty baseline every generated contract points at, once.

    Without it each generated case fails at load with "baseline not found", which
    makes a freshly discovered tree useless in a way that reads as a harness bug
    rather than as a missing file.
    """
    path = content_root / EMPTY_BASELINE
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")


def _placeholders(endpoint: EndpointSchema) -> dict[str, Any]:
    """The keys every generated case needs as much as it needs a contract.

    A path parameter is the only one today: without a value the URL keeps its
    `{id}`, and the case quietly exercises a route that does not exist.
    """
    if not endpoint.path_params:
        return {}
    return {"path_values": {name: PLACEHOLDER for name in endpoint.path_params}}


def _placeholder_report(endpoint: EndpointSchema) -> tuple[tuple[str, str], ...]:
    """Where a `TODO` was written, and why, in the order a reader fixes them."""
    found: list[tuple[str, ...]] = []
    if endpoint.success_status is None:
        found.append(("expect.status", "the source declares no 2xx for this operation"))
    found.extend(
        (
            f"path_values.{name}",
            "the source says a path parameter exists but not what value it takes",
        )
        for name in endpoint.path_params
    )
    if _body_needs_a_baseline(endpoint):
        found.append(
            (
                "baseline",
                "the source declares required fields and no example for any of them,"
                " so `baselines/empty.json` cannot be the happy body — every generated"
                " case would send `{}` and report a 400 the report never explained",
            )
        )
    return tuple(found)


def _body_needs_a_baseline(endpoint: EndpointSchema) -> bool:
    """Whether the happy body has a hole no `TODO` marks.

    A required field with no example is a value nobody supplied. The generated cases
    are still honest — they omit it, overrun it, break its pattern — but the one case
    that has to send it *correctly* has nothing to send, and that is a fact about the
    tree the report has to state. Otherwise a freshly discovered POST looks runnable
    and fails four times out of fourteen for a reason it does not name.
    """
    return any(field.required and field.example is None for field in endpoint.fields)


def _field_payload(field_schema: FieldSchema) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "required": field_schema.required,
        "json": field_schema.json_name or field_schema.name,
    }
    if field_schema.max_length is not None:
        payload["max_length"] = field_schema.max_length
    if field_schema.pattern is not None:
        payload["pattern"] = field_schema.pattern
    if field_schema.max_keys is not None:
        payload["max_keys"] = field_schema.max_keys
    if field_schema.denylist:
        payload["denylist"] = list(field_schema.denylist)
    if field_schema.json_alias:
        payload["json_alias"] = True
    if field_schema.example is not None:
        payload["example"] = field_schema.example
    if field_schema.invalid is not None:
        payload["invalid"] = field_schema.invalid
    return payload


def _rule_payload(rule: RuleSchema) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": rule.id, "status": rule.status}
    if rule.code:
        payload["code"] = rule.code
    if rule.error:
        payload["error"] = rule.error
    return payload


def render_report(report: DiscoveryReport) -> str:
    """The markdown a human opens first.

    It answers four questions in that order: what was read, what the source said
    about failures, which endpoint still needs a decision, and which generated cases
    cannot run until someone decides.
    """
    lines = [
        "# Contract discovery",
        "",
        f"- source: `{report.source}` (provided by `{report.provider}`)",
        f"- location: `{report.location or '-'}`",
        f"- contracts: {len(report.endpoints)} written or kept in"
        f" `{report.contracts_dir}`",
        f"- cases: {report.written_case_count()} written,"
        f" {report.kept_case_count()} kept in `{report.cases_dir}`",
        f"- gaps: {report.gap_count()} across"
        f" {sum(1 for endpoint in report.endpoints if endpoint.gaps)} of"
        f" {len(report.endpoints)} endpoints",
        "",
    ]
    if report.dry_run:
        lines.extend(
            [
                "> Read-only run: nothing was written. The paths above say where a",
                "> run without `--dry-run` would put the files.",
                "",
            ]
        )
    lines.extend(_surface_section(report))
    lines.extend(_error_table(report))
    for endpoint in report.endpoints:
        lines.append(f"## {endpoint.endpoint}")
        lines.append("")
        lines.append(f"- contract: `{endpoint.contract_path.name}`")
        lines.append(
            f"- cases: {len(endpoint.case_ids)} ({len(endpoint.written_case_ids)}"
            f" written, {len(endpoint.kept_case_ids)} kept) in `{endpoint.area}/`"
        )
        if endpoint.required_headers:
            # No case family covers a required header today, so this is the report's
            # only use for it. Dropping it would read as "the route needs nothing".
            lines.append(
                "- requires headers: "
                + ", ".join(f"`{name}`" for name in endpoint.required_headers)
            )
        if endpoint.conditional is not None:
            # Only ever set when `--route` named the route: the reader gated it and the
            # human overrode the gate, so the contract exists and the condition is the
            # caveat on it. Without this line the override would be invisible.
            lines.append(
                f"- conditional: `{endpoint.conditional}` — generated because `--route`"
                " named it; it exists only where the condition holds"
            )
        lines.append("")
        lines.extend(_gap_table(endpoint))
    lines.extend(_blocked_table(report))
    return "\n".join(lines).rstrip() + "\n"


def _surface_section(report: DiscoveryReport) -> list[str]:
    """The facts that belong to the API and not to a route, said before any route.

    Two kinds: the gaps a reader reports once for the whole surface, and the routes it
    read but kept off it. Both would be lost if they were filed under an endpoint —
    there is no endpoint to file them under — so they get the top of the report, where
    a human reads them before deciding anything.
    """
    lines: list[str] = []
    if report.surface_gaps:
        lines.extend(
            [
                "## The surface itself",
                "",
                "| axis | what could not be derived | how to close it |",
                "| --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| `{gap.axis}` | {gap.why} | {gap.fix} |" for gap in report.surface_gaps
        )
        lines.append("")
    if report.off_surface:
        lines.extend(
            [
                "## Routes read but not generated",
                "",
                "These exist in the code and not necessarily in the deployment the run",
                "talks to, so no contract was written for them. `--route` generates one",
                "on request, for a run against a deployment where the condition holds.",
                "",
                "| route | condition |",
                "| --- | --- |",
            ]
        )
        lines.extend(
            f"| `{route}` | {condition} |" for route, condition in report.off_surface
        )
        lines.append("")
    return lines


def _error_table(report: DiscoveryReport) -> list[str]:
    """What the source said about failure, and where it contradicts the descriptor.

    It is a section of its own because it is not per endpoint: one advice class
    answers for the whole surface, and a reader that cannot see one says so by
    leaving the section empty rather than by guessing 400.
    """
    if not report.errors.statuses and not report.errors.notes:
        return []
    lines = ["## Failures as the source declares them", ""]
    if report.errors.statuses:
        lines.extend(["| axis | status |", "| --- | --- |"])
        lines.extend(
            f"| `{axis}` | {status} |"
            for axis, status in sorted(report.errors.statuses.items())
        )
        lines.append("")
    if report.errors.envelope:
        lines.append(f"- envelope: `{report.errors.envelope}`")
        lines.append("")
    if report.disagreements:
        lines.extend(
            [
                "### The descriptor contradicts the source",
                "",
                "The declaration still wins — a human who wrote a number meant it —",
                "but the two cannot both be right:",
                "",
                "| axis | declared | the source says |",
                "| --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| `{axis}` | {declared} | {read} |"
            for axis, declared, read in report.disagreements
        )
        lines.append("")
    if report.declared_only:
        answered = ", ".join(
            f"`{axis}` {status}" for axis, status in report.declared_only
        )
        lines.append(f"- the descriptor answers what the source could not: {answered}")
        lines.append("")
    for note in report.errors.notes:
        lines.append(f"- {note}")
    lines.append("")
    return lines


def _gap_table(endpoint: DiscoveredEndpoint) -> list[str]:
    if not endpoint.gaps:
        return ["Nothing left open: every axis this source could speak for was derived.", ""]
    lines = [
        "| axis | what could not be derived | how to close it |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| `{gap.axis}` | {gap.why} | {gap.fix} |" for gap in endpoint.gaps)
    lines.append("")
    return lines


def _blocked_table(report: DiscoveryReport) -> list[str]:
    blocked = report.blocked()
    if not blocked:
        return []
    lines = [
        "## Cases that cannot run yet",
        "",
        "`TODO` is written where the source had no value, and both `validate` and the",
        "runner refuse it: a case carrying a literal `TODO` would report a result for",
        "a request nobody wrote.",
        "",
        "| endpoint | where | why |",
        "| --- | --- | --- |",
    ]
    for endpoint in blocked:
        lines.extend(
            f"| `{endpoint.endpoint}` | `{where}` | {why} |"
            for where, why in endpoint.placeholders
        )
    lines.append("")
    return lines
