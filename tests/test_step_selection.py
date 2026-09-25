"""Starting a suite run in the middle, and why the middle is a loop and not a probe.

`from_step` is the suite's half of `from_case`: a suite's steps are not cases, so
"run from here on" has to mean *the visible steps from this one on*. The queue is
where that is read — it is the list the reviewer sees on the unit card — and the two
refusals are the other half: a step that the suite does not have, and a step asked of
a round that lists cases instead of steps.

The run itself belongs to a provider's suite: `SuiteRun` asks the descriptor for a
provider's oracle, and the core's own descriptor deliberately names one that no
distribution here provides, so a core test can only check the decision and not the
replay.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from heimdall_qa import keys
from heimdall_qa.collection import index_workspace
from heimdall_qa.errors import HarnessError
from heimdall_qa.plan import CASE_FORWARD
from heimdall_qa.plan import ROUND
from heimdall_qa.plan import plan_for
from heimdall_qa.session import RoundSession
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"
SUITE_ROUND = FIXTURES / "rounds" / "two-loops.yaml"
SUITE_REL = "rounds/two-loops.yaml"

FIRST_LOOP = "loop chain-H01 ×2"
SECOND_LOOP = "loop chain-H01-linked ×1"
PROBE = "probe two-loops"

#: The id these tests index under, so a key built here and a node in the tree are
#: the same key.
PROJECT = "fixtures"
FIRST_LOOP_KEY = keys.for_step(PROJECT, SUITE_REL, 0)
SECOND_LOOP_KEY = keys.for_step(PROJECT, SUITE_REL, 1)


def _config(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    return config_for(project_at(_DESCRIPTOR, web=str(web), worker=str(worker)))


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(202, json={})))


def _session(tmp_path: Path, **kwargs) -> RoundSession:
    return RoundSession(
        SUITE_ROUND,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_client(),
        runs_dir=tmp_path / "runs",
        **kwargs,
    )


def test_the_whole_suite_queues_every_visible_step(tmp_path: Path):
    session = _session(tmp_path)
    view = session.view()

    assert [item.case_id for item in view.queue] == [FIRST_LOOP, SECOND_LOOP, PROBE]
    # `probe_begin` is not in the queue: it is a photograph, not a step to review.
    assert view.progress == (0, 3)


def test_from_step_queues_the_tail_and_not_the_steps_before_it(tmp_path: Path):
    session = _session(tmp_path, from_step=SECOND_LOOP, selection="from-loop-chain-h01-linked-1")

    assert [item.case_id for item in session.view().queue] == [SECOND_LOOP, PROBE]
    assert session.view().progress == (0, 2)


def test_a_step_that_the_suite_does_not_have_is_refused_by_name(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        _session(tmp_path, from_step="loop nothing-H01 ×2")

    assert raised.value.code == "CASE_INVALID"
    assert "loop nothing-H01 ×2" in raised.value.message


def test_a_step_of_a_round_that_is_not_a_suite_is_refused(tmp_path: Path):
    """A round that lists cases has no step to start at, and says which round."""
    with pytest.raises(HarnessError) as raised:
        RoundSession(
            FIXTURES / "rounds" / "chain-two.yaml",
            root=FIXTURES,
            config=_config(tmp_path),
            client=_client(),
            runs_dir=tmp_path / "runs",
            from_step=FIRST_LOOP,
        )

    assert raised.value.code == "CASE_INVALID"
    assert "not a suite" in raised.value.message


def test_the_plan_carries_the_step_and_the_token_the_run_is_named_for(tmp_path: Path):
    """From the button to the unit: the label the session matches, the name it writes.

    `loop chain-H01-linked ×1` is a sentence and a run directory is a name, so the two travel
    apart — and the count beside them is the tail of the suite, not the whole of it.
    """
    config = _config(tmp_path)
    tree = index_workspace(
        FIXTURES, tmp_path / "runs", config.project, project_id=PROJECT
    )
    plan = plan_for(
        tree,
        SECOND_LOOP_KEY,
        CASE_FORWARD,
        root=FIXTURES,
        project=config.project,
    )

    unit = plan.units[0]
    assert unit.from_step == SECOND_LOOP
    assert unit.from_case is None
    assert unit.case_total == 2
    assert unit.selection == "from-loop-chain-h01-linked-1"


def test_the_round_scope_on_the_same_loop_runs_the_whole_suite(tmp_path: Path):
    """The wider button beside it means the whole suite, not the step selected."""
    config = _config(tmp_path)
    tree = index_workspace(
        FIXTURES, tmp_path / "runs", config.project, project_id=PROJECT
    )
    plan = plan_for(
        tree,
        FIRST_LOOP_KEY,
        ROUND,
        root=FIXTURES,
        project=config.project,
    )

    assert plan.units[0].from_step is None
    assert plan.units[0].selection == ""
