from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import Literal

from pydantic import ValidationError
from yaml import YAMLError

from heimdall_qa.campaign import find_latest_run
from heimdall_qa.project import ProjectView
from heimdall_qa.runner import optional_suite
from heimdall_qa.schema.load import content_root
from heimdall_qa.schema.load import iter_cases
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.load import load_round
from heimdall_qa.schema.load import load_yaml
from heimdall_qa.schema.load import resolve_path
from heimdall_qa.schema.load import split_selector
from heimdall_qa.suite_run import visible_steps
from heimdall_qa.validate import validate_round

NodeKind = Literal["campaign", "folder", "round", "case"]

_RANK = {
    "http_5xx": 0,
    "fail": 1,
    "not_ready": 2,
    "missing": 3,
    "not_reviewed": 4,
    "pending": 4,
    "skip": 5,
    "pass": 6,
}


@dataclass(frozen=True)
class TreeNode:
    key: str
    kind: NodeKind
    label: str
    status: str
    children: tuple[TreeNode, ...] = ()
    path: str | None = None
    round_id: str | None = None
    endpoint: str | None = None
    matrix: str | None = None
    case_id: str | None = None
    reason: str | None = None
    startable: bool = False
    expanded: bool = False
    environment: str = ""


def index_workspace(
    root: Path,
    runs_dir: Path,
    project: ProjectView | None = None,
) -> tuple[TreeNode, ...]:
    content = content_root(root, project)
    claimed: set[str] = set()
    campaigns: list[TreeNode] = []
    campaigns_dir = content / "campaigns"
    if campaigns_dir.is_dir():
        for path in sorted(campaigns_dir.glob("*.yaml")):
            node = _campaign_node(path, root, runs_dir, claimed, project)
            if node is not None:
                campaigns.append(node)
    orphans: list[TreeNode] = []
    rounds_dir = content / "rounds"
    if rounds_dir.is_dir():
        for path in sorted(rounds_dir.glob("*.yaml")):
            rel = _rel(path, content)
            if rel in claimed:
                continue
            orphans.append(
                inspect_round(
                    rel, root, runs_dir, project, endpoint=None, matrix=None
                )
            )
    return tuple(campaigns + orphans)


def inspect_round(
    rel: str,
    root: Path,
    runs_dir: Path,
    project: ProjectView | None = None,
    *,
    endpoint: str | None,
    matrix: str | None,
) -> TreeNode:
    round_path = resolve_path(root, rel, project)
    key = f"round:{rel}"
    if not round_path.is_file():
        return TreeNode(
            key=key,
            kind="round",
            label=endpoint or Path(rel).stem,
            status="missing",
            path=rel,
            endpoint=endpoint,
            matrix=matrix,
            reason="round file is missing",
            startable=False,
        )
    try:
        round_file = load_round(round_path)
    except (ValidationError, ValueError, OSError, YAMLError) as exc:
        return TreeNode(
            key=key,
            kind="round",
            label=endpoint or Path(rel).stem,
            status="not_ready",
            path=rel,
            endpoint=endpoint,
            matrix=matrix,
            reason=str(exc),
            startable=False,
        )
    label = endpoint or round_file.id
    children = _round_children(rel, round_file, root, project)
    errors = validate_round(round_path, root, project)
    if errors:
        return TreeNode(
            key=key,
            kind="round",
            label=label,
            status="not_ready",
            children=children,
            path=rel,
            round_id=round_file.id,
            endpoint=endpoint or round_file.id,
            matrix=matrix,
            reason=errors[0].summary(),
            startable=False,
            environment=round_file.environment,
        )
    run_dir = find_latest_run(runs_dir, round_file.id)
    status = "not_reviewed"
    if run_dir is not None:
        status = _status_from_run(run_dir)
        children = _apply_run_status(children, run_dir)
    return TreeNode(
        key=key,
        kind="round",
        label=label,
        status=status,
        children=children,
        path=rel,
        round_id=round_file.id,
        endpoint=endpoint or round_file.id,
        matrix=matrix,
        startable=True,
        environment=round_file.environment,
    )


def find_node(nodes: tuple[TreeNode, ...], key: str) -> TreeNode | None:
    for node in nodes:
        if node.key == key:
            return node
        found = find_node(node.children, key)
        if found is not None:
            return found
    return None


def expand_to(nodes: tuple[TreeNode, ...], selected: str) -> tuple[TreeNode, ...]:
    expanded: list[TreeNode] = []
    for node in nodes:
        kids = expand_to(node.children, selected)
        open_here = node.key == selected or any(
            child.expanded or child.key == selected for child in kids
        )
        expanded.append(replace(node, children=kids, expanded=open_here))
    return tuple(expanded)


def parent_round(nodes: tuple[TreeNode, ...], key: str) -> TreeNode | None:
    node = find_node(nodes, key)
    if node is None:
        return None
    if node.kind == "round":
        return node
    if node.kind == "case" and node.path:
        return find_node(nodes, f"round:{node.path}")
    return None


