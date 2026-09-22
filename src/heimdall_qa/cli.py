import argparse
import json
from pathlib import Path
import sys
import traceback

import httpx
from pydantic import ValidationError
import yaml
from yaml import YAMLError

from heimdall_qa.campaign import campaign_status
from heimdall_qa.campaign import validate_campaign
from heimdall_qa.collection import is_campaign_yaml
from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import load_config
from heimdall_qa.descriptor import resolve_descriptor
from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import format_cli
from heimdall_qa.fixtures import build_payload
from heimdall_qa.runner import execute_round
from heimdall_qa.scaffold import scaffold_endpoint
from heimdall_qa.scaffold import scaffold_round
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.load import load_round
from heimdall_qa.serve.app import create_app
from heimdall_qa.serve.bind import assert_local_bind
from heimdall_qa.validate import validate_round
from heimdall_qa.workspace import WorkspaceSession


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heimdall-qa",
        description="Heimdall QA review harness",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print exception cause and traceback",
    )
    subparsers = parser.add_subparsers(dest="command")
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate a round against contract coverage",
    )
    validate_parser.add_argument("round")
    validate_parser.add_argument("--root", default=None)
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
    scaffold_parser.add_argument("--out", default="cases")
    scaffold_parser.add_argument("--force", action="store_true")
    scaffold_parser.add_argument("--h01-status", type=int, default=None)
    scaffold_round_parser = subparsers.add_parser(
        "scaffold-round",
        help="Generate cases plus a round YAML with include = expand()",
    )
    scaffold_round_parser.add_argument("contract")
    scaffold_round_parser.add_argument("--out", default="rounds")
    scaffold_round_parser.add_argument("--cases-out", default=None)
    scaffold_round_parser.add_argument("--force", action="store_true")
    scaffold_round_parser.add_argument("--h01-status", type=int, default=None)
    scaffold_round_parser.add_argument("--round-id", default=None)
    scaffold_round_parser.add_argument("--root", default=None)
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
    campaign_status_parser = campaign_sub.add_parser(
        "status",
        help="Report run status for every round in a campaign",
    )
    campaign_status_parser.add_argument("campaign")
    campaign_status_parser.add_argument("--root", default=None)
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


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "validate":
        return _run_validate(args)
    if args.command == "scaffold-endpoint":
        return _run_scaffold(args)
    if args.command == "scaffold-round":
        return _run_scaffold_round(args)
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


