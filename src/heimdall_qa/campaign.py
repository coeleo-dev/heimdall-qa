from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.models import CampaignFile
from heimdall_qa.schema.models import CampaignRound
from heimdall_qa.session_validate import validate_campaign_chain
from heimdall_qa.validate import resolve_path
from heimdall_qa.validate import validate_round

_RUN_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}-(.+?)(?:~\d+)?$")


def validate_campaign(campaign_path: Path, root: Path) -> list[str]:
    try:
        campaign = load_campaign(campaign_path)
    except (ValidationError, ValueError, OSError) as exc:
        return [f"{campaign_path}: {exc}"]
    errors: list[str] = []
    for entry in campaign.rounds:
        round_path = resolve_path(root, entry.round)
        if not round_path.is_file():
            errors.append(f"{entry.round}: round file is missing")
            continue
        for error in validate_round(round_path, root):
            errors.append(f"{entry.round}: {error}")
    errors.extend(validate_campaign_chain(campaign, root))
    return errors


def campaign_status(
    campaign_path: Path,
    root: Path,
    runs_dir: Path,
) -> dict[str, Any]:
    campaign = load_campaign(campaign_path)
    return {
        "id": campaign.id,
        "environment": campaign.environment,
        "rounds": [
            _round_status(entry, root, runs_dir, campaign) for entry in campaign.rounds
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
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "round": entry.round,
        "endpoint": entry.endpoint,
        "matrix": entry.matrix,
        "dto": entry.dto,
        "auth": entry.auth,
        "campaign_id": campaign.id,
    }
    round_path = resolve_path(root, entry.round)
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
