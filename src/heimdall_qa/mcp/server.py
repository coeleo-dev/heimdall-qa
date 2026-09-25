"""The harness as an MCP server, beside the CLI and not instead of it.

A model driving a review asks the same questions a person does — does this round
validate, run it, what did the last run leave — and it asks them with no shell in the
way. Every tool here delegates to `heimdall_qa.operations`, which is where the CLI
delegates too, so a tool and a command cannot drift into disagreeing about which
project they measured.

Two deliberate differences from the CLI, both because a server has no person's working
directory:

* `root` defaults to the process's directory rather than being required, and the run
  evidence defaults to `root/runs` instead of `cwd/runs`. When a client launches this
  from the project directory the two agree exactly.
* A **domain** failure — a round that does not load, a run that does not exist — comes
  back as an `error` object carrying the code, the message and the fix, not as an
  exception. A refusal a model can act on is worth more than a traceback it can only
  repeat. A bug still raises: those should look like bugs.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from heimdall_qa import __version__
from heimdall_qa import operations
from heimdall_qa.discovery import render_report
from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import format_cli
from heimdall_qa.errors import to_dict
from heimdall_qa.findings import ValidationFinding
from heimdall_qa.findings import explain
from heimdall_qa.operations import Settings
from heimdall_qa.projects import ProjectsRegistry
from heimdall_qa.serve.bind import assert_local_bind

INSTRUCTIONS = """\
Heimdall QA replays HTTP against an API it did not write and leaves evidence a person
can judge. It carries no product: `root` points at the project under review, whose
descriptor, contracts, cases and rounds are that project's own files.

Start with `validate_round`, then `run_round`, then read the run. Never invent a value
a fixture can generate, and never make a failing round pass by editing its expectation
— a waiver needs a registered gap."""


def _default_root() -> Path:
    """The first project the client has open, or the working directory.

    A server launched by an MCP client has no meaningful working directory: it is
    whatever the client happened to start from, which is often the home directory or
    `/`. Falling back to the registry means a model that says
    `validate_round("rounds/smoke.yaml")` with no root measures the project the person
    has open — which is the one they are asking about — instead of failing against a
    directory nobody chose.
    """
    entries = ProjectsRegistry().load()
    if entries:
        return entries[0].root
    return Path.cwd()


def _settings(root: str | None, *, runs_dir: str | None = None) -> Settings:
    """The wiring, with the server's two defaults rather than the CLI's."""
    where = Path(root) if root else _default_root()
    return operations.resolve_settings(
        where,
        runs_dir=Path(runs_dir) if runs_dir else where / "runs",
    )


def _readable(fn: Callable[..., Any]) -> Callable[..., Any]:
    """A refused operation comes back as something a model can fix.

    Only `HarnessError` is caught. That class is the harness saying "I will not do
    this, and here is why" — a `KeyError` is the harness being wrong, and swallowing
    it here would hide the bug behind a tidy-looking payload.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HarnessError as exc:
            return {"error": to_dict(exc)}

    return wrapper


def _finding(item: ValidationFinding) -> dict[str, str]:
    return {
        "code": item.code,
        "where": item.where,
        "message": item.message,
        "fix": item.fix,
        "why": item.why,
    }


def init(root: str | None = None, force: bool = False) -> dict[str, Any]:
    """Write the starter tree a new project begins from, and name what it wrote.

    Idempotent: a file that already exists is kept, not clobbered, because the second
    run is usually somebody who has already started editing. `force` overwrites, and
    still only touches the files this ships.
    """
    where = Path(root) if root else Path.cwd()
    return {"root": str(where.resolve()), "files": operations.write_starter(where, force=force)}


@_readable
def validate_round(round: str, root: str | None = None) -> dict[str, Any]:
    """Check a round before spending a run on it.

    A round is worth fixing in one pass, so every finding comes back, not the first.
    Read `explain_rules` before answering a finding by deleting the case that caused
    it: the code says which rule fired, and the rule usually has a right answer.
    """
    findings = operations.validate_round_file(_settings(root), round)
    return {"ok": not findings, "findings": [_finding(item) for item in findings]}


@_readable
def explain_rules() -> str:
    """The catalog of `validate` findings: every code with the reason it exists."""
    return explain()


