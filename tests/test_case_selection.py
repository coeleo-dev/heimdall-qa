"""Running a slice of a round, and why the slice cannot pass for the round.

`only_case` and `from_case` are the two scopes a case offers, and the reason they are
two and not one is the capture: `chain-H01-linked` reads an id that `chain-H01` mints
from its own response, which is the shape of every authentication chain in miniature.
The other half of the file is the accounting — a partial run writes a differently
named directory and a `selection` in its summary, so nothing downstream can mistake
its counts for the round's verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from heimdall_qa import keys
from heimdall_qa.campaign import find_latest_run
from heimdall_qa.collection import index_workspace
from heimdall_qa.engine import RunEngine
from heimdall_qa.errors import HarnessError
from heimdall_qa.plan import CASE
from heimdall_qa.plan import plan_for
from heimdall_qa.session import RoundSession
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"
CHAIN_ROUND = FIXTURES / "rounds" / "chain-two.yaml"
CHAIN_REL = "rounds/chain-two.yaml"

#: The id these tests index under; the key and the index have to agree.
PROJECT = "fixtures"
CHAIN_H01 = keys.for_case(PROJECT, CHAIN_REL, "chain-H01")


def _project(tmp_path: Path):
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    return project_at(_DESCRIPTOR, web=str(web), worker=str(worker))


def _config(tmp_path: Path):
    return config_for(_project(tmp_path))


def _client() -> httpx.Client:
    """An API that mints an id per request and echoes nothing else.

    The id is what the chain captures, so it has to differ per call: a fixed value
    would let a test pass while the capture was never spent.
    """
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(
            202,
            json={"status": "ACCEPTED", "id": f"chain-{counter['calls']:03d}"},
            headers={"X-Trace-Id": "t"},
        )

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)


def _session(tmp_path: Path, **kwargs) -> RoundSession:
    return RoundSession(
        CHAIN_ROUND,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_client(),
        runs_dir=tmp_path / "runs",
        **kwargs,
    )


def _session_with_round(tmp_path: Path, round_path: Path, **kwargs) -> RoundSession:
    return RoundSession(
        round_path,
        root=FIXTURES,
        config=_config(tmp_path),
        client=_client(),
        runs_dir=tmp_path / "runs",
        **kwargs,
    )


def _walk_to_the_end(session: RoundSession) -> None:
    view = session.start("walk")
    while view.phase == "step":
        view = session.apply_verdict("pass", "", True)


def _steps(session: RoundSession) -> list[str]:
    run_dir = session.view().run_dir
    assert run_dir is not None
    return sorted(path.name for path in (run_dir / "steps").iterdir())


def _summary(session: RoundSession) -> dict:
    run_dir = session.view().run_dir
    assert run_dir is not None
    return json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))


def test_the_whole_round_runs_both_cases_and_claims_the_plain_name(tmp_path: Path):
    session = _session(tmp_path)
    _walk_to_the_end(session)

    assert _steps(session) == ["001-chain-H01", "002-chain-H01-linked"]
    assert _summary(session)["selection"] == ""
    assert session.view().run_dir is not None
    assert "~" not in session.view().run_dir.name


def test_from_case_replays_the_anchor_and_the_chain_still_resolves(tmp_path: Path):
    """The point of `case_forward`: the credential is minted, then spent."""
    session = _session(tmp_path, from_case="chain-H01", selection="from-chain-H01")
    _walk_to_the_end(session)

    run_dir = session.view().run_dir
    assert run_dir is not None
    assert run_dir.name.endswith("~from-chain-H01")
    assert _steps(session) == ["001-chain-H01", "002-chain-H01-linked"]

    request = json.loads((run_dir / "steps" / "002-chain-H01-linked" / "request.json").read_text())
    assert request["body"]["parent_id"] == "chain-001"
    assert _summary(session)["selection"] == "from-chain-H01"


def test_only_case_replays_one_case_and_nothing_else(tmp_path: Path):
    session = _session(tmp_path, only_case="chain-H01", selection="case-chain-H01")
    _walk_to_the_end(session)

    run_dir = session.view().run_dir
    assert run_dir is not None
    assert run_dir.name.endswith("~case-chain-H01")
    assert _steps(session) == ["001-chain-H01"]
    assert _summary(session)["selection"] == "case-chain-H01"


def test_a_case_already_covering_the_chain_runs_alone_through_the_engine(tmp_path: Path):
    """`only_case` on a case with no upstream capture is a complete run."""
    config = _config(tmp_path)
    tree = index_workspace(
        FIXTURES, tmp_path / "runs", config.project, project_id=PROJECT
    )
    plan = plan_for(
        tree,
        CHAIN_H01,
        CASE,
        root=FIXTURES,
        project=config.project,
    )
    engine = RunEngine(
        root=FIXTURES,
        config=config,
        client=_client(),
        runs_dir=tmp_path / "runs",
    )
    engine.start(plan, "walk")
    parked = engine.wait_settled(timeout=30)

    assert parked.case_id == "chain-H01"
    assert parked.step_total == 1
    assert parked.case_total == 1

    engine.submit_verdict("pass", "", True)
    done = engine.wait_settled(timeout=30)
    assert done.phase == "done"
    assert done.counts == {"pass": 1, "fail": 0, "skip": 0}


def test_running_a_dependent_case_alone_fails_instead_of_faking_a_pass(tmp_path: Path):
    """A spent capture with no mint is a 400 nobody claimed, never a green step."""
    session = _session(tmp_path, only_case="chain-H01-linked", selection="case-chain-H01-linked")

    with pytest.raises(HarnessError) as caught:
        session.start("walk")

    assert caught.value.code == "CAPTURE_MISSING"
    assert "chain_id" in caught.value.message


def test_coverage_is_checked_against_the_whole_round_not_the_selection(tmp_path: Path):
    """A one-case run may skip kinds; it may never relax the round's coverage.

    `h01-only` has a coverage gap, and `only_case` is applied *after*
    `_require_coverage` on purpose — otherwise selecting a subset would be a way to
    start a round the harness has already refused.
    """
    with pytest.raises(HarnessError) as caught:
        _session_with_round(
            tmp_path,
            FIXTURES / "rounds" / "h01-only.yaml",
            only_case="ingest-H01",
            selection="case-ingest-H01",
        )

    assert caught.value.code == "ROUND_INVALID"
    assert "coverage" in caught.value.message
    # The missing kinds are ones the selection never contained, which is how we know
    # the check ran over the round and not over the one case that was asked for.
    assert any("ingest-N-auth" in detail for detail in caught.value.details)


def test_an_unknown_case_id_is_refused_by_name(tmp_path: Path):
    with pytest.raises(HarnessError) as caught:
        _session(tmp_path, only_case="chain-H99", selection="case-chain-H99")

    assert caught.value.code == "CASE_INVALID"
    assert "chain-H99" in caught.value.message


def test_a_partial_run_never_becomes_the_rounds_latest_run(tmp_path: Path):
    """What `~case-x` in the directory name is for.

    `find_latest_run(round_id)` compares the whole tail, so the name a partial run
    writes cannot answer for the round — the round's status stays the whole round's,
    which is the only thing it can honestly be.
    """
    runs_dir = tmp_path / "runs"
    full = _session(tmp_path)
    _walk_to_the_end(full)
    canonical = find_latest_run(runs_dir, "chain-two")
    assert canonical is not None
    assert "~" not in canonical.name

    partial = _session(tmp_path, only_case="chain-H01", selection="case-chain-H01")
    _walk_to_the_end(partial)

    assert find_latest_run(runs_dir, "chain-two") == canonical
    assert len([path for path in runs_dir.iterdir() if "~" in path.name]) == 1


def test_a_partial_run_alone_leaves_the_round_unreviewed(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    partial = _session(tmp_path, only_case="chain-H01", selection="case-chain-H01")
    _walk_to_the_end(partial)

    assert find_latest_run(runs_dir, "chain-two") is None
