from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from yaml import YAMLError

from heimdall_qa import keys
from heimdall_qa.campaign import find_latest_run
from heimdall_qa.keys import NodeKind
from heimdall_qa.project import ProjectView
from heimdall_qa.projects import ProjectRef
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

#: The suffix a round or a campaign file carries. Matches the `*.yaml` glob this used,
#: which is deliberately narrower than what the editor accepts: a `.yml` file has never
#: appeared in a collection, and widening the tree is not a change to smuggle in here.
_YAML_SUFFIX = ".yaml"

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
    #: `"loop"` or `"probe"` when this node is a visible step of a suite round, empty
    #: for a case file. A suite's steps are what the tree can show and what the run's
    #: queue is keyed by, and they are *not* cases a runner can be handed alone: a
    #: loop is a count and a probe is a comparison against a baseline. The scope table
    #: reads this instead of reading `kind == "case"` as "runnable on its own", and
    #: only a loop is a step a run may start at.
    step_kind: str = ""
    #: The project this node belongs to, and the second segment of its key. Carried on
    #: the node so a walk that needs to build a relative's key — `parent_round` going
    #: from a case up to its round — does not have to be told which project it is in.
    #: The alternative is a `project_id` threaded beside every node through every
    #: helper, which is how two callers end up disagreeing about which project a key
    #: belongs to. Empty only for a node built outside a project, which is a test.
    project: str = ""
    #: The campaign a `folder` (Fluxo) node hangs from. `matrix` says which grouping it
    #: is; this says whose. `plan._campaign_of` used to recover it by splitting the key
    #: on `:` — exactly the parsing `keys.py` refuses, because a suite step's id is a
    #: sentence and nothing stops a path or a matrix from holding a colon too.
    campaign_id: str | None = None
    #: `"running"` or `"awaiting"` while this node holds the case a run is on, empty the
    #: rest of the time. Set where the queue is painted and nowhere else, because the
    #: queue is the only thing that knows *which row* it is on: a suite lists the same
    #: step label six times, so a client that matched a case id against the rows would
    #: mark every one of them. The walk that paints the verdicts is the same walk that
    #: knows the position, so the mark is decided here and shipped rather than guessed.
    live: str = ""


def index_workspace(
    root: Path,
    runs_dir: Path,
    project: ProjectView | None = None,
    *,
    project_id: str = "",
) -> tuple[TreeNode, ...]:
    """One project's content: its folders, its campaigns and its orphan rounds.

    `project_id` qualifies every key built here, and it defaults to the empty string so
    that a caller holding only a path — a test, a one-off index — keeps working at the
    cost of keys that are not unique across projects. A session always passes a real one.

    Folders are real and recursive: `campaigns/` is walked rather than globbed, so a
    campaign sorted into `campaigns/ingest/` appears under that folder. An empty folder
    survives too — a folder the reviewer just created has to be somewhere before
    anything can be moved into it, and a tree that hid it would make the creation look
    like it failed.
    """
    content = content_root(root, project)
    claimed: set[str] = set()
    directories, campaigns = _campaign_subtree(
        content / "campaigns",
        content,
        root,
        runs_dir,
        project,
        project_id,
        claimed,
    )
    orphans = _orphan_rounds(content, root, runs_dir, project, project_id, claimed)
    return (*directories, *campaigns, *orphans)


def index_project(project: ProjectRef) -> TreeNode:
    """One registered project, as the root of its own subtree.

    The resolved root rides on the node's `path` so the client can say where a project
    lives without a second request, and the roll-up rides on its status so a project
    whose every round passed reads as `pass` and not as an unreadable pile.
    """
    children = index_workspace(
        project.root,
        project.runs_dir,
        project.config.project,
        project_id=project.id,
    )
    return TreeNode(
        key=keys.for_project(project.id),
        kind="project",
        label=project.name,
        status=rollup(children),
        children=children,
        path=str(project.root),
        project=project.id,
    )


def index_projects(projects: Sequence[ProjectRef]) -> tuple[TreeNode, ...]:
    """Every registered project, in the order the registry lists them.

    The order is the registry's and not alphabetical: it is the order the reviewer
    added them, which is the order they think about them, and a registry file is
    hand-editable when that order is wrong.
    """
    return tuple(index_project(project) for project in projects)


def _campaign_subtree(
    directory: Path,
    content: Path,
    root: Path,
    runs_dir: Path,
    project: ProjectView | None,
    project_id: str,
    claimed: set[str],
) -> tuple[tuple[TreeNode, ...], tuple[TreeNode, ...]]:
    """The folders and campaigns under one directory, as `(directories, campaigns)`.

    Folders come first in the answer because they come first in the tree: a reader
    scanning for "the folder I made" should not have to look past forty campaigns for
    it. That is the ordering every file explorer uses, and it costs a caller nothing
    because the two lists are returned separately and concatenated by the caller.

    A directory whose resolved path leaves the content root is skipped rather than
    followed. One check retires two problems: a symlink that points outside the project
    would otherwise leak absolute paths into the tree, and a symlink that points at an
    ancestor would make this recursion never end.
    """
    if not directory.is_dir():
        return (), ()
    directories: list[TreeNode] = []
    campaigns: list[TreeNode] = []
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        if child.is_dir():
            if not _inside(child, content):
                continue
            sub_directories, sub_campaigns = _campaign_subtree(
                child, content, root, runs_dir, project, project_id, claimed
            )
            children = (*sub_directories, *sub_campaigns)
            directories.append(
                TreeNode(
                    key=keys.for_directory(project_id, _rel(child, content)),
                    kind="directory",
                    label=child.name,
                    status=rollup(children),
                    children=children,
                    path=_rel(child, content),
                    project=project_id,
                )
            )
            continue
        if child.suffix != _YAML_SUFFIX:
            continue
        node = _campaign_node(child, root, runs_dir, claimed, project, project_id)
        if node is not None:
            campaigns.append(node)
    return tuple(directories), tuple(campaigns)


