"""What a front end does, written once for every front end.

The CLI and the MCP server say the same things — validate this round, run it, read
what the last run left — in two registers. The arguments differ; the wiring does not:
which descriptor wins, where the project's content root is, which config and which
secrets are in force. Written twice that wiring drifts, and two front ends start
disagreeing about which project they just measured.

So the wiring lives here, and these functions **return data and never print**.
Presentation belongs to the front end: the CLI writes lines a script can grep, an MCP
tool returns an object a model reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import ValidationError
from yaml import YAMLError

from heimdall_qa.campaign import campaign_status as _campaign_status
from heimdall_qa.campaign import validate_campaign as _validate_campaign
from heimdall_qa.collection import is_campaign_yaml
from heimdall_qa.config import HarnessConfig
from heimdall_qa.config import load_config
from heimdall_qa.descriptor import ResolvedDescriptor
from heimdall_qa.descriptor import resolve_descriptor
from heimdall_qa.discovery import DiscoveryReport
from heimdall_qa.discovery import discover as _discover
from heimdall_qa.errors import HarnessError
from heimdall_qa.findings import ValidationFinding
from heimdall_qa.fixtures import build_payload
from heimdall_qa.onboarding import write_starter_tree
from heimdall_qa.project import ProjectView
from heimdall_qa.runner import execute_round
from heimdall_qa.scaffold import scaffold_endpoint as _scaffold_endpoint
from heimdall_qa.scaffold import scaffold_round as _scaffold_round
from heimdall_qa.schema.load import content_root
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.load import resolve_path
from heimdall_qa.schema.models import CampaignFile
from heimdall_qa.validate import validate_round as _validate_round
from heimdall_qa.workspace import WorkspaceSession


@dataclass(frozen=True)
class Settings:
    """Everything a command needs that is not an argument of that command.

    `runs_dir` is the working directory's `runs/` and not the root's, which is what
    the CLI has always done: a round named with `--root` still writes its evidence
    beside the person running it. Front ends that want it elsewhere — the MCP server,
    whose working directory belongs to nobody — say so when they resolve.
    """

    root: Path
    runs_dir: Path
    config: HarnessConfig
    project: ProjectView
    secrets: dict[str, str]
    descriptor: ResolvedDescriptor | None


def resolve_settings(
    root: Path,
    *,
    config_path: str | None = None,
    secrets_path: str | None = None,
    descriptor_path: str | None = None,
    runs_dir: Path | None = None,
) -> Settings:
    """Resolve the four things that decide what a command is looking at."""
    resolved = resolve_descriptor(root, explicit=descriptor_path)
    project = project_of(resolved)
    return Settings(
        root=root,
        runs_dir=runs_dir if runs_dir is not None else Path.cwd() / "runs",
        config=resolve_config(config_path).with_project(project),
        project=project,
        secrets=resolve_secrets(secrets_path, root),
        descriptor=resolved,
    )


def write_starter(root: Path, *, force: bool = False) -> list[str]:
    """Write the starter tree under `root`, keeping whatever is already there.

    Takes a path rather than `Settings` on purpose: `init` exists to create the files
    the other operations read, so resolving a descriptor or a config first would be
    asking a question this command is the answer to.
    """
    return write_starter_tree(root, force=force)


def project_of(resolved: ResolvedDescriptor | None) -> ProjectView:
    """The view of a resolved descriptor, or the empty one when none exists.

    An absent descriptor is not an error: it is optional until a case needs
    something only it knows, and the empty view is the honest answer until then.
    """
    if resolved is None:
        return ProjectView()
    return ProjectView(
        descriptor=resolved.descriptor,
        descriptor_dir=resolved.path.parent,
    )


def resolve_config(raw: str | None) -> HarnessConfig:
    """`--config`, else `config.yaml` in the working directory, else the defaults."""
    if raw:
        return read_config(Path(raw))
    default = Path.cwd() / "config.yaml"
    if default.is_file():
        return read_config(default)
    return HarnessConfig()


def read_config(path: Path) -> HarnessConfig:
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


def resolve_secrets(raw: str | None, root: Path) -> dict[str, str]:
    """`--secrets`, else `secrets.local.yaml` under the root, else nothing.

    An empty map is a real answer, not a missing file: the fixtures run with it, and
    a project that needs no credential declares no secret to look for.
    """
    if raw:
        return read_secrets(Path(raw))
    default = root / "secrets.local.yaml"
    if default.is_file():
        return read_secrets(default)
    return {}


def read_secrets(path: Path) -> dict[str, str]:
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


def validate_round_file(settings: Settings, round_name: str) -> list[ValidationFinding]:
    """Validate one round against the project in force."""
    path = resolve_path(settings.root, round_name, settings.project)
    return _validate_round(path, settings.root, settings.project)


def contract_path(settings: Settings, contract_name: str) -> Path:
    """The contract a scaffold is asked to read, or the error that says it is missing.

    `scaffold` reads the file before it can say anything useful about it, so a typo
    would otherwise reach the caller as a `FileNotFoundError` — the one kind of
    failure that reads like a bug instead of like an instruction.
    """
    path = resolve_path(settings.root, contract_name, settings.project)
    if not path.is_file():
        raise HarnessError(
            code="CONTRACT_UNREADABLE",
            message=f"contract not found: {path}",
            hint=(
                "run heimdall-qa discover, or name a contract under the project's"
                " content root"
            ),
        )
    return path


def scaffold_contract(
    settings: Settings,
    contract_name: str,
    *,
    out: str = "cases",
    force: bool = False,
    h01_status: int | None = None,
) -> list[str]:
    """Write the case stubs a contract derives, into the project's content root."""
    content = content_root(settings.root, settings.project)
    return _scaffold_endpoint(
        contract_path(settings, contract_name),
        under(content, out),
        project=settings.project,
        content_base=content,
        force=force,
        h01_status=h01_status,
    )


