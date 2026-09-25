"""The review surface, driven the way the client drives it.

This file used to read the Jinja page's HTML and assert on its copy — `"Aprovar" in
page.text`, `'<section class="detail">' in page.text`. Phase 6 deleted the page, so the
same journeys are now taken over `POST /api/*` and read off the contract models. What
was worth keeping is the *behaviour*, and every one of those asserts had one behind it:
the request answers before the run finishes, a planted credential never reaches the
screen, an empty log source says why it is empty, a reviewed case can be rerun from its
own node, and a finished run opens read-only.

The models are the assertion, not JSON strings: `BootstrapModel.model_validate` is what
turns "the screen shows something" into "the screen shows something the client was
promised". `tests/test_serve_api.py` covers the surface itself — its token, its origin,
its stream — and this file covers what the surface says about a run.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from heimdall_qa import keys
from heimdall_qa.projects import id_for
from heimdall_qa.serve.app import create_app
from heimdall_qa.serve.models import BootstrapModel
from heimdall_qa.session import RoundSession
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at
from heimdall_qa.testing import stub_client

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WALK_HN = FIXTURES / "rounds" / "walk-hn.yaml"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"

#: The id the workspace derives from the root it is handed, so a key built here and
#: a key off the tree are the same string.
PROJECT = id_for(FIXTURES)
NOTE_H01 = keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-H01")
WALK_HN_KEY = keys.for_round(PROJECT, "rounds/walk-hn.yaml")
ORDER_FIRST_KEY = keys.for_round(PROJECT, "rounds/order-first.yaml")
INTERLEAVED = keys.for_campaign(PROJECT, "interleaved")


def _handler(web_log: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "missing")
        body = json.loads(request.content.decode("utf-8") or "{}")
        if "note" in body:
            with web_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    "2026-09-01 16:00:00 [vt] INFO  c.n.Qa [SANDBOX] - "
                    f"trace_id: [{trace}] - accepted\n"
                )
            #: The API answers with a credential it should not echo, so that the
            #: screen has something to prove it redacted. It is planted here and not
            #: in a committed file on purpose: `validate` refuses a credential in the
            #: round's own content, and this fixture has to stay valid.
            return httpx.Response(
                202,
                json={"status": "ACCEPTED", "api_key": "test_key_plantedkey"},
                headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
            )
        return httpx.Response(
            400,
            json={"error": "note is required", "traceId": trace},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    return handler


def _client(tmp_path: Path) -> TestClient:
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    http = httpx.Client(
        transport=httpx.MockTransport(_handler(web_log)),
        timeout=10.0,
    )
    session = RoundSession(
        WALK_HN,
        root=FIXTURES,
        config=config_for(
            project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))
        ),
        client=http,
        runs_dir=tmp_path / "runs",
    )
    return TestClient(
        create_app(session=session, api_token="t", webapp=stub_client(tmp_path)),
        follow_redirects=False,
    )


def _headers() -> dict[str, str]:
    return {"X-Heimdall-Token": "t"}


def _post(client: TestClient, url: str, **body) -> httpx.Response:
    """Post, then wait for the engine instead of assuming the request ran the run.

    `POST /api/start` and `POST /api/verdict` answer as soon as the plan is handed over
    — that is the change this suite guards, and it is what makes a campaign, or a probe
    that waits thirty seconds, not hold the connection. What the screen shows is decided
    by the engine afterwards, so a test waits for it to settle rather than racing a
    worker thread and hoping it won.
    """
    response = client.post(url, json=body, headers=_headers())
    client.app.state.workspace.wait_settled(timeout=30)
    return response


def _screen(client: TestClient) -> BootstrapModel:
    response = client.get("/api/bootstrap", headers=_headers())
    assert response.status_code == 200, response.text
    return BootstrapModel.model_validate(response.json())


def test_openapi_and_docs_are_disabled(tmp_path: Path):
    client = _client(tmp_path)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_verdict_reject_without_comment_is_refused_and_keeps_the_step(tmp_path: Path):
    """The one refusal the form must not clear: a reprove owes a comment."""
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    response = client.post(
        "/api/verdict",
        json={"status": "fail", "comment": "", "continue_round": True},
        headers=_headers(),
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VERDICT_INVALID"
    assert body["comment_required"] is True

    # The step is still the one on screen, and it is the one a verdict applies to.
    # (`comment_required` on the refusal is what the form reads to keep the comment;
    # there is no second flag on the step, because two answers to one question is how
    # the form and the server start disagreeing.)
    screen = _screen(client)
    assert screen.step is not None
    assert screen.step.awaiting_verdict


def test_verdict_reject_with_comment_moves_to_the_next_case(tmp_path: Path):
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    response = _post(
        client,
        "/api/verdict",
        status="fail",
        comment="envelope ok but the copy is a Java class name",
        continue_round=True,
    )
    assert response.status_code == 200
    assert _screen(client).step.case_label == "note-N-omit-note"


def test_verdict_stop_finishes_the_run_with_its_aggregate(tmp_path: Path):
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    _post(
        client,
        "/api/verdict",
        status="fail_stop",
        comment="stopping after the happy path",
        continue_round=True,
    )
    screen = _screen(client)
    assert screen.pane == "done"
    assert screen.run is not None
    assert "counts" in screen.run.summary
    # The path the reviewer can open, and the pointer that makes it findable.
    assert "runs" in screen.run.run_path
    assert (tmp_path / "runs" / "latest").exists()


def test_the_step_body_does_not_leak_a_planted_credential(tmp_path: Path):
    """The harness redacts before it writes, so the screen cannot leak what it never got."""
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    screen = _screen(client)
    assert screen.step is not None
    assert "test_key_plantedkey" not in json.dumps(screen.step.model_dump())
    # The step is still the one we asked for: redaction must not blank the panel.
    assert screen.step.case_label == "note-H01"
    assert screen.step.has_http


def test_every_declared_log_source_arrives_with_why_it_is_empty(tmp_path: Path):
    """The screen has to say which source owes a line, not render a blank panel.

    A reader who sees nothing cannot tell "the file is not there" from "the project
    declared the trace does not reach it" — and those two lead to opposite conclusions
    about the product.
    """
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    step = _screen(client).step
    assert step is not None

    assert len(step.logs_sources) == 3
    by_id = {str(source.get("id")): source for source in step.logs_sources}
    assert "trace_id: [" in str(by_id["web"]["text"])
    # The worker owes a line — the endpoint is async and the call was accepted — and
    # has none, and says so.
    assert str(by_id["worker"].get("reason") or by_id["worker"].get("text")) != ""
    # The third source is declared and not instrumented, which is a different fact.
    assert by_id["admin"].get("propagate") is False


def test_the_idle_screen_names_the_environment_and_offers_the_round(tmp_path: Path):
    client = _client(tmp_path)
    screen = _screen(client)
    assert screen.session.environment == "sandbox"
    # The unit card offers what the selection supports, and for a round that is the
    # round itself. A button that ran something else would be a plan nobody asked for,
    # which is why the table that decides is one table.
    assert screen.unit.scopes == ["round"]
    assert screen.unit.scope_labels["round"] == "Rodar este endpoint"
    assert screen.labels.status_label["pending"] == "Pendente"


def test_a_reviewed_step_can_be_revisited_and_then_returned_from(tmp_path: Path):
    """Walking back in a live round shows the old step and takes the verdict away.

    The verdict belongs to the case that is *waiting*, so a step the reviewer scrolled
    back to must not offer the buttons — approving from there would record a decision
    against a case nobody was looking at.
    """
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    assert _screen(client).step.awaiting_verdict

    _post(client, "/api/verdict", status="pass", comment="", continue_round=True)
    live = _screen(client)
    assert live.step.case_label == "note-N-omit-note"
    assert live.step.awaiting_verdict
    assert live.session.can_prev

    _post(client, "/api/focus", index=0)
    back = _screen(client)
    assert "note-H01" in back.step.case_label
    assert back.step.awaiting_verdict is False, "a step walked back to is not awaiting"
    assert back.step.recorded_verdict.get("status") == "pass"
    # The session's own flag is where that rule lives: a verdict is pending, but not on
    # the step on screen. That is what lets the client offer "go to the current step"
    # instead of a pair of buttons that would decide for a case nobody is looking at.
    assert back.session.awaiting_verdict is False
    assert back.session.pending_index == 1
    assert back.session.focus_index == 0

    _post(client, "/api/focus", index=1)
    returned = _screen(client)
    assert returned.step.case_label == "note-N-omit-note"
    assert returned.step.awaiting_verdict
    assert returned.session.awaiting_verdict


def test_a_reviewed_case_is_rerun_from_its_own_node(tmp_path: Path):
    """A case's scopes live where the case is, because it has no card of its own.

    Selecting a case that has been reviewed reopens its step — that is what the tree is
    for — so a red case inside a green round would have no way to be run on its own if
    the buttons only existed for a round.
    """
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    _post(client, "/api/verdict", status="pass", comment="", continue_round=True)
    _post(client, "/api/verdict", status="pass", comment="", continue_round=True)

    case_key = NOTE_H01
    _post(client, "/api/select", key=case_key)
    screen = _screen(client)
    assert screen.unit.scopes == ["case", "case_forward"]
    assert screen.unit.scope_labels["case"] == "Rodar só este caso"
    assert screen.unit.scope_labels["case_forward"] == "Rodar deste caso em diante"

    _post(client, "/api/start", mode="review", scope="case", node=case_key)
    run_dir = client.app.state.workspace.view().engine.run_dir
    assert run_dir is not None
    assert run_dir.name.endswith("~case-note-H01")
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["selection"] == "case-note-H01"


def test_a_case_clicked_after_the_run_is_over_opens_it_read_only(tmp_path: Path):
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    _post(
        client,
        "/api/verdict",
        status="fail_stop",
        comment="stopping after the happy path",
        continue_round=True,
    )
    assert _screen(client).pane == "done"

    _post(client, "/api/select", key=NOTE_H01)
    screen = _screen(client)
    # The step is reopened for reading — that is what clicking a case in the tree is
    # for — and nothing about it is startable or decidable any more.
    assert screen.pane in {"review", "historical"}
    assert screen.step is not None
    # The label is the step directory's own name when the step belongs to a run that is
    # over: there is no live queue to ask, and `001-note-H01` is the honest answer.
    assert "note-H01" in screen.step.case_label
    assert screen.step.has_http
    assert screen.step.recorded_verdict, "a reviewed step carries the verdict it got"
    assert screen.step.awaiting_verdict is False
    # Read-only means the *verdict* is gone, not that the step is dead: a case's own
    # scopes survive the end of the run so a red case can be run again from where it is,
    # which is the same affordance `test_a_reviewed_case_is_rerun_from_its_own_node`
    # guards on a live round.
    assert screen.unit.scopes == ["case", "case_forward"]


def test_the_done_screen_carries_the_kpis_and_the_run_it_read(tmp_path: Path):
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    _post(
        client,
        "/api/verdict",
        status="fail",
        comment="stopping after the happy path",
        continue_round=False,
    )
    screen = _screen(client)
    assert screen.pane == "done"
    assert screen.run is not None
    assert "round_id" in screen.run.summary
    assert screen.run.run_path
    assert (tmp_path / "runs" / "latest").exists()
    # `walk` stops at the first case, and this verdict says "stop", so exactly one case
    # decided and the other never ran — which is what the aggregate has to say.
    assert len(screen.session.queue) == 2
    assert [row["case_id"] for row in screen.run.failed_cases] == ["note-H01"]
    assert screen.run.summary["counts"]["fail"] == 1


def test_a_round_the_engine_moved_off_still_opens_on_its_kpis(tmp_path: Path):
    """The KPI pane follows the disk, not the engine's memory of the last unit.

    A campaign of forty rounds used to be able to show exactly one round's numbers —
    the one the engine finished last, and only until another plan started. Selecting a
    round back after a detour showed the start card again, so the summary a reviewer
    had just produced became unreachable. The run is on disk and the tree indexed it,
    so the pane is the same one whether or not the engine still holds the round.
    """
    client = _client(tmp_path)
    _post(client, "/api/start", scope="round", node=ORDER_FIRST_KEY, mode="review")
    _post(client, "/api/verdict", status="pass", comment="", continue_round=True)

    # Another plan takes the engine off it, so the round is history and not the plan.
    _post(client, "/api/start", scope="round", node=WALK_HN_KEY, mode="walk")
    live = client.app.state.workspace.view().engine.unit_round
    assert live == "rounds/walk-hn.yaml", "the detour must be a different round"

    _post(client, "/api/select", key=ORDER_FIRST_KEY)
    screen = _screen(client)
    assert screen.pane == "done"
    assert screen.run is not None
    assert screen.run.unit_kind == "round"
    assert screen.run.unit_key == ORDER_FIRST_KEY
    assert screen.run.summary["counts"]["pass"] == 1
    assert screen.run.run_path.endswith("order-first")
    # The live walk is parked on its own round's step, and it did not take the pane.
    assert screen.step is None


def test_a_campaign_roll_up_reads_one_row_per_endpoint_from_history(tmp_path: Path):
    """A campaign gets a table, and the totals are only the numbers that add up.

    Every row is a round's latest run off disk, so a campaign answers with the state of
    each endpoint even when the engine is somewhere else entirely. Latency is
    deliberately *not* in the totals: a p95 over a campaign is not the average of forty
    p95s, and a harness that printed one would be inventing a number it cannot weight.
    """
    client = _client(tmp_path)
    _post(client, "/api/start", scope="round", node=ORDER_FIRST_KEY, mode="review")
    _post(client, "/api/verdict", status="pass", comment="", continue_round=True)

    _post(client, "/api/select", key=INTERLEAVED)
    screen = _screen(client)
    assert screen.pane == "campaign"
    assert screen.rollup is not None
    # One row per round the campaign holds, in the tree's own order (the matrices
    # group the two A4 rounds together, which is what the pane above the table shows).
    assert screen.rollup.rounds_total == 3
    assert [row.round_id for row in screen.rollup.units] == [
        "order-first",
        "order-third",
        "order-second",
    ]
    # The round that ran carries its own evidence, per row.
    ran = [row for row in screen.rollup.units if row.found]
    assert [row.round_id for row in ran] == ["order-first"]
    assert ran[0].counts["pass"] == 1
    assert ran[0].latency_ms, "the run that happened keeps its own latency"
    assert ran[0].run_path
    assert ran[0].stamp == ran[0].run_path.rsplit("/", 1)[-1]
    # The additive totals only.
    assert screen.rollup.rounds_run == 1
    assert screen.rollup.totals["pass"] == 1
    assert screen.rollup.totals["not_run"] == 2
    for invented in ("p50", "p95", "coverage_pct"):
        assert invented not in screen.rollup.totals


def test_a_round_reopened_from_history_lists_the_cases_worth_reopening(tmp_path: Path):
    """The end-of-run list survives the engine letting go of the run.

    `failed_cases` has two sources: the live queue, which knows which row a step
    painted, and the run directory, for every round the engine no longer holds. The
    second is the one this guards, and the key it resolves is the round's own case
    row — not the step directory's file name.
    """
    client = _client(tmp_path)
    _post(client, "/api/start", mode="walk")
    _post(client, "/api/verdict", status="pass", comment="", continue_round=True)
    _post(
        client,
        "/api/verdict",
        status="fail",
        comment="the copy is a Java class name",
        continue_round=False,
    )

    # Move the engine to another round so `walk-hn` is history and not the live plan.
    _post(client, "/api/start", scope="round", node=ORDER_FIRST_KEY, mode="review")
    _post(client, "/api/select", key=WALK_HN_KEY)
    screen = _screen(client)

    assert screen.pane == "done"
    assert screen.run is not None
    assert [row["case_id"] for row in screen.run.failed_cases] == ["note-N-omit-note"]
    row = screen.run.failed_cases[0]
    assert row["key"] == keys.for_case(PROJECT, "rounds/walk-hn.yaml", "note-N-omit-note")
    assert row["reason"]
    assert row["step_dir"]
