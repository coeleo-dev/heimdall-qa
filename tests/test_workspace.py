from pathlib import Path

import httpx
import pytest

from heimdall_qa import keys
from heimdall_qa.collection import TreeNode
from heimdall_qa.collection import find_node
from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError
from heimdall_qa.projects import id_for
from heimdall_qa.session import QueueItem
from heimdall_qa.session import RoundSession
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at
from heimdall_qa.workspace import WorkspaceSession
from heimdall_qa.workspace import _overlay_queue

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"
H01_ONLY = FIXTURES / "rounds" / "h01-only.yaml"
TWO_LOOPS = FIXTURES / "rounds" / "two-loops.yaml"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"

#: `WorkspaceSession` derives a project id from the root it is handed, so a key
#: built here and a key from the tree agree only if this is the same derivation.
PROJECT = id_for(FIXTURES)
TWO_LOOPS_KEY = keys.for_round(PROJECT, "rounds/two-loops.yaml")
EXAMPLE_CAMPAIGN = keys.for_campaign(PROJECT, "example-campaign")


def _http() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"status": "ACCEPTED"})

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)


def _config(tmp_path: Path) -> HarnessConfig:
    """Harness settings bound to the project in force, with this test's logs."""
    return config_for(
        project_at(
            _DESCRIPTOR,
            web=str(tmp_path / "web.log"),
            worker=str(tmp_path / "worker.log"),
        )
    )


def _workspace(tmp_path: Path, *, focus: Path | None = None) -> WorkspaceSession:
    return WorkspaceSession(
        root=FIXTURES,
        config=_config(tmp_path),
        client=_http(),
        runs_dir=tmp_path / "runs",
        focus=focus,
    )


def test_workspace_indexes_fixtures_and_selects_focus(tmp_path: Path):
    workspace = _workspace(tmp_path, focus=WALK_HN)
    view = workspace.view()
    assert view.selected.path == "rounds/walk-hn.yaml"
    assert view.pane == "start"
    assert view.selected.startable is True


def test_not_ready_round_cannot_start(tmp_path: Path):
    workspace = _workspace(tmp_path, focus=H01_ONLY)
    with pytest.raises(HarnessError) as caught:
        workspace.start("round", "walk")
    assert caught.value.code == "ROUND_INVALID"


