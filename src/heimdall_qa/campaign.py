from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from heimdall_qa.descriptor import resolve_project
from heimdall_qa.findings import ValidationFinding
from heimdall_qa.findings import finding
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.models import CampaignFile
from heimdall_qa.schema.models import CampaignRound
from heimdall_qa.session_validate import validate_campaign_chain
from heimdall_qa.validate import resolve_path
from heimdall_qa.validate import validate_round

_RUN_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}-(.+?)(?:~\d+)?$")


def validate_campaign(
    campaign_path: Path,
    root: Path,
    project: ProjectView | None = None,
) -> list[ValidationFinding]:
    """Validates every round of a campaign against the project in force.

    `project` is optional only because the campaign is the one entry point whose
    caller does not already hold a view; it is resolved from `root` when omitted,
    by the same precedence rule a run uses.
    """
    view = project if project is not None else resolve_project(root)
    try:
        campaign = load_campaign(campaign_path)
    except (ValidationError, ValueError, OSError) as exc:
        return [
            finding(
                "ROUND_UNREADABLE",
                str(campaign_path),
                str(exc),
                fix="open the campaign and correct the YAML it reports.",
            )
        ]
    findings: list[ValidationFinding] = []
    for entry in campaign.rounds:
        round_path = resolve_path(root, entry.round, view)
        if not round_path.is_file():
            findings.append(
                finding(
                    "ROUND_UNREADABLE",
                    entry.round,
                    "the round the campaign lists is missing",
                    fix="write the round, or drop the entry from the campaign.",
                )
            )
            continue
        for item in validate_round(round_path, root, view):
            findings.append(
                ValidationFinding(
                    code=item.code,
                    where=f"{entry.round}: {item.where}",
                    message=item.message,
                    fix=item.fix,
                    why=item.why,
                )
            )
    findings.extend(validate_campaign_chain(campaign, root, view))
    return findings


def campaign_status(
    campaign_path: Path,
    root: Path,
    runs_dir: Path,
    project: ProjectView | None = None,
) -> dict[str, Any]:
    view = project if project is not None else resolve_project(root)
    campaign = load_campaign(campaign_path)
    return {
        "id": campaign.id,
        "environment": campaign.environment,
        "rounds": [
            _round_status(entry, root, runs_dir, campaign, view)
            for entry in campaign.rounds
        ],
    }


def find_latest_run(runs_dir: Path, round_id: str) -> Path | None:
    matches: list[Path] = []
    if not runs_dir.is_dir():
        return None
    for path in runs_dir.iterdir():
        if not path.is_dir():
            continue
        matched = _RUN_DIR.match(path.name)
        if matched and matched.group(1) == round_id:
            matches.append(path)
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.name)[-1]


def _round_status(
    entry: CampaignRound,
    root: Path,
    runs_dir: Path,
    campaign: CampaignFile,
    project: ProjectView,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "round": entry.round,
        "endpoint": entry.endpoint,
        "matrix": entry.matrix,
        "auth": entry.auth,
        "campaign_id": campaign.id,
    }
    #: `dto` is provenance, and a target that has none (no OpenAPI, no Java
    #: record) keeps the key out instead of reporting a null that reads like a
    #: missing value.
    if entry.dto is not None:
        payload["dto"] = entry.dto
    round_path = resolve_path(root, entry.round, project)
    if not round_path.is_file():
        payload["status"] = "not_reviewed"
        payload["reason"] = "round file is missing"
        return payload
    try:
        round_id = load_round(round_path).id
    except (ValidationError, ValueError, OSError) as exc:
        payload["status"] = "not_reviewed"
        payload["reason"] = str(exc)
        return payload
    payload["round_id"] = round_id
    run_dir = find_latest_run(runs_dir, round_id)
    if run_dir is None:
        payload["status"] = "not_reviewed"
        return payload
    summary_path = run_dir / "summary.json"
    payload["run"] = str(run_dir)
    if not summary_path.is_file():
        payload["status"] = "not_reviewed"
        payload["reason"] = "summary.json is missing"
        return payload
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    counts = summary.get("counts") or {}
    payload["counts"] = counts
    payload["status"] = _status_from_counts(counts)
    return payload


def _status_from_counts(counts: dict[str, Any]) -> str:
    if int(counts.get("http_5xx") or 0) > 0:
        return "http_5xx"
    if int(counts.get("fail") or 0) > 0:
        return "fail"
    return "pass"
