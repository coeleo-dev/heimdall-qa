"""`RunEngine`: the worker, the park, and the seam that keeps the suite honest.

Every test here waits with `wait_settled`, which blocks on the engine's own condition
variable. Nothing sleeps: a thread plus a sleep is a test that is either slow or
flaky, and the engine exists so that neither has to be true.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from heimdall_qa import keys
from heimdall_qa.collection import index_workspace
from heimdall_qa.engine import RunEngine
from heimdall_qa.errors import HarnessError
from heimdall_qa.plan import CAMPAIGN
from heimdall_qa.plan import CASE
from heimdall_qa.plan import CASE_FORWARD
from heimdall_qa.plan import ROUND
from heimdall_qa.plan import plan_for
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"

#: The id the tree is indexed under. Keys are built through `keys` rather than
#: spelled out, so this file never becomes a second implementer of the key format.
PROJECT = "fixtures"

WALK_HN = keys.for_round(PROJECT, "rounds/walk-hn.yaml")
CHAIN_LINKED = keys.for_case(PROJECT, "rounds/chain-two.yaml", "chain-H01-linked")
CHAIN = keys.for_case(PROJECT, "rounds/chain-two.yaml", "chain-H01")
WALK_NOTE = keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-H01")
MISSING_CAMPAIGN = keys.for_campaign(PROJECT, "missing-round")


def _project(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    return project_at(_DESCRIPTOR, web=str(web), worker=str(worker))


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)


def _accepted(request: httpx.Request) -> httpx.Response:
    return httpx.Response(202, json={"status": "ACCEPTED"}, headers={"X-Trace-Id": "t"})


def _engine(tmp_path: Path, handler=_accepted) -> RunEngine:
    config = config_for(_project(tmp_path))
    return RunEngine(
        root=FIXTURES,
        config=config,
        client=_client(handler),
        runs_dir=tmp_path / "runs",
    )


def _plan(tmp_path: Path, key: str, scope: str):
    config = config_for(_project(tmp_path))
    tree = index_workspace(
        FIXTURES, tmp_path / "runs", config.project, project_id=PROJECT
    )
    return plan_for(tree, key, scope, root=FIXTURES, project=config.project)


def _approve_until_done(engine: RunEngine) -> None:
    """Walk a plan to its end, one verdict at a time, without sleeping."""
    while True:
        view = engine.wait_settled(timeout=30)
        if not view.awaiting:
            return
        engine.submit_verdict("pass", "", True)


def test_start_does_not_wait_for_the_first_request(tmp_path: Path):
    """The property the whole module exists for: `start` answers while it runs.

    The handler parks on an event, so the assertion is deterministic rather than a
    race: if `start` were waiting for the run, it could not return while the first
    request is still in flight.
    """
    in_flight = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        in_flight.set()
        assert release.wait(timeout=10)
        return _accepted(request)

    engine = _engine(tmp_path, handler=handler)
    view = engine.start(_plan(tmp_path, WALK_HN, ROUND), "walk")

    assert in_flight.wait(timeout=10)
    assert view.phase == "running"
    assert view.unit_total == 1
    assert view.unit_index == 1

    release.set()
    settled = engine.wait_settled(timeout=30)
    assert settled.awaiting is True
    # An orphan round has no campaign endpoint to borrow, so it is named by its own id
    # rather than by an endpoint nobody declared.
    assert settled.unit_label == "walk-hn"
    assert settled.case_id == "note-H01"
    assert settled.step_index == 1


def test_walk_parks_at_every_case_and_counts_the_verdicts(tmp_path: Path):
    engine = _engine(tmp_path)
    engine.start(_plan(tmp_path, WALK_HN, ROUND), "walk")

    first = engine.wait_settled(timeout=30)
    assert (first.step_index, first.step_total) == (1, 2)
    assert first.case_id == "note-H01"

    engine.submit_verdict("pass", "", True)
    second = engine.wait_settled(timeout=30)
    assert (second.step_index, second.step_total) == (2, 2)
    assert second.case_id == "note-N-omit-note"

    engine.submit_verdict("fail", "the 400 came back as 500", False)
    done = engine.wait_settled(timeout=30)

    assert done.phase == "done"
    assert done.awaiting is False
    assert done.counts == {"pass": 1, "fail": 1, "skip": 0}
    assert done.cases_done == 2
    assert done.elapsed_ms >= 0
    assert done.run_dir is not None and done.run_dir.is_dir()


def test_the_feed_names_the_plan_and_every_case(tmp_path: Path):
    """Counts going up is not feedback; a campaign is 41 rounds and hundreds of cases."""
    engine = _engine(tmp_path)
    engine.start(_plan(tmp_path, WALK_HN, ROUND), "walk")
    _approve_until_done(engine)

    texts = [event.text for event in engine.snapshot().events]
    assert any("2 unidade(s)" in text or "1 unidade(s)" in text for text in texts)
    assert "note-H01 — pass" in texts
    assert "note-N-omit-note — pass" in texts
    statuses = {event.status for event in engine.snapshot().events}
    assert "pass" in statuses


def test_a_review_run_publishes_each_case_while_the_round_is_still_running(tmp_path: Path):
    """The screen has to move during a long automatic stretch, not after it.

    `review` resolves case after case inside the session's own loop, so the engine
    used to publish twice for a whole round — once at the start and once at the end.
    Everything in between was a frozen window, which is a reviewer's "not real time".
    Here the second request parks, so the state between the first case landing and the
    second one finishing is a state this test can actually look at.
    """
    web_log = tmp_path / "web.log"
    second_in_flight = threading.Event()
    release = threading.Event()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "missing")
        body = json.loads(request.content.decode("utf-8") or "{}")
        requests.append(request)
        if len(requests) >= 2:
            second_in_flight.set()
            assert release.wait(timeout=10)
        if "note" in body:
            # The passing case needs the log, or the observability pack fails it and
            # `review` parks on the first case instead of auto-advancing past it.
            with web_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    "2026-09-01 16:00:00 [vt] INFO  c.n.Qa [SANDBOX] - "
                    f"trace_id: [{trace}] - accepted\n"
                )
            return httpx.Response(
                202,
                json={"status": "ACCEPTED"},
                headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
            )
        return httpx.Response(
            400,
            json={"error": "note is required", "traceId": trace},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    engine = _engine(tmp_path, handler=handler)
    engine.start(_plan(tmp_path, WALK_HN, ROUND), "review")

    assert second_in_flight.wait(timeout=10)

    # The first case has landed and the second is on the wire. The counter has already
    # moved, and the feed already names the verdict — both of which read `0/2` and
    # empty before the session reported its boundaries.
    mid = engine.snapshot()
    assert mid.phase == "running"
    assert mid.awaiting is False
    assert (mid.step_index, mid.step_total) == (1, 2)
    assert mid.cases_done == 1
    assert "note-H01 — pass" in [event.text for event in mid.events]

    release.set()
    settled = engine.wait_settled(timeout=30)

    # The second case fails, so `review` parks there: the run is over for the engine
    # but its verdict is a human's to write, which is the same round one case later.
    assert settled.awaiting is True
    assert settled.case_id == "note-N-omit-note"
    assert settled.cases_done == 1


def test_a_second_plan_while_one_runs_is_refused(tmp_path: Path):
    engine = _engine(tmp_path)
    plan = _plan(tmp_path, WALK_HN, ROUND)
    engine.start(plan, "walk")
    engine.wait_settled(timeout=30)

    with pytest.raises(HarnessError) as caught:
        engine.start(plan, "walk")

    assert caught.value.code == "ROUND_BUSY"


def test_the_clock_stops_when_the_plan_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A finished run reports how long it took, not how long ago it started.

    The number keeps its meaning only if it stops: the strip reads a climbing clock as
    "still going", and the campaign roll-up behind it keeps the live card — with the
    Start buttons replaced by "a plan is already running here" — drawn on top of a plan
    that ended. Every frame after the run was a bigger lie than the last.
    """
    clock = {"now": 100.0}
    monkeypatch.setattr("heimdall_qa.engine.time.perf_counter", lambda: clock["now"])

    engine = _engine(tmp_path)
    engine.start(_plan(tmp_path, WALK_HN, ROUND), "walk")
    assert engine.snapshot().elapsed_ms == 0

    clock["now"] = 130.0
    _approve_until_done(engine)
    done = engine.wait_settled(timeout=30)

    assert done.phase == "done"
    assert done.elapsed_ms == 30_000

    # Ten minutes later, with the client still asking, the answer is the same one.
    clock["now"] = 700.0
    assert engine.snapshot().elapsed_ms == 30_000


