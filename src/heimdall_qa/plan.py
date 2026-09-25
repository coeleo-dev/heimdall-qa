"""What a run is made of: a plan is an ordered list of units, one per round.

The review UI could only ever start the round under the cursor, because every path
into a run went through `WorkspaceSession._require_startable`, which resolves a
single `TreeNode` of kind `round`. A campaign is not a round and a case is not a
round, so neither had a route into execution — and the two things a reviewer
actually wants ("run this whole campaign", "run this one case") were both absent.

This module is that missing translation, and only that: it reads the collection
tree the workspace already built and answers "which rounds, in which order, each
with which selection". It replays nothing, holds no state, and knows no HTTP.

The order is not a detail. A campaign's rounds share `runs/shared-captures.json` —
A0 mints the JWT that A1..A4 spend — so a plan that reordered them would run
authenticated rounds against no credential and report a wall of 401s that look
like a product defect. For a campaign the order is the manifest's, read from the
file rather than inferred from the tree's grouping; for anything smaller it is the
tree's, which is the order the file lists its cases in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from heimdall_qa import keys
from heimdall_qa.collection import TreeNode
from heimdall_qa.collection import find_node
from heimdall_qa.collection import parent_round
from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.load import resolve_path
from heimdall_qa.step_kinds import LOOP
from heimdall_qa.step_kinds import PROBE

#: The scopes a plan can be asked for. `case` runs one case; `case_forward` runs
#: that case and every case after it in the round, which is the only shape that can
#: run a case whose credential an earlier case mints. `directory` runs every campaign
#: under a real folder, which is what makes sorting campaigns into folders useful for
#: anything beyond reading.
CAMPAIGN = "campaign"
DIRECTORY = "directory"
FOLDER = "folder"
ROUND = "round"
CASE = "case"
CASE_FORWARD = "case_forward"

SCOPES = (CAMPAIGN, DIRECTORY, FOLDER, ROUND, CASE, CASE_FORWARD)

#: What a case id or a suite step may look like in a run directory name. A case id is
#: already this by schema; a suite's visible step is a sentence (`loop chain ×2`) and
#: has to be folded into one token before it can ride in a filename.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
_UNSAFE_RUN = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class RunUnit:
    """One round to replay, and how much of it.

    `skip` is the honest alternative to dropping a unit: a round the campaign lists
    that cannot run still belongs in the plan, because a plan that silently omits it
    reports a coverage nobody asked for. The engine skips it with the reason the
    collection already worked out (`missing`, `not_ready`) and the strip shows why.
    """

    round: str
    round_id: str
    label: str
    only_case: str | None = None
    from_case: str | None = None
    #: The label of the suite step a `case_forward` run starts at, when the node
    #: selected was a step of a suite and not a case. `from_case` cannot carry it:
    #: a case id is a key of the round's case list and a step label is not — it is
    #: the sentence `loop chain ×2`, matched against `visible_steps`. The two are
    #: different namespaces, so they are different fields, and `selection` is what
    #: folds the second one into something a directory name can hold.
    from_step: str | None = None
    skip: str | None = None
    case_total: int = 0

    @property
    def selection(self) -> str:
        """What this unit replays, as the word that goes into the run directory name.

        A case id rides as it is, which is what keeps `~case-note-H01` the name the
        plan promised. A suite step does not: it can be a sentence with a space and a
        `×` in it, so it is folded into one token — `from-loop-chain-2` — and the same
        token is what `summary.json` records.
        """
        if self.only_case:
            return f"case-{_token(self.only_case)}"
        if self.from_case:
            return f"from-{_token(self.from_case)}"
        if self.from_step:
            return f"from-{_token(self.from_step)}"
        return ""

    @property
    def runnable(self) -> bool:
        return self.skip is None


@dataclass(frozen=True)
class RunPlan:
    """An ordered batch, and the scope it was asked for."""

    scope: str
    label: str
    units: tuple[RunUnit, ...]

    @property
    def runnable(self) -> tuple[RunUnit, ...]:
        return tuple(unit for unit in self.units if unit.runnable)

    @property
    def skipped(self) -> tuple[RunUnit, ...]:
        return tuple(unit for unit in self.units if not unit.runnable)

    @property
    def case_total(self) -> int:
        return sum(unit.case_total for unit in self.runnable)


def plan_for(
    nodes: tuple[TreeNode, ...],
    key: str,
    scope: str,
    *,
    root: Path,
    project: ProjectView | None = None,
) -> RunPlan:
    """The plan one collection node and scope add up to.

    Raises `ROUND_INVALID` for a node or a scope that do not belong together, which
    is the same code the UI already turns into "Não foi possível iniciar" — asking to
    run a campaign when a round is selected is a bad request, not a crash.
    """
    if scope not in SCOPES:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"unknown run scope: {scope}",
            hint=f"use one of {', '.join(SCOPES)}",
        )
    node = find_node(nodes, key)
    if node is None:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"unknown tree node: {key}",
            hint="reload the collection from the left tree",
        )
    if scope in {CAMPAIGN, DIRECTORY, FOLDER}:
        return _collection_plan(nodes, node, scope, root, project)
    if scope == ROUND:
        return _round_plan(nodes, node)
    return _case_plan(nodes, node, scope)


def scopes_for(node: TreeNode) -> tuple[str, ...]:
    """Which scopes the unit card should offer for the selected node.

    A case with no id is not runnable, and a not-ready round still gets its button:
    pressing it is how its reason gets read, and the alternative is a tree that looks
    like it has nothing to say about a round that will not start.

    A **suite step** is not a case, and which scopes it gets is the difference between
    two kinds of step:

    - A `loop` may start a run. "From here on" is a real question — the ingest loop
      plus the conference after it — and a suite run that starts in the middle still
      compares against a baseline, because the runner photographs any `probe_begin`
      before the first step it executes. The round is offered beside it.
    - A `probe` may not. Alone it would photograph the world and immediately read the
      same world back, so every `expect: unchanged` surface would pass without
      anything having happened — a green that measured nothing, which is the one
      failure this harness exists to refuse. A probe is reachable through the round.

    Neither offers `case`: a loop is a count and a probe is a comparison, and `case`
    would be asking the runner for a case file that does not exist.
    """
    if node.step_kind == LOOP:
        return (CASE_FORWARD, ROUND)
    if node.step_kind == PROBE:
        return (ROUND,)
    if node.kind == "case" and not node.case_id:
        return ()
    return scopes_for_kind(node.kind)


def scopes_for_kind(kind: str) -> tuple[str, ...]:
    """The same answer, from the node's kind alone.

    Kept as the one table so the panel that draws the buttons and the plan that has
    to honour them cannot disagree about which scopes exist.

    A `project` gets nothing on purpose. The engine runs one plan at a time and a
    project is "everything I have registered", which is a bigger question than a
    button on a card should answer; a folder is the scope a reviewer reaches for, and
    it is a thing they organised deliberately.
    """
    return {
        "project": (),
        "directory": (DIRECTORY,),
        "campaign": (CAMPAIGN,),
        "folder": (FOLDER,),
        "round": (ROUND,),
        "case": (CASE, CASE_FORWARD),
    }.get(kind, ())


def _round_plan(nodes: tuple[TreeNode, ...], node: TreeNode) -> RunPlan:
    """The whole round, whether the round was selected or a step of it.

    A suite step is not a case and cannot be run alone, so `scopes_for` offers the
    round beside "from here on"; this is the other half of that offer — the wider
    button has to mean the whole round and not the step that was selected.
    """
    if node.kind == "case":
        node = _round_of(nodes, node)
    if node.kind != "round":
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"{node.label} is not a round",
            hint="select a round or a case to run",
        )
    unit = _unit_from_node(node)
    return RunPlan(scope=ROUND, label=node.label, units=(unit,))


def _case_plan(nodes: tuple[TreeNode, ...], node: TreeNode, scope: str) -> RunPlan:
    if node.kind != "case" or not node.case_id:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"{node.label} is not a case",
            hint="select a case under a round to run it",
        )
    if scope == CASE_FORWARD:
        # A forward run needs the round node — for a case, to know how many cases
        # follow it; for a suite step, to know which steps follow. The unit is
        # rebuilt from the round rather than from the node alone.
        return RunPlan(
            scope=scope,
            label=node.case_id,
            units=(_forward_unit(nodes, node),),
        )
    if node.step_kind:
        # A suite step reached with the `case` scope: `scopes_for` never offers that
        # button, so this is a hand-made request, and the honest answer is the one
        # the runner would give — the step is not a case and there is no such run.
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"{node.label} is a step of a suite, not a case",
            hint="run the round, or start from this step forward",
        )
    return RunPlan(
        scope=scope,
        label=node.case_id,
        units=(
            replace(
                _unit_from_node(_round_of(nodes, node)),
                only_case=node.case_id,
                case_total=1,
            ),
        ),
    )


def _forward_unit(nodes: tuple[TreeNode, ...], node: TreeNode) -> RunUnit:
    """The unit a `case_forward` run is made of, for a case or for a suite step.

    The two are the same idea — start here and go on — but they are told apart by
    the round they belong to. A case carries `from_case`, which the session resolves
    against the round's case list; a step carries `from_step`, which the session
    resolves against the suite's `visible_steps`. Only one of the two is meaningful
    for a given round, and setting the wrong one would have the session look for a
    case named after a loop.
    """
    round_node = _round_of(nodes, node)
    unit = _unit_from_node(round_node)
    if node.step_kind:
        return replace(
            unit,
            from_step=node.case_id,
            case_total=_cases_from(round_node, node.case_id),
        )
    return replace(
        unit,
        from_case=node.case_id,
        case_total=_cases_from(round_node, node.case_id),
    )


def _round_of(nodes: tuple[TreeNode, ...], node: TreeNode) -> TreeNode:
    """The round a case node belongs to.

    A case reached without its round is a broken selection and not a plan, so this
    fails by name instead of producing a unit that would replay the wrong file.
    """
    round_node = parent_round(nodes, node.key)
    if round_node is None:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"the round of {node.label} is not in the collection",
            hint="reload the collection, or run the round itself",
        )
    return round_node


def _cases_from(round_node: TreeNode, case_id: str) -> int:
    """How many units run when the plan starts at `case_id` and goes on.

    The same count answers for a case and for a suite step: in both cases the round
    node's children are exactly the units the round can be told to start at, and a
    round is either a list of cases or a suite's visible steps, never both.
    """
    ids = [child.case_id for child in round_node.children]
    if case_id not in ids:
        return 0
    return len(ids) - ids.index(case_id)


def _token(value: str) -> str:
    """A name a run directory can hold, from a name a tree label can be.

    Case ids already pass through unchanged, because the schema keeps them to
    `[A-Za-z0-9_-]`. A suite step's label does not — `loop chain ×2` is a sentence —
    and the run directory is also what `schedule` and the operator read, so the
    folding has to be deterministic and readable rather than a hash. The label the
    session matches on travels separately, in `from_step`; only the directory name
    and the `selection` in `summary.json` are lossy, and both are for people.
    """
    if _SAFE_TOKEN.match(value):
        return value
    return _UNSAFE_RUN.sub("-", value).strip("-").lower()


def _collection_plan(
    nodes: tuple[TreeNode, ...],
    node: TreeNode,
    scope: str,
    root: Path,
    project: ProjectView | None,
) -> RunPlan:
    """A campaign, a flow or a folder, in the order the campaign manifest lists them.

    Reached through the campaign node's `path`, which the collection puts there for
    exactly this: reading the manifest is the only way to get the order the captures
    depend on, and a plan that guessed it from the tree would be resting on the
    manifests' habit of grouping rounds by matrix.

    A folder is several campaigns, and *their* order is the path's. That is a choice
    and not an accident: a folder can hold campaigns from any part of the tree, and the
    only order that survives a rename is the one the files themselves carry. Inside one
    campaign the manifest still rules, because that is where the capture chain is
    declared.
    """
    _require_collection_kind(node, scope)
    if scope == DIRECTORY:
        campaign_nodes = _campaigns_under(node)
    else:
        campaign_nodes = (_campaign_of(nodes, node),)
    units: list[RunUnit] = []
    for campaign_node in campaign_nodes:
        units.extend(
            _campaign_units(
                nodes,
                campaign_node,
                root,
                project,
                matrix=node.matrix if scope == FOLDER else None,
            )
        )
    return RunPlan(
        scope=scope,
        label=node.label,
        units=tuple(units),
    )


#: What each collection scope's node kind is called in a refusal, and what to do about
#: it. One table so the three branches of `_require_collection_kind` cannot word the
#: same mistake three ways.
_COLLECTION_KIND = {
    CAMPAIGN: ("campaign", "campaign", "select a campaign to run all of it"),
    DIRECTORY: ("directory", "folder", "select a folder to run every campaign under it"),
    FOLDER: ("folder", "flow", "select a flow to run all of it"),
}


def _require_collection_kind(node: TreeNode, scope: str) -> None:
    """Refuse a collection scope asked of a node that is not one."""
    expected, word, hint = _COLLECTION_KIND[scope]
    if node.kind == expected:
        return
    raise HarnessError(
        code="ROUND_INVALID",
        message=f"{node.label} is not a {word}",
        hint=hint,
    )


def _campaigns_under(node: TreeNode) -> tuple[TreeNode, ...]:
    """Every campaign in a folder's subtree, ordered by the file's path.

    Recursive because folders nest, and sorted by `path` rather than left in tree order
    so that two folders holding the same campaigns run them the same way.
    """
    found: list[TreeNode] = []
    for child in node.children:
        if child.kind == "campaign":
            found.append(child)
        elif child.kind == "directory":
            found.extend(_campaigns_under(child))
    return tuple(sorted(found, key=lambda item: item.path or ""))


def _campaign_units(
    nodes: tuple[TreeNode, ...],
    campaign_node: TreeNode,
    root: Path,
    project: ProjectView | None,
    *,
    matrix: str | None,
) -> list[RunUnit]:
    """One campaign's rounds as units, optionally narrowed to a single matrix.

    The round is looked up with the campaign's own project id, which is what makes this
    correct in a tree holding several projects: a manifest lists a path relative to its
    own content root, and the same path in another project is another round.
    """
    if campaign_node.path is None:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"the campaign behind {campaign_node.label} is not known",
            hint="reload the collection from the left tree",
        )
    campaign = load_campaign(resolve_path(root, campaign_node.path, project))
    entries = campaign.rounds
    if matrix is not None:
        entries = [entry for entry in entries if entry.matrix == matrix]
    units: list[RunUnit] = []
    for entry in entries:
        round_node = find_node(nodes, keys.for_round(campaign_node.project, entry.round))
        if round_node is None:
            units.append(_missing_unit(entry.round, entry.endpoint))
            continue
        units.append(_unit_from_node(round_node))
    return units


def _campaign_of(nodes: tuple[TreeNode, ...], node: TreeNode) -> TreeNode:
    """The campaign node a flow hangs from.

    Read off the node's own `campaign_id` and not out of its key. The key is opaque by
    design — `keys.py` builds it and nothing parses it back — and a flow has no path of
    its own to read the manifest from, so the field is the only honest source.
    """
    if node.kind == "campaign":
        return node
    if node.kind != "folder" or not node.campaign_id:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"{node.label} does not belong to a campaign",
            hint="select a campaign or one of its flows",
        )
    campaign_node = find_node(nodes, keys.for_campaign(node.project, node.campaign_id))
    if campaign_node is None:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"the campaign of {node.label} is not in the collection",
            hint="reload the collection from the left tree",
        )
    return campaign_node


def _unit_from_node(round_node: TreeNode) -> RunUnit:
    if not round_node.path:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"{round_node.label} has no round file",
            hint="reload the collection from the left tree",
        )
    return RunUnit(
        round=round_node.path,
        round_id=round_node.round_id or round_node.label,
        label=round_node.endpoint or round_node.round_id or round_node.label,
        skip=None if round_node.startable else (round_node.reason or "round is not startable"),
        case_total=len(round_node.children),
    )


def _missing_unit(round_path: str, endpoint: str) -> RunUnit:
    """A round the campaign lists but the collection never saw.

    It cannot be resolved to a node, so it cannot be read for an id either; the
    path stands in. The unit is skipped, never run, and says why.
    """
    return RunUnit(
        round=round_path,
        round_id=Path(round_path).stem,
        label=endpoint or Path(round_path).stem,
        skip="round file is missing",
    )
