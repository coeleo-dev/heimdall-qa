"""The workspace's view model, read the way the client reads it.

These tests used to look at the Jinja page — `<div class="pane-scroll">`, the collapsed
branches in the polled tree fragment, `error[ROUND_INVALID]` in an HTML form. The page is
gone (phase 6), so what is asserted here is the same view model through `/api/bootstrap`,
which is at least as good a guard: the model is what the client draws from, and the two
things these tests were really about — a tree that ships its own layout, and a suite step
that offers a different button from a case — are properties of the model and not of its
markup.

The scrolling test that used to live here is deleted rather than translated: `.pane-scroll`
was a CSS class, and the client now owns its own layout. `test_serve_assets.py` went with
it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from heimdall_qa import keys
from heimdall_qa.projects import id_for
from heimdall_qa.serve.app import create_app
from heimdall_qa.serve.models import BootstrapModel
from heimdall_qa.serve.models import TreeNodeModel
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at
from heimdall_qa.testing import stub_client
from heimdall_qa.workspace import WorkspaceSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"
H01_ONLY = FIXTURES / "rounds" / "h01-only.yaml"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"

#: The id `WorkspaceSession` derives from the root it is handed. A key built here
#: and a key off the tree agree only if the derivation is the same one.
PROJECT = id_for(FIXTURES)


def _workspace(tmp_path: Path, *, focus: Path | None = None) -> WorkspaceSession:
    return WorkspaceSession(
        root=FIXTURES,
        config=config_for(
            project_at(
                _DESCRIPTOR,
                web=str(tmp_path / "web.log"),
                worker=str(tmp_path / "worker.log"),
            )
        ),
        client=httpx.Client(timeout=10.0),
        runs_dir=tmp_path / "runs",
        focus=focus,
    )


def _client(tmp_path: Path, *, focus: Path | None = None) -> TestClient:
    return TestClient(
        create_app(
            workspace=_workspace(tmp_path, focus=focus),
            api_token="t",
            webapp=stub_client(tmp_path),
        ),
        follow_redirects=False,
    )


def _headers() -> dict[str, str]:
    return {"X-Heimdall-Token": "t"}


def _screen(client: TestClient) -> BootstrapModel:
    response = client.get("/api/bootstrap", headers=_headers())
    assert response.status_code == 200, response.text
    return BootstrapModel.model_validate(response.json())


def _flatten(nodes: list[TreeNodeModel]) -> list[TreeNodeModel]:
    out: list[TreeNodeModel] = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten(node.children))
    return out


def test_the_collection_arrives_with_its_campaign_and_its_rounds(tmp_path: Path):
    screen = _screen(_client(tmp_path))
    seen = {node.key for node in _flatten(screen.tree)}
    assert keys.for_campaign(PROJECT, "example-campaign") in seen
    assert keys.for_round(PROJECT, "rounds/example.yaml") in seen
    # A tree node carries the word the harness uses for its kind, which is what the
    # sidebar and the unit card draw.
    assert screen.labels.kind_label["campaign"] == "Campanha"
    assert screen.labels.kind_label["folder"] == "Fluxo"


def test_the_tree_ships_its_own_layout(tmp_path: Path):
    """The branches the reader did not open arrive closed, decided by the server.

    The client re-applies the reader's own open/closed choices on top of what it is
    handed; everything it is not told about has to arrive already decided. A tree that
    arrived fully expanded would be nine hundred rows the first time a campaign of
    seventy rounds was open, and the client would have no way to know that was wrong.
    """
    screen = _screen(_client(tmp_path))
    nodes = _flatten(screen.tree)
    assert len(nodes) > 5, "the fixture collection is bigger than one open path"
    assert any(not node.expanded for node in nodes), "the whole collection arrives open"
    # And the path to what is selected is open, or the selection would be invisible in
    # the sidebar on the first paint — the client has no way to guess it.
    by_key = {node.key: node for node in nodes}
    assert screen.selected in by_key, "the selection is not in the tree"
    assert by_key[screen.selected].expanded, "the selected node arrives collapsed"


def test_a_round_that_is_not_ready_is_refused_by_name(tmp_path: Path):
    """`ROUND_INVALID`, and the reason the round is not ready.

    The round in the fixture has a coverage gap `validate` can name, so the plan is
    refused before anything starts. `ROUND_BUSY` would send the reader to wait for a run
    that is never going to happen, and the reason travels with the refusal so the screen
    can say which gap stopped it.
    """
    client = _client(tmp_path, focus=H01_ONLY)
    screen = _screen(client)
    # The card offers the button even so, and says why pressing it will not work: the
    # alternative is a tree that looks like it has nothing to say about this round.
    assert screen.unit.scopes == ["round"]
    assert screen.unit.startable is False
    assert "COVERAGE_GAP" in str(screen.unit.reason)

    response = client.post(
        "/api/start",
        json={"scope": "", "mode": "walk", "node": None},
        headers=_headers(),
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "ROUND_INVALID"
    assert "COVERAGE_GAP" in body["error"]["message"]


def test_a_suite_loop_offers_a_run_and_a_probe_does_not(tmp_path: Path):
    """A step of a suite is not a case, and the card has to admit that in words.

    `loop chain-H01 ×2` can start a run — the loop and the conference behind it — so it gets
    "desta etapa em diante" beside the round. `probe two-loops` cannot: alone it would
    photograph the world and read it straight back, so its card offers the round and says
    why, instead of a button the runner would have to refuse.
    """
    client = _client(tmp_path)

    loop = client.post(
        "/api/select",
        json={"key": keys.for_step(PROJECT, "rounds/two-loops.yaml", 0)},
        headers=_headers(),
    )
    assert loop.status_code == 200, loop.text
    loop_screen = BootstrapModel.model_validate(loop.json())
    assert loop_screen.unit.step_kind == "loop"
    assert loop_screen.labels.step_kind_label["loop"] == "Laço"
    assert loop_screen.unit.scopes == ["case_forward", "round"]
    assert loop_screen.unit.scope_labels["case_forward"] == "Rodar desta etapa em diante"
    assert loop_screen.unit.scope_labels["round"] == "Rodar este endpoint"
    assert "contagem, não um caso" in loop_screen.unit.step_note

    probe = client.post(
        "/api/select",
        json={"key": keys.for_step(PROJECT, "rounds/two-loops.yaml", 2)},
        headers=_headers(),
    )
    assert probe.status_code == 200, probe.text
    probe_screen = BootstrapModel.model_validate(probe.json())
    assert probe_screen.unit.step_kind == "probe"
    assert probe_screen.labels.step_kind_label["probe"] == "Conferência"
    # The round and nothing else: a probe alone would measure nothing.
    assert probe_screen.unit.scopes == ["round"]
    assert "fotografaria o mundo" in probe_screen.unit.step_note
