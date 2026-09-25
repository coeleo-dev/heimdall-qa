"""`plan_for`: what a scope on a node has to add up to.

The translation the UI depends on, tested without a server and without a run. Every
assertion here is about an answer the reviewer would otherwise have to discover by
starting 41 rounds: which rounds, in which order, and which of them cannot run.
"""

from pathlib import Path

import pytest

from heimdall_qa import keys
from heimdall_qa.collection import TreeNode
from heimdall_qa.collection import find_node
from heimdall_qa.collection import index_workspace
from heimdall_qa.errors import HarnessError
from heimdall_qa.plan import CAMPAIGN
from heimdall_qa.plan import CASE
from heimdall_qa.plan import CASE_FORWARD
from heimdall_qa.plan import FOLDER
from heimdall_qa.plan import ROUND
from heimdall_qa.plan import plan_for
from heimdall_qa.plan import scopes_for
from heimdall_qa.plan import scopes_for_kind
from heimdall_qa.step_kinds import LOOP
from heimdall_qa.step_kinds import PROBE
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"

#: What the tree is indexed under here. The plan is read by key, so the key and the
#: index have to agree; both go through `keys` so the format lives in one module.
PROJECT = "fixtures"

EXAMPLE = keys.for_round(PROJECT, "rounds/example.yaml")
WALK_HN = keys.for_round(PROJECT, "rounds/walk-hn.yaml")
CHAIN = keys.for_round(PROJECT, "rounds/chain-two.yaml")
CHAIN_LINKED = keys.for_case(PROJECT, "rounds/chain-two.yaml", "chain-H01-linked")
CHAIN_ANCHOR = keys.for_case(PROJECT, "rounds/chain-two.yaml", "chain-H01")
H01_ONLY = keys.for_round(PROJECT, "rounds/h01-only.yaml")
NEVER_SEEN = keys.for_round(PROJECT, "rounds/never-seen.yaml")
INTERLEAVED = keys.for_campaign(PROJECT, "interleaved")
INTERLEAVED_FLOW = keys.for_folder(PROJECT, "interleaved", "A4")
MISSING_CAMPAIGN = keys.for_campaign(PROJECT, "missing-round")
EXAMPLE_FLOW = keys.for_folder(PROJECT, "example-campaign", "A4")
TWO_LOOPS = "rounds/two-loops.yaml"


def _tree(tmp_path: Path) -> tuple[TreeNode, ...]:
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    config = config_for(
        project_at(_DESCRIPTOR, web=str(web), worker=str(worker)),
    )
    return index_workspace(
        FIXTURES, tmp_path / "runs", config.project, project_id=PROJECT
    )