def test_cancel_while_awaiting_keeps_what_already_ran(tmp_path: Path):
    """A cancelled plan is a run somebody can still review, not a lost one."""
    engine = _engine(tmp_path)
    engine.start(_plan(tmp_path, WALK_HN, ROUND), "walk")
    engine.wait_settled(timeout=30)

    engine.cancel()
    view = engine.wait_settled(timeout=30)

    assert view.phase == "cancelled"
    assert view.awaiting is False
    assert view.counts == {"pass": 0, "fail": 0, "skip": 0}
    assert view.run_dir is not None
    assert (view.run_dir / "summary.json").is_file()
    assert (view.run_dir / "evidence.md").is_file()


def test_a_skipped_unit_is_announced_and_never_started(tmp_path: Path):
    """The hole is in the feed before the first request goes out."""
    engine = _engine(tmp_path)
    engine.start(_plan(tmp_path, MISSING_CAMPAIGN, CAMPAIGN), "walk")
    view = engine.wait_settled(timeout=30)

    assert view.phase == "done"
    assert view.unit_skips == ("POST /qa/echo: round file is missing",)
    assert engine.session_view() is None
    assert view.run_dir is None


def test_a_plan_whose_only_unit_cannot_start_ends_in_error(tmp_path: Path):
    """The chain's second case spends a capture the first one mints.

    Run alone it cannot resolve its own body, so its unit never starts. One unit
    failing out of forty is a line in the feed; when that is the whole plan, `done`
    would be a lie and the phase has to say so.
    """
    engine = _engine(tmp_path)
    plan = _plan(tmp_path, CHAIN_LINKED, CASE)
    engine.start(plan, "walk")
    view = engine.wait_settled(timeout=30)

    assert view.phase == "error"
    assert view.error is not None
    assert view.error["code"] == "CAPTURE_MISSING"
    assert any("chain-H01-linked" in event.text for event in view.events)