def test_rerun_creates_a_new_run_directory(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    workspace = WorkspaceSession(
        root=FIXTURES,
        config=_config(tmp_path),
        client=_http(),
        runs_dir=tmp_path / "runs",
        focus=WALK_HN,
    )
    workspace.start("round", "walk")
    workspace.wait_settled(timeout=30)
    first = workspace.view().session.run_dir
    workspace.apply_verdict("fail", "stop after happy path", False)
    workspace.wait_settled(timeout=30)
    workspace.start("round", "walk")
    workspace.wait_settled(timeout=30)
    second = workspace.view().session.run_dir
    assert first is not None and second is not None
    assert first != second
    assert first.is_dir() and second.is_dir()


def test_select_case_after_round_done_opens_the_step(tmp_path: Path):
    """After the run ends the step is still on screen, and still read-only.

    The same round is still the one the engine last touched, so the case is read from
    the live session and not from `runs/` on disk: the session already knows which
    directory each step wrote, and re-globbing the run directory to learn the same
    thing would be a second answer to one question.
    """
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    workspace = WorkspaceSession(
        root=FIXTURES,
        config=_config(tmp_path),
        client=_http(),
        runs_dir=tmp_path / "runs",
        focus=WALK_HN,
    )
    workspace.start("round", "walk")
    workspace.wait_settled(timeout=30)
    workspace.apply_verdict("pass", "", True)
    workspace.wait_settled(timeout=30)
    workspace.apply_verdict("fail", "stop after negative", False)
    workspace.wait_settled(timeout=30)

    round_view = workspace.view()
    assert round_view.session.phase == "done"
    assert round_view.pane == "done"

    case_view = workspace.select(keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-H01"))
    assert case_view.pane == "review"
    assert case_view.session.awaiting_verdict is False
    assert case_view.session.current_step_dir is not None
    assert (case_view.session.current_step_dir / "request.json").is_file()
    assert case_view.session.current_step_dir.name.endswith("note-H01")

    later = workspace.select(keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-N-omit-note"))
    assert later.pane == "review"
    assert later.session.current_step_dir is not None
    assert later.session.current_step_dir.name.endswith("note-N-omit-note")

    back_to_round = workspace.select(keys.for_round(PROJECT, "rounds/walk-hn.yaml"))
    assert back_to_round.pane == "done"


def test_select_case_without_live_session_uses_disk_run(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    first = WorkspaceSession(
        root=FIXTURES,
        config=_config(tmp_path),
        client=_http(),
        runs_dir=runs_dir,
        focus=WALK_HN,
    )
    first.start("round", "walk")
    first.wait_settled(timeout=30)
    first.apply_verdict("fail", "stop after happy path", False)
    first.wait_settled(timeout=30)

    reopened = WorkspaceSession(
        root=FIXTURES,
        config=_config(tmp_path),
        client=_http(),
        runs_dir=runs_dir,
        focus=WALK_HN,
    )
    view = reopened.select(keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-H01"))
    assert view.pane == "historical"
    assert view.historical_dir is not None
    assert (view.historical_dir / "response.json").is_file()


def test_busy_round_blocks_starting_another(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    session = RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_http(),
        runs_dir=tmp_path / "runs",
    )
    workspace = WorkspaceSession.wrap(session)
    workspace.start("round", "walk")
    workspace.wait_settled(timeout=30)
    workspace.select(keys.for_round(PROJECT, "rounds/example.yaml"))
    with pytest.raises(HarnessError) as caught:
        workspace.start("round", "walk")
    assert caught.value.code == "ROUND_BUSY"


def test_a_suite_step_is_keyed_by_position_and_reads_distinctly(tmp_path: Path):
    """A suite's steps are rows of their own, told apart by where they are.

    A step's label is built from the case it runs, so a suite that runs one case twice
    gives two steps one label. Keyed by that label the two rows would be one key: the
    second would overwrite the first in the tree's own lookup, and the queue's case id
    could not say which of them a click meant. The label is read by a human, so the two
    rows also have to *read* differently — `loop chain ×2` twice is a tree nobody can
    navigate, which is the half of this a reviewer notices.
    """
    round_node = find_node(_workspace(tmp_path, focus=TWO_LOOPS).view().tree, TWO_LOOPS_KEY)
    assert [child.key for child in round_node.children] == [
        keys.for_step(PROJECT, "rounds/two-loops.yaml", 0),
        keys.for_step(PROJECT, "rounds/two-loops.yaml", 1),
        keys.for_step(PROJECT, "rounds/two-loops.yaml", 2),
    ]
    labels = [child.label for child in round_node.children]
    assert labels == ["loop chain-H01 ×2", "loop chain-H01-linked ×1", "probe two-loops"]
    assert len(set(labels)) == len(labels)
    assert all(child.step_kind for child in round_node.children)


def test_the_overlay_paints_one_row_per_queue_position():
    """Rows that read alike are still two rows, and the run is on one of them.

    The unit under the user's report: a suite of two identical loops used to paint both
    rows with the last verdict written and mark both as running, because the paint was
    keyed by the label the two rows share. Painted by position, the first row is the
    case on the wire and the second is untouched. The third row is not in the queue at
    all — a round filtered down to a subset runs fewer cases than its tree lists — and
    it keeps the status it was indexed with instead of borrowing a neighbour's.
    """
    rows = (
        TreeNode(
            key="step:0", kind="case", label="loop chain ×2",
            status="not_reviewed", case_id="loop chain ×2", step_kind="loop",
            project=PROJECT,
        ),
        TreeNode(
            key="step:1", kind="case", label="loop chain ×2",
            status="not_reviewed", case_id="loop chain ×2", step_kind="loop",
            project=PROJECT,
        ),
        TreeNode(
            key="step:2", kind="case", label="probe two-loops",
            status="not_reviewed", case_id="probe two-loops", step_kind="probe",
            project=PROJECT,
        ),
    )
    round_node = TreeNode(
        key="suite:two-loops",
        kind="round",
        label="two-loops",
        status="not_reviewed",
        children=rows,
    )
    queue = (
        QueueItem("loop chain ×2", "pass", current=True, reachable=True, live="running"),
        QueueItem("loop chain ×2", "pending", live="awaiting"),
    )
    painted, painted_at = _overlay_queue((round_node,), round_node.key, queue)
    assert [(child.status, child.live) for child in painted[0].children] == [
        ("pass", "running"),
        ("pending", "awaiting"),
        ("not_reviewed", ""),
    ]
    # Which row each position painted, which is what a click on the queue opens: the
    # third row is named by nothing because no position painted it.
    assert painted_at == {0: "step:0", 1: "step:1"}
    # The round's badge is re-derived from the rows and its mark from theirs: a
    # collapsed campaign still shows that the run is under it.
    assert painted[0].status == "pending"
    assert painted[0].live == "running"


def test_a_campaign_selection_reads_the_live_round(tmp_path: Path):
    """The roll-up a campaign is started from is the one that has to stay live.

    A campaign is selected to start it and to watch it, and its node is not the live
    round — the round under it is. Asked by identity alone the campaign got the
    *stored* session: `phase="start"`, an empty queue and progress 0/0, while a plan was
    running inside it. The roll-up's progress bar therefore drew nothing and its badges
    said whatever the last re-index had read, and the screen only told the truth once
    the reviewer clicked into a case and back — the "it does not update in real time"
    this replaces.
    """
    workspace = _workspace(tmp_path, focus=WALK_HN)
    workspace.select(EXAMPLE_CAMPAIGN)
    # Nothing is running yet, so the campaign is answered from disk: the stored view is
    # the honest one and it says so with an empty queue.
    assert workspace.view().session.queue == ()

    workspace.start("campaign", "walk")
    view = workspace.wait_settled(timeout=30)

    # The session is the live round's, whatever the pane does with it: a parked walk
    # puts the verdict form on screen, and the roll-up reads the same session.
    assert view.session.phase == "step"
    assert view.session.round_id == "example"
    assert [item.case_id for item in view.session.queue] == ["echo-H01"]
    assert view.session.progress == (1, 1)
    # And the row the run is on is the row a click opens, reached from the campaign.
    assert view.session.row_keys[0] == keys.for_case(
        PROJECT, "rounds/example.yaml", "echo-H01"
    )
    # Starting the campaign is the one selection where the roll-up is not what was
    # asked for: walk mode stopped to ask for a verdict, and that is what the screen
    # has to show.
    assert view.pane == "review"


def test_a_campaign_that_is_not_the_plan_still_answers_with_its_own_card(tmp_path: Path):
    """The jump back to a parked plan is offered, not imposed.

    The reverse of the test above, and the reason `_started_key` exists at all: a plan
    waiting on a verdict used to take the whole screen, so a campaign selected while it
    waited drew the parked step. A reviewer reading another campaign's roll-up is now
    answered with that roll-up, and the parked step is one click away on the strip.
    """
    workspace = _workspace(tmp_path, focus=WALK_HN)
    workspace.select(EXAMPLE_CAMPAIGN)
    workspace.start("campaign", "walk")
    workspace.wait_settled(timeout=30)

    other = keys.for_campaign(PROJECT, "interleaved")
    opened = workspace.select(other)

    assert opened.selected.key == other
    assert opened.pane == "campaign"
    # And the row the verdict is owed on is still named, from a screen that is not it.
    assert opened.pending_key == keys.for_case(PROJECT, "rounds/example.yaml", "echo-H01")


def test_only_the_live_round_and_its_ancestors_hold_it(tmp_path: Path):
    """Containment means "the live round is at or under this node", nothing looser.

    The tree is one tree over every project, so a rule that read "the project is live"
    without walking down would paint every sibling of the run as live. A sibling round
    is answered from disk; the round itself, its cases and the campaigns above it are
    the ones that read the live session.
    """
    workspace = _workspace(tmp_path, focus=WALK_HN)
    workspace.select(EXAMPLE_CAMPAIGN)
    workspace.start("campaign", "walk")
    workspace.wait_settled(timeout=30)
    tree = workspace.view().tree

    campaign = find_node(tree, EXAMPLE_CAMPAIGN)
    live_round = find_node(tree, keys.for_round(PROJECT, "rounds/example.yaml"))
    live_case = find_node(tree, keys.for_case(PROJECT, "rounds/example.yaml", "echo-H01"))
    sibling = find_node(tree, keys.for_round(PROJECT, "rounds/walk-hn.yaml"))
    project = tree[0]

    assert workspace._holds_live(project) is True
    assert workspace._holds_live(campaign) is True
    assert workspace._holds_live(live_round) is True
    assert workspace._holds_live(live_case) is True
    # A round the plan is not on, and the folders that do not contain it.
    assert workspace._holds_live(sibling) is False
    for node in tree:
        for child in node.children:
            if child.kind == "directory":
                assert workspace._holds_live(child) is False


def test_a_case_outside_the_parked_round_still_opens(tmp_path: Path):
    """A parked run is not a modal. Every other case has to stay reachable.

    A parked plan used to outrank the selection outright: clicking any node while a
    verdict was owed redrew the parked step, so the tree highlighted one case and the
    pane showed another. That is what "it will not let me open a case" means from the
    outside. The roll-up answers for the plan's campaign now, and a case that is not
    part of the live round is read from disk like any other.
    """
    workspace = _workspace(tmp_path, focus=WALK_HN)
    workspace.start("round", "walk")
    view = workspace.wait_settled(timeout=30)
    assert view.engine.awaiting, "the run must be parked to test this"
    assert view.pending_key, "a parked run names the row it is on"

    parked_key = view.pending_key
    other = keys.for_case(PROJECT, "rounds/chain-two.yaml", "chain-H01")
    opened = workspace.select(other)

    # The selection is what was clicked and the pane is not the parked step.
    assert opened.selected.key == other
    assert opened.pane != "review"
    assert opened.session.round_id == "chain-two"

    # And the parked step is still reachable: its key survives the detour.
    back = workspace.select(parked_key)
    assert back.selected.key == parked_key
    assert back.pane == "review"


def test_the_parked_row_is_named_from_its_position_not_what_is_on_screen(tmp_path: Path):
    """The jump-back target is the row the *run* is on, not the row being browsed.

    `pending_key` exists so a screen that is not the parked step can offer to return to
    it, and the two are different the moment a reviewer walks back to an earlier case
    while a verdict is owed — the pane shows one row, the run waits on another. Reading
    the target off the focus would send the reviewer to the case they are already
    looking at and leave the run parked, which is worse than not offering it.
    """
    workspace = _workspace(tmp_path, focus=WALK_HN)
    workspace.start("round", "walk")
    first = workspace.wait_settled(timeout=30)
    assert first.engine.awaiting
    # Step one is approved, so the run parks on the second case.
    workspace.apply_verdict("pass", "", True)
    parked = workspace.wait_settled(timeout=30)
    assert parked.engine.awaiting
    second_row = keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-N-omit-note")
    assert parked.pending_key == second_row

    # Browse back to the first case. The pane follows, the parked row does not.
    back = workspace.focus(0)
    assert back.session.focus_index == 0
    assert back.session.pending_index == 1
    assert back.pending_key == second_row
    assert back.pending_key != keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-H01")