def _project(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    return config_for(project_at(_DESCRIPTOR, web=str(web), worker=str(worker))).project


def _plan(tmp_path: Path, key: str, scope: str):
    tree = _tree(tmp_path)
    return plan_for(tree, key, scope, root=FIXTURES, project=_project(tmp_path))


def test_campaign_plan_reads_the_manifest_order_not_the_tree_order(tmp_path: Path):
    """The order is a dependency, so it is read where it is declared.

    `interleaved.yaml` lists A4, B1, A4, and the tree renders A4's two rounds
    together. The plan runs them in the manifest's order, which is the order whose
    captures work.
    """
    tree = _tree(tmp_path)
    plan = plan_for(
        tree, INTERLEAVED, CAMPAIGN, root=FIXTURES, project=_project(tmp_path)
    )

    assert plan.label == "interleaved"
    assert [unit.round for unit in plan.units] == [
        "rounds/order-first.yaml",
        "rounds/order-second.yaml",
        "rounds/order-third.yaml",
    ]
    assert all(unit.runnable for unit in plan.units)

    # The tree, walked the way a plan that trusted it would walk it, disagrees.
    folder = find_node(tree, INTERLEAVED_FLOW)
    assert folder is not None
    assert [child.path for child in folder.children] == [
        "rounds/order-first.yaml",
        "rounds/order-third.yaml",
    ]


def test_campaign_plan_skips_a_missing_round_with_its_reason(tmp_path: Path):
    """A hole in the campaign is reported, never silently dropped.

    A plan that omitted the round would report a coverage the campaign never had, so
    the round stays and carries the reason the collection already worked out.
    """
    plan = _plan(tmp_path, MISSING_CAMPAIGN, CAMPAIGN)

    assert plan.runnable == ()
    assert len(plan.skipped) == 1
    assert plan.skipped[0].skip == "round file is missing"
    assert plan.skipped[0].round == "rounds/does-not-exist.yaml"


def test_folder_plan_is_the_rounds_of_that_matrix(tmp_path: Path):
    plan = _plan(tmp_path, EXAMPLE_FLOW, FOLDER)

    assert [unit.round for unit in plan.units] == ["rounds/example.yaml"]


def test_round_plan_is_the_round_itself(tmp_path: Path):
    plan = _plan(tmp_path, EXAMPLE, ROUND)

    assert len(plan.units) == 1
    assert plan.units[0].selection == ""
    assert plan.units[0].only_case is None
    assert plan.case_total == 1


def test_case_plan_narrows_the_round_to_one_case(tmp_path: Path):
    plan = _plan(tmp_path, CHAIN_LINKED, CASE)

    unit = plan.units[0]
    assert unit.only_case == "chain-H01-linked"
    assert unit.selection == "case-chain-H01-linked"
    assert unit.round == "rounds/chain-two.yaml"
    assert plan.case_total == 1


def test_case_forward_plan_counts_the_cases_after_the_anchor(tmp_path: Path):
    """`from_case` is the smallest scope that can run a case whose capture precedes it."""
    anchor = CHAIN_ANCHOR
    plan = _plan(tmp_path, anchor, CASE_FORWARD)

    unit = plan.units[0]
    assert unit.from_case == "chain-H01"
    assert unit.only_case is None
    assert unit.selection == "from-chain-H01"
    assert plan.case_total == 2

    last = _plan(tmp_path, CHAIN_LINKED, CASE_FORWARD)
    assert last.case_total == 1


def test_a_not_ready_round_enters_the_plan_skipped(tmp_path: Path):
    """`h01-only` has a coverage gap. It is not startable, and the plan says why."""
    plan = _plan(tmp_path, H01_ONLY, ROUND)

    assert plan.runnable == ()
    assert "COVERAGE_GAP" in (plan.skipped[0].skip or "")
    assert plan.case_total == 0


def test_an_unknown_scope_is_a_bad_request(tmp_path: Path):
    with pytest.raises(HarnessError) as caught:
        _plan(tmp_path, EXAMPLE, "everything")

    assert caught.value.code == "ROUND_INVALID"


def test_a_scope_that_does_not_belong_to_the_node_is_refused(tmp_path: Path):
    """Running "the campaign" with a round selected is a bad request, not a crash."""
    with pytest.raises(HarnessError) as campaign_on_round:
        _plan(tmp_path, EXAMPLE, CAMPAIGN)
    assert campaign_on_round.value.code == "ROUND_INVALID"

    with pytest.raises(HarnessError) as case_on_round:
        _plan(tmp_path, EXAMPLE, CASE)
    assert case_on_round.value.code == "ROUND_INVALID"

    with pytest.raises(HarnessError) as folder_on_campaign:
        _plan(tmp_path, INTERLEAVED, FOLDER)
    assert folder_on_campaign.value.code == "ROUND_INVALID"


def test_an_unknown_node_is_a_bad_request(tmp_path: Path):
    with pytest.raises(HarnessError) as caught:
        _plan(tmp_path, NEVER_SEEN, ROUND)

    assert caught.value.code == "ROUND_INVALID"


def test_scopes_for_kind_is_the_one_table(tmp_path: Path):
    """The panel that draws the buttons and the plan that honours them share this."""
    assert scopes_for_kind("campaign") == (CAMPAIGN,)
    assert scopes_for_kind("folder") == (FOLDER,)
    assert scopes_for_kind("round") == (ROUND,)
    assert scopes_for_kind("case") == (CASE, CASE_FORWARD)
    assert scopes_for_kind("workspace") == ()


def test_a_case_node_without_an_id_offers_no_scope(tmp_path: Path):
    empty_case = TreeNode(
        key=keys.for_case(PROJECT, "rounds/x.yaml", ""),
        kind="case",
        label="(sem id)",
        status="not_ready",
        case_id=None,
    )

    assert scopes_for(empty_case) == ()
    assert scopes_for(TreeNode(key=EXAMPLE, kind="round", label="x", status="pending")) == (
        ROUND,
    )


def test_a_suite_loop_offers_the_round_and_the_tail_behind_it(tmp_path: Path):
    """A loop is not a case, but it can start a run — and the run stays honest.

    `loop chain-H01 ×2` is a count, so the smallest unit is not "this case": it is the
    loop and everything after it. That is exactly what `case_forward` means, so the
    step offers it beside the round, and the plan says which step the run starts at.
    The baseline survives the truncation because the runner photographs any
    `probe_begin` before the first step it executes.
    """
    tree = _tree(tmp_path)
    step = find_node(tree, keys.for_step(PROJECT, TWO_LOOPS, 0))
    assert step is not None
    assert step.step_kind == LOOP

    assert scopes_for(step) == (CASE_FORWARD, ROUND)

    forward = plan_for(tree, step.key, CASE_FORWARD, root=FIXTURES, project=_project(tmp_path))
    unit = forward.units[0]
    assert unit.round == "rounds/two-loops.yaml"
    assert unit.from_step == "loop chain-H01 ×2"
    assert unit.from_case is None
    assert unit.only_case is None
    # The suite has three visible steps and the run is the tail of them, not the
    # whole round: `loop chain-H01 ×2`, `loop chain-H01-linked ×1`, `probe two-loops`.
    assert unit.case_total == 3
    # `loop chain-H01 ×2` is a sentence and the run directory is a name, so the two are
    # told apart: the session matches the label, the directory takes the token.
    assert unit.selection == "from-loop-chain-h01-2"

    middle = find_node(tree, keys.for_step(PROJECT, TWO_LOOPS, 1))
    assert middle is not None
    tail = plan_for(tree, middle.key, CASE_FORWARD, root=FIXTURES, project=_project(tmp_path))
    assert tail.units[0].case_total == 2


def test_a_suite_probe_offers_only_the_round(tmp_path: Path):
    """A probe alone would measure nothing, so it does not get a button of its own.

    `probe two-loops` compares a surface against the photograph `probe_begin` took.
    Reached on its own it would photograph the world and read it straight back, so
    every `expect: unchanged` would pass without anything having happened — a green
    that measured nothing. The scope table refuses it, and the round still runs it.
    """
    tree = _tree(tmp_path)
    probe = find_node(tree, keys.for_step(PROJECT, TWO_LOOPS, 2))
    assert probe is not None
    assert probe.step_kind == PROBE

    assert scopes_for(probe) == (ROUND,)

    plan = plan_for(tree, probe.key, ROUND, root=FIXTURES, project=_project(tmp_path))
    assert plan.label == "two-loops"
    assert len(plan.units) == 1
    assert plan.units[0].round == "rounds/two-loops.yaml"
    assert plan.units[0].from_step is None
    assert plan.units[0].from_case is None
    assert plan.units[0].runnable