def find_step_dir(run_dir: Path, case_id: str) -> Path | None:
    steps = run_dir / "steps"
    if steps.is_dir():
        for child in sorted(steps.iterdir()):
            if not child.is_dir():
                continue
            if child.name.endswith(f"-{case_id}") or child.name == case_id:
                return child
    probes = run_dir / "probes"
    if probes.is_dir():
        direct = probes / case_id
        if direct.is_dir():
            return direct
        for child in sorted(probes.iterdir()):
            if child.is_dir() and case_id in child.name:
                return child
    return None


def first_startable(nodes: tuple[TreeNode, ...]) -> TreeNode | None:
    for node in nodes:
        if node.kind == "round" and node.startable:
            return node
        found = first_startable(node.children)
        if found is not None:
            return found
    return None


def _campaign_node(
    path: Path,
    root: Path,
    runs_dir: Path,
    claimed: set[str],
    project: ProjectView | None,
) -> TreeNode | None:
    try:
        campaign = load_campaign(path)
    except (ValidationError, ValueError, OSError, YAMLError):
        return None
    folders: dict[str, list[TreeNode]] = {}
    for entry in campaign.rounds:
        claimed.add(entry.round)
        child = inspect_round(
            entry.round,
            root,
            runs_dir,
            project,
            endpoint=entry.endpoint,
            matrix=entry.matrix,
        )
        folders.setdefault(entry.matrix, []).append(child)
    folder_nodes = tuple(
        TreeNode(
            key=f"folder:{campaign.id}:{matrix}",
            kind="folder",
            label=matrix,
            status=_rollup(tuple(children)),
            children=tuple(children),
            matrix=matrix,
        )
        for matrix, children in folders.items()
    )
    return TreeNode(
        key=f"campaign:{campaign.id}",
        kind="campaign",
        label=campaign.id,
        status=_rollup(folder_nodes),
        children=folder_nodes,
    )


def _round_children(
    rel: str,
    round_file: Any,
    root: Path,
    project: ProjectView | None = None,
) -> tuple[TreeNode, ...]:
    suite = optional_suite(round_file, root, project)
    if suite is not None:
        items = [
            _case_node(rel, visible.label, visible.label)
            for visible in visible_steps(suite)
        ]
        return tuple(items)
    items: list[TreeNode] = []
    for relative in round_file.include:
        try:
            found = list(iter_cases(root, [relative], project))
        except (ValidationError, ValueError, OSError, YAMLError):
            # A round whose case does not parse still has to appear in the tree:
            # the review screen is where the reason gets read, and a round that
            # vanished from the collection is a round nobody can fix.
            path, case_id = split_selector(relative)
            label = case_id or Path(path).stem
            items.append(_case_node(rel, label, label))
            continue
        for _, case in found:
            items.append(_case_node(rel, case.id, case.id))
    return tuple(items)


def _case_node(round_rel: str, case_id: str, label: str) -> TreeNode:
    return TreeNode(
        key=f"case:{round_rel}:{case_id}",
        kind="case",
        label=label,
        status="not_reviewed",
        path=round_rel,
        case_id=case_id,
        startable=False,
    )


def _apply_run_status(children: tuple[TreeNode, ...], run_dir: Path) -> tuple[TreeNode, ...]:
    updated: list[TreeNode] = []
    for child in children:
        if child.kind != "case" or not child.case_id:
            updated.append(child)
            continue
        step_dir = find_step_dir(run_dir, child.case_id)
        status = child.status
        if step_dir is not None:
            verdict = _read_json(step_dir / "verdict.json")
            raw = verdict.get("status")
            if raw in {"pass", "fail", "skip"}:
                status = str(raw)
        updated.append(replace(child, status=status))
    return tuple(updated)


def _status_from_run(run_dir: Path) -> str:
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        return "not_reviewed"
    summary = _read_json(summary_path)
    counts = summary.get("counts") if isinstance(summary, dict) else None
    if not isinstance(counts, dict):
        return "not_reviewed"
    if int(counts.get("http_5xx") or 0) > 0:
        return "http_5xx"
    if int(counts.get("fail") or 0) > 0:
        return "fail"
    if int(counts.get("pass") or 0) == 0 and int(counts.get("skip") or 0) > 0:
        return "skip"
    return "pass"


def _rollup(children: tuple[TreeNode, ...]) -> str:
    if not children:
        return "not_reviewed"
    worst = "pass"
    worst_rank = _RANK["pass"]
    for child in children:
        rank = _RANK.get(child.status, 4)
        if rank < worst_rank:
            worst = child.status
            worst_rank = rank
    return worst


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_campaign_yaml(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = load_yaml(path)
    except (OSError, ValueError, YAMLError):
        return False
    return isinstance(data.get("rounds"), list)