def scaffold_contract_round(
    settings: Settings,
    contract_name: str,
    *,
    out: str = "rounds",
    cases_out: str | None = None,
    force: bool = False,
    h01_status: int | None = None,
    round_id: str | None = None,
) -> dict[str, object]:
    """Write a contract's cases and the round that includes exactly them."""
    content = content_root(settings.root, settings.project)
    return _scaffold_round(
        contract_path(settings, contract_name),
        under(content, out),
        cases_dir=under(content, cases_out) if cases_out else None,
        project=settings.project,
        force=force,
        h01_status=h01_status,
        round_id=round_id,
        root=content,
    )


def discover_surface(
    settings: Settings,
    *,
    source: str | None = None,
    location: str | None = None,
    route: str | None = None,
    contracts_out: str = "contracts",
    cases_out: str = "cases",
    report: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> DiscoveryReport:
    """Read the API's own contract and write contracts, cases and the gaps report."""
    content = content_root(settings.root, settings.project)
    report_path = under(content, report) if report else content / "DISCOVERY.md"
    return _discover(
        settings.project,
        contracts_dir=under(content, contracts_out),
        cases_dir=under(content, cases_out),
        report_path=report_path,
        source=source,
        location=location,
        route=route,
        dry_run=dry_run,
        force=force,
    )


def run_round(
    settings: Settings,
    round_name: str,
    *,
    client: httpx.Client,
    mode: str = "headless",
) -> Path:
    """Replay a round and return the directory the evidence landed in."""
    return execute_round(
        resolve_path(settings.root, round_name, settings.project),
        root=settings.root,
        config=settings.config,
        client=client,
        runs_dir=settings.runs_dir,
        secrets=settings.secrets,
        mode=mode,
        descriptor=settings.descriptor.as_summary() if settings.descriptor else None,
    )


def latest_run(runs_dir: Path) -> Path:
    """`runs/latest`, resolved, or the named error that says to run something."""
    latest = runs_dir / "latest"
    if not latest.is_symlink() or not latest.exists():
        raise HarnessError(
            code="LAST_RUN_MISSING",
            message=f"no {runs_dir}/latest symlink",
            hint="run a round first: heimdall-qa run ROUND --mode headless",
        )
    return latest.resolve()


def validate_campaign_file(
    settings: Settings,
    campaign_name: str,
) -> list[ValidationFinding]:
    """Validate every round a campaign lists."""
    path = resolve_path(settings.root, campaign_name, settings.project)
    return _validate_campaign(path, settings.root, settings.project)


def campaign_report(settings: Settings, campaign_name: str) -> dict[str, Any]:
    """Per round of a campaign: pass, fail, 5xx, or not reviewed."""
    path = resolve_path(settings.root, campaign_name, settings.project)
    return _campaign_status(path, settings.root, settings.runs_dir, settings.project)


def fixture_payload(settings: Settings, kind: str) -> dict[str, Any]:
    """One identity block, generated from the project's declared generators."""
    return build_payload(kind, project=settings.project)


def build_workspace(
    settings: Settings,
    *,
    client: httpx.Client,
    target: str | None = None,
) -> WorkspaceSession:
    """The review session a `serve` process walks, focused on `target` if given."""
    focus: Path | None = None
    campaign: str | None = None
    if target:
        path = resolve_path(settings.root, target, settings.config.project)
        if is_campaign_yaml(path):
            campaign = _load_campaign_or_invalid(path).id
        else:
            _load_round_or_invalid(path)
            focus = path
    workspace = WorkspaceSession(
        root=settings.root,
        config=settings.config,
        client=client,
        runs_dir=settings.runs_dir,
        secrets=settings.secrets,
        focus=focus,
    )
    if campaign is not None:
        workspace.select(f"campaign:{campaign}")
    return workspace


def under(base: Path, raw: str) -> Path:
    """A front-end path that is meant to sit inside the project's content."""
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _load_round_or_invalid(path: Path) -> None:
    """A round that cannot be parsed is a named error, not a traceback."""
    try:
        load_round(path)
    except (OSError, ValueError, ValidationError, YAMLError) as exc:
        raise HarnessError(
            code="ROUND_INVALID",
            message="round YAML is invalid",
            hint="fix the round file and run heimdall-qa validate",
            details=(str(exc),),
        ) from exc


def _load_campaign_or_invalid(path: Path) -> CampaignFile:
    try:
        return load_campaign(path)
    except (OSError, ValueError, ValidationError, YAMLError) as exc:
        raise HarnessError(
            code="ROUND_INVALID",
            message="campaign YAML is invalid",
            hint="fix the campaign file and run heimdall-qa campaign validate",
            details=(str(exc),),
        ) from exc
