import argparse
import json
import sys
import traceback
from pathlib import Path

import httpx

from heimdall_qa import __version__
from heimdall_qa.discovery import DiscoveryReport
from heimdall_qa.discovery import render_report
from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import format_cli
from heimdall_qa.findings import explain
from heimdall_qa.operations import Settings
from heimdall_qa.operations import build_workspace
from heimdall_qa.operations import campaign_report
from heimdall_qa.operations import discover_surface
from heimdall_qa.operations import fixture_payload
from heimdall_qa.operations import latest_run
from heimdall_qa.operations import resolve_settings
from heimdall_qa.operations import run_round
from heimdall_qa.operations import scaffold_contract
from heimdall_qa.operations import scaffold_contract_round
from heimdall_qa.operations import validate_campaign_file
from heimdall_qa.operations import validate_round_file
from heimdall_qa.operations import write_starter
from heimdall_qa.serve.app import create_app
from heimdall_qa.serve.bind import assert_local_bind


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heimdall-qa",
        description="Heimdall QA review harness",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print the harness version and exit",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print exception cause and traceback",
    )
    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser(
        "init",
        help="Write the starter tree a project begins from",
    )
    init_parser.add_argument(
        "--root",
        default=None,
        help="Directory to write into (default: the working directory)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the starter files; a tree you have edited is kept without it",
    )
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate a round against contract coverage",
    )
    validate_parser.add_argument(
        "round",
        nargs="?",
        default=None,
        help="Round YAML to validate; omit it with --explain",
    )
    validate_parser.add_argument(
        "--explain",
        action="store_true",
        help="List every rule code with the one-line reason it exists, and exit",
    )
    validate_parser.add_argument("--root", default=None)
    validate_parser.add_argument("--config", default=None)
    validate_parser.add_argument(
        "--descriptor",
        default=None,
        help="Project descriptor to validate (default: ROOT/qa/project.yaml)",
    )
    scaffold_parser = subparsers.add_parser(
        "scaffold-endpoint",
        help="Generate case stubs from a contract (mechanical expect filled)",
    )
    scaffold_parser.add_argument("contract")
    scaffold_parser.add_argument(
        "--out",
        default="cases",
        help="Directory the area's case file goes in (default: cases)",
    )
    scaffold_parser.add_argument("--root", default=None)
    scaffold_parser.add_argument("--config", default=None)
    scaffold_parser.add_argument("--descriptor", default=None)
    scaffold_parser.add_argument("--force", action="store_true")
    scaffold_parser.add_argument("--h01-status", type=int, default=None)
    scaffold_round_parser = subparsers.add_parser(
        "scaffold-round",
        help="Generate cases plus a round YAML with include = expand()",
    )
    scaffold_round_parser.add_argument("contract")
    scaffold_round_parser.add_argument("--out", default="rounds")
    scaffold_round_parser.add_argument(
        "--cases-out",
        default=None,
        help="Directory the area's case file goes in (default: <content>/cases)",
    )
    scaffold_round_parser.add_argument("--force", action="store_true")
    scaffold_round_parser.add_argument("--h01-status", type=int, default=None)
    scaffold_round_parser.add_argument("--round-id", default=None)
    scaffold_round_parser.add_argument("--root", default=None)
    scaffold_round_parser.add_argument("--config", default=None)
    scaffold_round_parser.add_argument("--descriptor", default=None)
    discover_parser = subparsers.add_parser(
        "discover",
        help="Read the API's own contract and generate contracts, cases and a gaps report",
    )
    discover_parser.add_argument(
        "--source",
        default=None,
        help="Reader to use for this run instead of the declared contract.source",
    )
    discover_parser.add_argument(
        "--location",
        default=None,
        help="Document to read for this run instead of contract.location",
    )
    discover_parser.add_argument("--contracts-out", default="contracts")
    discover_parser.add_argument("--cases-out", default="cases")
    discover_parser.add_argument(
        "--report",
        default=None,
        help="Where to write the gaps report (default: <content>/DISCOVERY.md)",
    )
    discover_parser.add_argument(
        "--route",
        nargs="+",
        default=None,
        help="Read only this route, e.g. --route POST /api/ingest",
    )
    discover_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the report and write nothing, which answers 'what would you generate'",
    )
    discover_parser.add_argument("--force", action="store_true")
    discover_parser.add_argument("--root", default=None)
    discover_parser.add_argument("--descriptor", default=None)
    campaign_parser = subparsers.add_parser(
        "campaign",
        help="Validate or report status of a multi-round campaign",
    )
    campaign_sub = campaign_parser.add_subparsers(
        dest="campaign_command",
        required=True,
    )
    campaign_validate = campaign_sub.add_parser(
        "validate",
        help="Validate every round listed in a campaign",
    )
    campaign_validate.add_argument("campaign")
    campaign_validate.add_argument("--root", default=None)
    campaign_validate.add_argument("--descriptor", default=None)
    campaign_status_parser = campaign_sub.add_parser(
        "status",
        help="Report run status for every round in a campaign",
    )
    campaign_status_parser.add_argument("campaign")
    campaign_status_parser.add_argument("--root", default=None)
    campaign_status_parser.add_argument("--descriptor", default=None)
    campaign_status_parser.add_argument("--runs-dir", default="runs")
    run_parser = subparsers.add_parser(
        "run",
        help="Execute a round in headless mode",
    )
    run_parser.add_argument("round")
    run_parser.add_argument("--root", default=None)
    run_parser.add_argument("--mode", default="headless")
    run_parser.add_argument("--config", default=None)
    run_parser.add_argument("--secrets", default=None)
    run_parser.add_argument(
        "--descriptor",
        default=None,
        help="Project descriptor in force (default: ROOT/qa/project.yaml)",
    )
    last_run_parser = subparsers.add_parser(
        "last-run",
        help="Print the absolute path of runs/latest",
    )
    last_run_parser.add_argument("--runs-dir", default="runs")
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the review UI on 127.0.0.1",
    )
    serve_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Optional campaign or round YAML; omit to open the collection workspace",
    )
    serve_parser.add_argument("--root", default=None)
    serve_parser.add_argument("--config", default=None)
    serve_parser.add_argument("--secrets", default=None)
    fixture_parser = subparsers.add_parser(
        "fixture",
        help="Print faker/validate-docbr identity JSON for case authors",
    )
    fixture_parser.add_argument(
        "kind",
        help="email, password, person_name, company_name, address, cpf, cnpj, or register",
    )
    fixture_parser.add_argument("--root", default=None)
    fixture_parser.add_argument("--config", default=None)
    fixture_parser.add_argument("--descriptor", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    verbose = bool(getattr(args, "verbose", False))
    try:
        return _dispatch(args)
    except HarnessError as err:
        print(format_cli(err), file=sys.stderr)
        if verbose:
            _print_verbose(err)
        return err.exit_code
    except Exception as exc:
        wrapped = HarnessError(
            code="STEP_INTERNAL",
            message=str(exc) or exc.__class__.__name__,
            hint="re-run with --verbose for a traceback",
            exit_code=2,
        )
        wrapped.__cause__ = exc
        print(format_cli(wrapped), file=sys.stderr)
        if verbose:
            traceback.print_exception(exc, file=sys.stderr)
        return 2


def _settings(args: argparse.Namespace, *, runs_dir: Path | None = None) -> Settings:
    """The wiring every command shares, built once from the parsed arguments."""
    root = Path(args.root) if getattr(args, "root", None) else Path.cwd()
    return resolve_settings(
        root,
        config_path=getattr(args, "config", None),
        secrets_path=getattr(args, "secrets", None),
        descriptor_path=getattr(args, "descriptor", None),
        runs_dir=runs_dir,
    )


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init":
        return _run_init(args)
    if args.command == "validate":
        return _run_validate(args)
    if args.command == "scaffold-endpoint":
        return _run_scaffold(args)
    if args.command == "scaffold-round":
        return _run_scaffold_round(args)
    if args.command == "discover":
        return _run_discover(args)
    if args.command == "campaign":
        return _run_campaign(args)
    if args.command == "run":
        return _run_run(args)
    if args.command == "last-run":
        return _run_last_run(args)
    if args.command == "serve":
        return _run_serve(args)
    if args.command == "fixture":
        return _run_fixture(args)
    return 0


def _run_init(args: argparse.Namespace) -> int:
    """The starter tree, and a line per file so `init` reports what it did."""
    root = Path(args.root) if getattr(args, "root", None) else Path.cwd()
    for line in write_starter(root, force=args.force):
        print(line)
    print(f"next: heimdall-qa validate rounds/smoke.yaml   # in {root.resolve()}")
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    if getattr(args, "explain", False):
        print(explain())
        return 0
    if not args.round:
        raise HarnessError(
            code="VALIDATE_NO_ROUND",
            message="validate needs a round, or --explain to list the rules",
            hint="heimdall-qa validate rounds/<id>.yaml | heimdall-qa validate --explain",
        )
    # A descriptor that fails here never reaches a run. An absent one is not an
    # error: it is optional until a case needs something only it knows.
    findings = validate_round_file(_settings(args), args.round)
    for item in findings:
        print(item.render(), file=sys.stderr)
    return 1 if findings else 0


def _run_scaffold(args: argparse.Namespace) -> int:
    for case_id in scaffold_contract(
        _settings(args),
        args.contract,
        out=args.out,
        force=args.force,
        h01_status=args.h01_status,
    ):
        print(case_id)
    return 0


def _run_scaffold_round(args: argparse.Namespace) -> int:
    result = scaffold_contract_round(
        _settings(args),
        args.contract,
        out=args.out,
        cases_out=args.cases_out,
        force=args.force,
        h01_status=args.h01_status,
        round_id=args.round_id,
    )
    print(result["round"])
    for case_id in result["case_ids"]:
        print(case_id)
    return 0


def _run_discover(args: argparse.Namespace) -> int:
    report = discover_surface(
        _settings(args),
        source=args.source,
        location=args.location,
        route=" ".join(args.route) if args.route else None,
        contracts_out=args.contracts_out,
        cases_out=args.cases_out,
        report=args.report,
        dry_run=args.dry_run,
        force=args.force,
    )
    if args.dry_run:
        # The report is the whole output of a read-only run, so it goes to stdout
        # where a diff can take it, and no file is left behind to review by mistake.
        print(render_report(report), end="")
        return 0
    _report_discovery(report)
    return 0


def _report_discovery(report: DiscoveryReport) -> None:
    """The path first, because it is what the next command reads."""
    print(str(report.report_path.resolve()))
    print(
        f"{len(report.endpoints)} endpoints,"
        f" {report.written_case_count()} cases written,"
        f" {report.kept_case_count()} kept,"
        f" {report.gap_count()} gaps"
    )


def _run_campaign(args: argparse.Namespace) -> int:
    command = getattr(args, "campaign_command", None)
    if command == "validate":
        return _run_campaign_validate(args)
    if command == "status":
        return _run_campaign_status(args)
    print("campaign requires validate or status", file=sys.stderr)
    return 2


def _run_campaign_validate(args: argparse.Namespace) -> int:
    findings = validate_campaign_file(_settings(args), args.campaign)
    for item in findings:
        print(item.render(), file=sys.stderr)
    return 1 if findings else 0


def _run_campaign_status(args: argparse.Namespace) -> int:
    settings = _settings(args, runs_dir=Path(args.runs_dir))
    print(json.dumps(campaign_report(settings, args.campaign), indent=2, ensure_ascii=False))
    return 0


def _run_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with httpx.Client(timeout=10.0) as client:
        run_dir = run_round(settings, args.round, client=client, mode=args.mode)
    print(str(run_dir.resolve()))
    counts = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["counts"]
    if counts["fail"] or counts["http_5xx"]:
        return 1
    return 0


def _run_fixture(args: argparse.Namespace) -> int:
    print(json.dumps(fixture_payload(_settings(args), args.kind), ensure_ascii=False, indent=2))
    return 0


def _run_last_run(args: argparse.Namespace) -> int:
    print(str(latest_run(Path(args.runs_dir))))
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = _settings(args)
    assert_local_bind(settings.config.ui.host)
    client = httpx.Client(timeout=10.0)
    workspace = build_workspace(settings, client=client, target=getattr(args, "target", None))
    app = create_app(workspace=workspace)
    print(f"http://{settings.config.ui.host}:{settings.config.ui.port}")
    try:
        uvicorn.run(
            app,
            host=settings.config.ui.host,
            port=settings.config.ui.port,
            access_log=False,
        )
    finally:
        client.close()
    return 0


def _print_verbose(err: BaseException) -> None:
    cause = err.__cause__ or err.__context__
    if cause is not None:
        traceback.print_exception(cause, file=sys.stderr)
    else:
        traceback.print_exception(err, file=sys.stderr)