@_readable
def discover(
    root: str | None = None,
    source: str | None = None,
    location: str | None = None,
    route: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Read the API's own contract and write one contract per endpoint.

    This is the first step against a project that has never been reviewed, and the
    report it writes lists what the source could not state — a rule in code describes
    no field, so the gap is the finding, not a failure.

    `source` and `location` try a reader without editing the descriptor, `route`
    narrows to one endpoint, and `dry_run` reads and reports without writing anything.
    """
    report = operations.discover_surface(
        _settings(root),
        source=source,
        location=location,
        route=route,
        dry_run=dry_run,
        force=force,
    )
    if dry_run:
        return {"dry_run": True, "report": render_report(report)}
    return {
        "report_path": str(report.report_path.resolve()),
        "endpoints": len(report.endpoints),
        "cases_written": report.written_case_count(),
        "cases_kept": report.kept_case_count(),
        "gaps": report.gap_count(),
    }


@_readable
def scaffold_endpoint(
    contract: str,
    root: str | None = None,
    out: str = "cases",
    force: bool = False,
    h01_status: int | None = None,
) -> dict[str, Any]:
    """Write the case stubs one contract derives, and name them.

    A stub that could not be derived is written with `TODO`, which is a question and
    not a passing test — `validate_round` reports it as `TODO_SENTINEL` until it is
    answered.
    """
    ids = operations.scaffold_contract(
        _settings(root), contract, out=out, force=force, h01_status=h01_status
    )
    return {"contract": contract, "cases": ids}


@_readable
def scaffold_round(
    contract: str,
    root: str | None = None,
    out: str = "rounds",
    cases_out: str | None = None,
    force: bool = False,
    h01_status: int | None = None,
    round_id: str | None = None,
) -> dict[str, Any]:
    """Write a contract's cases and the round that includes exactly them."""
    return operations.scaffold_contract_round(
        _settings(root),
        contract,
        out=out,
        cases_out=cases_out,
        force=force,
        h01_status=h01_status,
        round_id=round_id,
    )


@_readable
def run_round(
    round: str,
    root: str | None = None,
    mode: str = "headless",
    runs_dir: str | None = None,
) -> dict[str, Any]:
    """Replay a round and return where the evidence landed and how it counted.

    `headless` and `ui` differ in how a step that wants a human is answered, not in
    what is measured. Reading the run afterwards — `read_run_file` — is the point of
    running it: the counts say what happened, the steps say why.
    """
    settings = _settings(root, runs_dir=runs_dir)
    with httpx.Client(timeout=10.0) as client:
        run_dir = operations.run_round(settings, round, client=client, mode=mode)
    summary = _read_json(run_dir / "summary.json")
    return {
        "run_dir": str(run_dir.resolve()),
        "counts": summary["counts"],
        "oracle": summary.get("oracle"),
    }


@_readable
def last_run(root: str | None = None, runs_dir: str | None = None) -> dict[str, Any]:
    """Where the most recent run's evidence is, as `runs/latest` resolves."""
    settings = _settings(root, runs_dir=runs_dir)
    return {"run_dir": str(operations.latest_run(settings.runs_dir))}


@_readable
def campaign_validate(campaign: str, root: str | None = None) -> dict[str, Any]:
    """Validate every round a campaign lists, in the order it lists them."""
    findings = operations.validate_campaign_file(_settings(root), campaign)
    return {"ok": not findings, "findings": [_finding(item) for item in findings]}


@_readable
def campaign_status(campaign: str, root: str | None = None) -> dict[str, Any]:
    """Per round of a campaign: passed, failed, 5xx, or never reviewed.

    "Never reviewed" is a real answer and not a failure. A campaign is a claim about
    coverage, and a round nobody has run is a hole in it.
    """
    return operations.campaign_report(_settings(root), campaign)


@_readable
def fixture(kind: str, root: str | None = None) -> dict[str, Any]:
    """One identity block, generated from the project's declared generators.

    A case that needs a document number or an e-mail asks for it here. A memorised
    value is a test that passes for a reason nobody chose.
    """
    return operations.fixture_payload(_settings(root), kind)


@_readable
def read_run_file(
    name: str,
    run: str | None = None,
    root: str | None = None,
) -> dict[str, Any]:
    """One file out of a run's evidence: `summary.json`, `steps/000/response.json`.

    Defaults to the latest run; `run` names a directory under `runs/` instead. A name
    that would leave the run is refused rather than resolved — the evidence is a
    directory, not a filesystem.
    """
    settings = _settings(root)
    directory = (
        settings.runs_dir / run if run else operations.latest_run(settings.runs_dir)
    )
    path = _inside(Path(directory), name)
    if not path.is_file():
        raise HarnessError(
            code="RUN_FILE_MISSING",
            message=f"no such file in the run: {name}",
            hint="summary.json and steps/<index>/request.json are the usual ones",
        )
    if path.suffix == ".json":
        return {"path": str(path), "json": _read_json(path)}
    return {"path": str(path), "text": path.read_text(encoding="utf-8")}


def latest_run_file(name: str) -> dict[str, Any]:
    """One file out of the latest run's evidence, by name."""
    return read_run_file(name)


def onboarding(root: str | None = None) -> str:
    """The recipe for pointing the harness at a project nobody has reviewed yet."""
    where = root or "the project's root"
    return f"""\
Onboard {where} into Heimdall QA, in this order, and stop at the first step that \
refuses:

1. `init` writes a starter tree if the project has none. It is idempotent and never
   overwrites a filled tree, so it is safe to call and read the result.
2. Fill `qa/project.yaml`: `project.id`, one `environments` entry with a `base_url`,
   and `routes` with the auth each prefix needs. A field the descriptor does not need
   yet is a field to leave out — the harness skips with a reason rather than guessing.
3. `discover` — or `heimdall-qa discover` — reads the API's own contract. Read the
   report it writes: the gaps are what the source could not state, and they are the
   work, not a defect.
4. `validate_round` the round discovery wrote, and answer every finding. `explain_rules`
   says what each code means.
5. `run_round`, then `read_run_file` the steps. Only now is there something to review.

Do not put a credential in a round: `secrets.local.yaml` is the only place one lives.
Do not answer a finding by deleting the case that produced it."""


def review_run(run: str | None = None) -> str:
    """The recipe for reading a run and writing the finding it supports."""
    where = run or "the latest run"
    return f"""\
Review {where} and report what the evidence supports — nothing more.

1. `read_run_file("summary.json")` first. The counts and `logs_incomplete` say whether
   the run is trustworthy; a run with uncollected logs has not finished answering.
2. Read the failing steps' `response.json` and `request.json` before theorising. The
   status, the body and the headers are the evidence; the case is the claim.
3. Separate what was measured from what was expected. An expected value in a case is a
   claim somebody made, and a mismatch may mean the claim is wrong — that is a finding
   about the case, and it is worth stating as one.
4. Where a pack reported a value, quote it. A number with no step behind it is an
   opinion.
5. Write one finding per distinct cause, with the step it came from and the smallest
   change that would settle it. If nothing failed, say so plainly: a clean run is a
   result, and padding it is how a review loses its meaning.

Never make a failing round pass by editing its expectation. A waiver needs a
registered gap."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _inside(base: Path, name: str) -> Path:
    """`name` resolved under `base`, refusing anything that leaves it."""
    root = base.resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise HarnessError(
            code="RUN_FILE_OUTSIDE",
            message=f"{name} points outside the run",
            hint="name a file inside the run, like summary.json or steps/000/request.json",
        )
    return candidate


#: The tools, in the order a review happens — which is the order to tell a model about
#: them. Read off this module rather than restated, so adding one is a one-line change.
TOOLS: tuple[Callable[..., Any], ...] = (
    init,
    validate_round,
    explain_rules,
    discover,
    scaffold_endpoint,
    scaffold_round,
    run_round,
    last_run,
    campaign_validate,
    campaign_status,
    fixture,
    read_run_file,
)


def build_server() -> Any:
    """The MCP server, or the error that says how to install the SDK it needs."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise HarnessError(
            code="MCP_EXTRA_MISSING",
            message=_missing_sdk(),
            hint="pip install --upgrade 'heimdall-qa[mcp]'",
            details=(str(exc),),
        ) from exc

    server = MCPServer(name="heimdall-qa", version=__version__, instructions=INSTRUCTIONS)
    for tool in TOOLS:
        server.add_tool(tool)
    # Applied programmatically rather than with `@server.resource`, so the functions
    # above stay plain and a test can call one without the SDK installed.
    server.resource(
        "heimdall://run/latest/{name}",
        description="One file out of the latest run's evidence, by name.",
    )(latest_run_file)
    server.prompt(
        name="onboarding",
        description="How to point the harness at a project nobody has reviewed yet.",
    )(onboarding)
    server.prompt(
        name="review_run",
        description="How to read a run and write the finding it supports.",
    )(review_run)
    return server


def _missing_sdk() -> str:
    """Which of the two failures this is: no SDK at all, or one too old for it.

    Worth the four lines: "the dependency is not installed" is a lie to somebody who
    has it, and the version that renamed the class is a one-line fix once named.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version

    try:
        installed = version("mcp")
    except PackageNotFoundError:
        return "the MCP server needs an optional dependency that is not installed"
    return (
        f"the MCP server needs MCP 2.x and found {installed}; 1.x named this API"
        " `FastMCP`, so upgrade rather than reinstall"
    )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="heimdall-qa-mcp",
        description=(
            "Heimdall QA over MCP. The CLI is unchanged and is the primary "
            "interface; this speaks the same operations to a model."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio (default, what an MCP client launches) or streamable-http",
    )
    # Loopback only, and asserted below: the harness reaches a project's API, so its
    # own surface is never something to expose.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    try:
        server = build_server()
        if args.transport == "streamable-http":
            assert_local_bind(args.host)
    except HarnessError as exc:
        print(format_cli(exc), file=sys.stderr)
        return 2
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - the console script is the entry
    raise SystemExit(main())