def _orphan_rounds(
    content: Path,
    root: Path,
    runs_dir: Path,
    project: ProjectView | None,
    project_id: str,
    claimed: set[str],
) -> tuple[TreeNode, ...]:
    """Rounds no campaign listed, as siblings of the campaigns.

    Only the top level of `rounds/` is walked. A campaign's `include:` names rounds
    relative to the content root and never in a subdirectory, so a round in one is not
    something a campaign can reach, and walking further would be inventing a layout the
    loaders do not read.
    """
    rounds_dir = content / "rounds"
    if not rounds_dir.is_dir():
        return ()
    nodes: list[TreeNode] = []
    for path in sorted(rounds_dir.glob(f"*{_YAML_SUFFIX}")):
        rel = _rel(path, content)
        if rel in claimed:
            continue
        nodes.append(
            inspect_round(
                rel,
                root,
                runs_dir,
                project,
                project_id=project_id,
                endpoint=None,
                matrix=None,
            )
        )
    return tuple(nodes)


def inspect_round(
    rel: str,
    root: Path,
    runs_dir: Path,
    project: ProjectView | None = None,
    *,
    project_id: str = "",
    endpoint: str | None,
    matrix: str | None,
) -> TreeNode:
    round_path = resolve_path(root, rel, project)
    key = keys.for_round(project_id, rel)
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
            project=project_id,
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
            project=project_id,
        )
    label = endpoint or round_file.id
    children = _round_children(rel, round_file, root, project, project_id=project_id)
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
            project=project_id,
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
        project=project_id,
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
        # Built from the node's own project, which is the whole reason `project` is a
        # field: the caller asked "which round is this case in" and should not have to
        # know, or be trusted to know, which project the case came from.
        return find_node(nodes, keys.for_round(node.project, node.path))
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
    project_id: str,
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
            project_id=project_id,
            endpoint=entry.endpoint,
            matrix=entry.matrix,
        )
        folders.setdefault(entry.matrix, []).append(child)
    folder_nodes = tuple(
        TreeNode(
            key=keys.for_folder(project_id, campaign.id, matrix),
            kind="folder",
            label=matrix,
            status=rollup(tuple(children)),
            children=tuple(children),
            matrix=matrix,
            project=project_id,
            campaign_id=campaign.id,
        )
        for matrix, children in folders.items()
    )
    return TreeNode(
        key=keys.for_campaign(project_id, campaign.id),
        kind="campaign",
        label=campaign.id,
        status=rollup(folder_nodes),
        children=folder_nodes,
        #: The campaign file itself, content-relative. A plan built from this node
        #: reads the manifest to get the rounds in the order it declares them, which
        #: is the order the captures need: A0 mints the JWT that A1..A4 spend. The
        #: tree happens to preserve that order today because the manifests group by
        #: matrix, and depending on that grouping would be depending on a convention
        #: this node can simply carry the answer to.
        path=_rel(path, content_root(root, project)),
        project=project_id,
    )


def _round_children(
    rel: str,
    round_file: Any,
    root: Path,
    project: ProjectView | None = None,
    *,
    project_id: str = "",
) -> tuple[TreeNode, ...]:
    suite = optional_suite(round_file, root, project)
    if suite is not None:
        # By position and not by label: a step's label is built from the case it runs,
        # and two steps over one case would otherwise be two nodes with one key. The
        # label still rides on `case_id` and `label` — the plan reads it, and it is
        # what the queue matches — only the key is positional.
        items = [
            _case_node(
                rel,
                visible.label,
                visible.label,
                project_id=project_id,
                step_kind=visible.kind,
                key=keys.for_step(project_id, rel, index),
            )
            for index, visible in enumerate(visible_steps(suite))
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
            items.append(_case_node(rel, label, label, project_id=project_id))
            continue
        for _, case in found:
            items.append(_case_node(rel, case.id, case.id, project_id=project_id))
    return tuple(items)


def _case_node(
    round_rel: str,
    case_id: str,
    label: str,
    *,
    project_id: str = "",
    step_kind: str = "",
    key: str | None = None,
) -> TreeNode:
    """One row of a round: a case of it, or one visible step of a suite.

    `key` overrides the case-keyed default, and only a suite uses it: a step is named
    by its position there because its label is not unique by construction (see
    `keys.for_step`). Every other caller leaves it, and a case is keyed by its id.
    """
    return TreeNode(
        key=key or keys.for_case(project_id, round_rel, case_id),
        kind="case",
        label=label,
        status="not_reviewed",
        path=round_rel,
        case_id=case_id,
        startable=False,
        step_kind=step_kind,
        project=project_id,
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


def rollup(children: tuple[TreeNode, ...]) -> str:
    """The worst status among `children`, which is what a parent's badge shows.

    Public because the live overlay re-derives it too: a node whose children just moved
    has to say the same thing here as it would after a re-index, and two answers to
    "what does this round read as" is exactly the drift this avoids.
    """
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


def _inside(path: Path, root: Path) -> bool:
    """Whether `path` resolves to somewhere at or under `root`.

    Asked about directories before descending into them. A symlink out of the content
    root is one problem, and a symlink pointing at one of its own ancestors is a second
    one this recursion cannot survive; the same resolved-path check retires both.
    """
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


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