def test_a_verdict_with_no_step_waiting_is_refused(tmp_path: Path):
    engine = _engine(tmp_path)

    with pytest.raises(HarnessError) as caught:
        engine.submit_verdict("pass", "", True)

    assert caught.value.code == "VERDICT_INVALID"


def test_a_rejected_verdict_is_rejected_on_the_calling_thread(tmp_path: Path):
    """Reprove without a comment must fail where the comment box still exists.

    If this were validated in the worker, the error would be raised after the browser
    had been redirected and the comment the reviewer wrote would be gone with it.
    """
    engine = _engine(tmp_path)
    engine.start(_plan(tmp_path, WALK_HN, ROUND), "walk")
    engine.wait_settled(timeout=30)

    with pytest.raises(HarnessError) as caught:
        engine.submit_verdict("fail", "", True)

    assert caught.value.code == "VERDICT_INVALID"
    assert engine.snapshot().awaiting is True


def test_focus_moves_the_step_under_review(tmp_path: Path):
    """Focus never points at a step that has not run: it is clamped to what happened."""
    engine = _engine(tmp_path)
    engine.start(_plan(tmp_path, WALK_HN, ROUND), "walk")
    engine.wait_settled(timeout=30)

    # Only the first step exists yet, so a request for the second is clamped back.
    engine.focus(1)
    assert engine.session_view() is not None
    assert engine.session_view().focus_index == 0

    engine.submit_verdict("pass", "", True)
    second = engine.wait_settled(timeout=30)
    assert second.case_id == "note-N-omit-note"

    engine.focus(0)
    assert engine.snapshot().case_id == "note-H01"

    engine.focus(1)
    assert engine.snapshot().case_id == "note-N-omit-note"


def test_wait_settled_returns_immediately_when_nothing_is_running(tmp_path: Path):
    """The seam has to be safe on an idle engine, or every test pays a timeout."""
    engine = _engine(tmp_path)

    view = engine.wait_settled(timeout=0.01)

    assert view.phase == "idle"
    assert view.unit_total == 0
    assert view.events == ()


def test_the_run_directory_is_named_for_the_selection(tmp_path: Path):
    """A partial run announces itself in the name, before anyone reads a summary."""
    alone = _engine(tmp_path)
    alone.start(
        _plan(tmp_path, WALK_NOTE, CASE),
        "walk",
    )
    single = alone.wait_settled(timeout=30)

    assert single.run_dir is not None
    assert single.run_dir.name.endswith("~case-note-H01")

    forward = _engine(tmp_path)
    forward.start(
        _plan(tmp_path, CHAIN, CASE_FORWARD),
        "walk",
    )
    _approve_until_done(forward)
    whole = forward.wait_settled(timeout=30)

    assert whole.run_dir is not None
    assert whole.run_dir.name.endswith("~from-chain-H01")