def _run_validate(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else Path.cwd()
    # A descriptor that fails here never reaches a run. An absent one is not an
    # error: it is optional until a case needs something only it knows.
    resolve_descriptor(root, explicit=getattr(args, "descriptor", None))
    errors = validate_round(Path(args.round), root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def _run_scaffold(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract)
    ids = scaffold_endpoint(
        contract_path,
        Path(args.out),
        force=args.force,
        h01_status=args.h01_status,
    )
    for case_id in ids:
        print(case_id)
    return 0


def _run_scaffold_round(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else Path.cwd()
    cases_out = Path(args.cases_out) if args.cases_out else None
    result = scaffold_round(
        Path(args.contract),
        Path(args.out),
        cases_dir=cases_out,
        force=args.force,
        h01_status=args.h01_status,
        round_id=args.round_id,
        root=root,
    )
    print(result["round"])
    for case_id in result["case_ids"]:
        print(case_id)
    return 0


def _run_campaign(args: argparse.Namespace) -> int:
    command = getattr(args, "campaign_command", None)
    if command == "validate":
        return _run_campaign_validate(args)
    if command == "status":
        return _run_campaign_status(args)
    print("campaign requires validate or status", file=sys.stderr)
    return 2


def _run_campaign_validate(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else Path.cwd()
    errors = validate_campaign(Path(args.campaign), root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def _run_campaign_status(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else Path.cwd()
    payload = campaign_status(Path(args.campaign), root, Path(args.runs_dir))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _run_run(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else Path.cwd()
    config = _resolve_config(args.config)
    secrets = _resolve_secrets(args.secrets, root)
    resolved = resolve_descriptor(root, explicit=getattr(args, "descriptor", None))
    runs_dir = Path.cwd() / "runs"
    with httpx.Client(timeout=10.0) as client:
        run_dir = execute_round(
            Path(args.round),
            root=root,
            config=config,
            client=client,
            runs_dir=runs_dir,
            secrets=secrets,
            mode=args.mode,
            descriptor=resolved.as_summary() if resolved is not None else None,
        )
    print(str(run_dir.resolve()))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    counts = summary["counts"]
    if counts["fail"] or counts["http_5xx"]:
        return 1
    return 0


def _run_fixture(args: argparse.Namespace) -> int:
    print(json.dumps(build_payload(args.kind), ensure_ascii=False, indent=2))
    return 0


def _run_last_run(args: argparse.Namespace) -> int:
    latest = Path(args.runs_dir) / "latest"
    if not latest.is_symlink() or not latest.exists():
        raise HarnessError(
            code="LAST_RUN_MISSING",
            message="no runs/latest symlink",
            hint="run a round first: heimdall-qa run ROUND --mode headless",
        )
    print(str(latest.resolve()))
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    import uvicorn

    root = Path(args.root) if args.root else Path.cwd()
    config = _resolve_config(args.config)
    assert_local_bind(config.ui.host)
    secrets = _resolve_secrets(args.secrets, root)
    client = httpx.Client(timeout=10.0)
    workspace = _build_workspace(
        getattr(args, "target", None),
        root=root,
        config=config,
        client=client,
        secrets=secrets,
    )
    app = create_app(workspace=workspace)
    print(f"http://{config.ui.host}:{config.ui.port}")
    try:
        uvicorn.run(
            app,
            host=config.ui.host,
            port=config.ui.port,
            access_log=False,
        )
    finally:
        client.close()
    return 0


def _build_workspace(
    target: str | None,
    *,
    root: Path,
    config: HarnessConfig,
    client: httpx.Client,
    secrets: dict[str, str],
) -> WorkspaceSession:
    runs_dir = Path.cwd() / "runs"
    if not target:
        return WorkspaceSession(
            root=root,
            config=config,
            client=client,
            runs_dir=runs_dir,
            secrets=secrets,
        )
    path = Path(target)
    if is_campaign_yaml(path):
        workspace = WorkspaceSession(
            root=root,
            config=config,
            client=client,
            runs_dir=runs_dir,
            secrets=secrets,
        )
        try:
            campaign = load_campaign(path)
        except (OSError, ValueError, ValidationError, YAMLError) as exc:
            raise HarnessError(
                code="ROUND_INVALID",
                message="campaign YAML is invalid",
                hint="fix the campaign file and run heimdall-qa campaign validate",
                details=(str(exc),),
            ) from exc
        workspace.select(f"campaign:{campaign.id}")
        return workspace
    try:
        load_round(path)
    except (OSError, ValueError, ValidationError, YAMLError) as exc:
        raise HarnessError(
            code="ROUND_INVALID",
            message="round YAML is invalid",
            hint="fix the round file and run heimdall-qa validate",
            details=(str(exc),),
        ) from exc
    return WorkspaceSession(
        root=root,
        config=config,
        client=client,
        runs_dir=runs_dir,
        secrets=secrets,
        focus=path,
    )


def _resolve_config(raw: str | None) -> HarnessConfig:
    if raw:
        return _read_config(Path(raw))
    default = Path.cwd() / "config.yaml"
    if default.is_file():
        return _read_config(default)
    return HarnessConfig()


def _read_config(path: Path) -> HarnessConfig:
    if not path.is_file():
        raise HarnessError(
            code="CONFIG_INVALID",
            message=f"config file not found: {path}",
            hint="pass --config or create config.yaml in the working directory",
        )
    try:
        return load_config(path)
    except (OSError, ValueError, ValidationError, YAMLError) as exc:
        raise HarnessError(
            code="CONFIG_INVALID",
            message=f"config file is invalid: {path}",
            hint="fix config.yaml and retry",
            details=(str(exc),),
        ) from exc


def _resolve_secrets(raw: str | None, root: Path) -> dict[str, str]:
    if raw:
        return _read_secrets(Path(raw))
    default = root / "secrets.local.yaml"
    if default.is_file():
        return _read_secrets(default)
    return {}


def _read_secrets(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise HarnessError(
            code="CONFIG_INVALID",
            message=f"secrets file not found: {path}",
            hint="create secrets.local.yaml with api_key, jwt, or admin_secret",
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, YAMLError) as exc:
        raise HarnessError(
            code="CONFIG_INVALID",
            message=f"secrets file is invalid: {path}",
            hint="fix secrets.local.yaml and retry",
            details=(str(exc),),
        ) from exc
    if not isinstance(data, dict):
        raise HarnessError(
            code="CONFIG_INVALID",
            message=f"secrets file must be a mapping: {path}",
            hint="use keys api_key, jwt, or admin_secret",
        )
    return {str(key): str(value) for key, value in data.items()}


def _print_verbose(err: BaseException) -> None:
    cause = err.__cause__ or err.__context__
    if cause is not None:
        traceback.print_exception(cause, file=sys.stderr)
    else:
        traceback.print_exception(err, file=sys.stderr)
