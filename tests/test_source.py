"""The one place the harness writes a file a person wrote.

A read that is wrong wastes a minute of a reviewer's attention; a save that is wrong
destroys work, and offering an editor is what makes that save reachable from a text
box. So most of what is tested here is what gets **refused** — a path that climbs out
of the content root, a symlink pointing out of it, a directory that is not one of the
four editable kinds — and the one thing the screen depends on: that what `validate`
says about a saved file is a fact about that file and not about a copy of it.

The write tests run against a copy of the fixture tree, because a suite that saves
over its own fixtures is a suite that passes once.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from heimdall_qa import source
from heimdall_qa.errors import HarnessError
from heimdall_qa.findings import RULE_WHY
from heimdall_qa.serve.app import create_app
from heimdall_qa.session import RoundSession
from heimdall_qa.source import READ_ONLY
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at
from heimdall_qa.testing import stub_client

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"
WALK_HN = "rounds/walk-hn.yaml"


def _valid_round() -> str:
    """A round the validator accepts, read from the fixture and never typed here.

    Typing one would let this suite pass while the schema moved on. It already did:
    `RoundFile` grew a required `suite`, and a hand-written round that had been valid
    in every test that copied it stopped loading — the failure mode of an example
    standing in for a contract.
    """
    return (FIXTURES / "rounds" / "order-first.yaml").read_text(encoding="utf-8")


def _project():
    return project_at(_DESCRIPTOR)


def _copy(tmp_path: Path) -> Path:
    """A writable copy of the fixture tree, for the tests that save.

    Copied whole rather than assembled key by key: a save test that builds its own
    content would stop noticing the day the fixtures move, and the fixtures moving is
    exactly what this suite is meant to catch.
    """
    root = tmp_path / "content"
    shutil.copytree(FIXTURES, root)
    return root


# -- what a path may name -------------------------------------------------


def test_the_kind_comes_from_the_directory_and_not_from_the_caller():
    """A caller cannot ask for a round to be checked by the contract rules."""
    assert source.kind_of("rounds/smoke.yaml") == "round"
    assert source.kind_of("campaigns/nightly.yaml") == "campaign"
    assert source.kind_of("contracts/qa-note-post.yaml") == "contract"
    assert source.kind_of("suites/values.yaml") == "suite"
    # `cases/` is content the harness reads and never writes, so it is read-only by
    # the same rule that makes the other four editable.
    assert source.kind_of("cases/echo.yaml") == READ_ONLY
    assert source.kind_of("project.yaml") == READ_ONLY


def test_a_path_that_climbs_out_of_the_content_root_is_refused(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        source.read_source(FIXTURES, _project(), "../outside.yaml")
    assert raised.value.code == "SOURCE_OUTSIDE_CONTENT"


def test_an_absolute_path_is_refused(tmp_path: Path):
    """An absolute path is a way to leave the content root, so it is the same refusal."""
    with pytest.raises(HarnessError) as raised:
        source.read_source(FIXTURES, _project(), "/etc/hosts.yaml")
    assert raised.value.code == "SOURCE_OUTSIDE_CONTENT"


def test_a_symlink_that_points_outside_is_refused(tmp_path: Path):
    """The check is on the resolved path, which is what makes a link fail here.

    A string check for `..` would pass this, and the file it read would be one the
    reviewer never meant to open — the whole reason the containment test resolves.
    """
    root = _copy(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("id: outside\n", encoding="utf-8")
    (root / "rounds" / "escape.yaml").symlink_to(outside)

    with pytest.raises(HarnessError) as raised:
        source.read_source(root, _project(), "rounds/escape.yaml")
    assert raised.value.code == "SOURCE_OUTSIDE_CONTENT"


def test_a_file_that_is_not_yaml_is_refused(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        source.read_source(FIXTURES, _project(), "rounds/walk-hn.txt")
    assert raised.value.code == "SOURCE_NOT_EDITABLE"


def test_a_file_that_is_not_there_is_a_named_404():
    """`NOT_FOUND` and not a `FileNotFoundError`, so the route has a status to send."""
    with pytest.raises(HarnessError) as raised:
        source.read_source(FIXTURES, _project(), "rounds/nope.yaml")
    assert raised.value.code == "NOT_FOUND"


# -- reading --------------------------------------------------------------


def test_a_round_is_read_with_the_findings_validate_gives_it():
    """Opening a broken round shows why, without the reviewer pressing anything."""
    document = source.read_source(FIXTURES, _project(), WALK_HN)
    assert document.path == WALK_HN
    assert document.kind == "round"
    assert document.editable
    assert document.text.strip(), "the text is what the editor draws"
    assert all(item.code in RULE_WHY for item in document.findings)
    # The two fields are one fact seen twice, and a client that checked `valid`
    # without reading `findings` would be drawing an empty list on every failure.
    assert document.valid is (not document.findings)


def test_a_contract_that_does_not_load_is_a_finding_and_not_a_crash(tmp_path: Path):
    """A YAML typo is the normal case this endpoint exists to report."""
    root = _copy(tmp_path)
    (root / "contracts" / "broken.yaml").write_text("id: broken\n  nested: nope\n")
    document = source.read_source(root, _project(), "contracts/broken.yaml")
    assert not document.valid
    assert [item.code for item in document.findings] == ["CONTRACT_UNREADABLE"]


# -- writing --------------------------------------------------------------


def test_a_save_writes_the_text_and_reports_what_validate_finds(tmp_path: Path):
    root = _copy(tmp_path)
    document = source.write_source(root, _project(), "rounds/written.yaml", _valid_round())

    assert (root / "rounds" / "written.yaml").read_text(encoding="utf-8") == _valid_round()
    assert document.path == "rounds/written.yaml"
    assert document.kind == "round"
    assert document.text == _valid_round()
    assert document.valid, [item.message for item in document.findings]


def test_an_invalid_save_is_kept_and_reported(tmp_path: Path):
    """The edit is not thrown away to make a status code more satisfying.

    An invalid round on disk is not dangerous — `validate` is the gate and a run
    refuses it — so the honest answer is to save it and say what is wrong.
    """
    root = _copy(tmp_path)
    broken = "id: broken\ncases: [\n"
    document = source.write_source(root, _project(), "rounds/broken.yaml", broken)

    assert (root / "rounds" / "broken.yaml").read_text(encoding="utf-8") == broken
    assert not document.valid
    assert "ROUND_UNREADABLE" in {item.code for item in document.findings}


def test_a_save_leaves_no_temporary_behind(tmp_path: Path):
    """The write goes through a temp and a rename; the temp must not survive it.

    A leftover `.<name>.<random>.tmp` beside the round is a file that would be picked
    up by the next glob of `rounds/*.yaml` if it were spelled `.yaml`, and by a
    reviewer's `git status` either way.
    """
    root = _copy(tmp_path)
    source.write_source(root, _project(), "rounds/written.yaml", _valid_round())
    leftovers = [path.name for path in (root / "rounds").iterdir() if ".tmp" in path.name]
    assert leftovers == []


def test_a_save_over_an_existing_file_replaces_it_whole(tmp_path: Path):
    """Not appended to, not merged: the client sends the document it holds."""
    root = _copy(tmp_path)
    original = (root / "rounds" / "walk-hn.yaml").read_text(encoding="utf-8")
    source.write_source(root, _project(), "rounds/walk-hn.yaml", _valid_round())
    replaced = (root / "rounds" / "walk-hn.yaml").read_text(encoding="utf-8")
    assert replaced == _valid_round()
    assert original not in replaced


def test_a_directory_that_is_not_editable_is_refused(tmp_path: Path):
    root = _copy(tmp_path)
    with pytest.raises(HarnessError) as raised:
        source.write_source(root, _project(), "cases/echo.yaml", "id: echo\n")
    assert raised.value.code == "SOURCE_NOT_EDITABLE"


def test_a_suite_whose_loop_names_a_missing_case_is_a_finding(tmp_path: Path):
    """A suite is checked on its own here, so the check has to be a real one.

    `heimdall-qa validate` has no suite entry point, which means this branch is the
    only place a suite is read outside a run. A loop pointing at a case file that was
    renamed is syntactically perfect and fails the first time anyone runs it.
    """
    suite = "id: suite\nsteps:\n  - loop:\n      times: 2\n      case: cases/gone.yaml\n"
    document = source.write_source(
        _copy(tmp_path), _project(), "suites/new-suite.yaml", suite
    )
    assert not document.valid
    assert [item.code for item in document.findings] == ["CASE_UNREADABLE"]


def test_a_suite_whose_loop_resolves_is_clean(tmp_path: Path):
    """The other half of the rule: the check must not fire on a suite that is fine."""
    suite = "id: suite\nsteps:\n  - loop:\n      times: 2\n      case: cases/echo.yaml\n"
    document = source.write_source(
        _copy(tmp_path), _project(), "suites/new-suite.yaml", suite
    )
    assert document.valid, [item.message for item in document.findings]


# -- through the API ------------------------------------------------------


def _client(tmp_path: Path, root: Path) -> TestClient:
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    session = RoundSession(
        root / "rounds" / "walk-hn.yaml",
        root=root,
        config=config_for(project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
            timeout=10.0,
        ),
        runs_dir=tmp_path / "runs",
    )
    return TestClient(
        create_app(session=session, api_token="test-token", webapp=stub_client(tmp_path)),
        follow_redirects=False,
    )


def _headers() -> dict[str, str]:
    return {"X-Heimdall-Token": "test-token"}


def test_the_api_reads_a_document_and_answers_with_the_contract(tmp_path: Path):
    client = _client(tmp_path, _copy(tmp_path))
    response = client.get("/api/source", params={"path": WALK_HN}, headers=_headers())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["path"] == WALK_HN
    assert payload["editable"] is True
    assert isinstance(payload["findings"], list)


def test_the_api_refuses_a_path_outside_the_content_root(tmp_path: Path):
    client = _client(tmp_path, _copy(tmp_path))
    response = client.get("/api/source", params={"path": "../x.yaml"}, headers=_headers())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SOURCE_OUTSIDE_CONTENT"


def test_the_api_answers_an_invalid_save_with_the_findings_and_a_200(tmp_path: Path):
    """A 200 because the save happened: the client has findings to draw, not an error."""
    client = _client(tmp_path, _copy(tmp_path))
    response = client.post(
        "/api/source",
        json={"path": "rounds/broken.yaml", "text": "cases: [\n"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["valid"] is False
    assert payload["findings"][0]["code"] == "ROUND_UNREADABLE"
    assert payload["findings"][0]["fix"], "every finding carries the fix"


def test_a_broken_contract_does_not_take_the_collection_down_with_it(tmp_path: Path):
    """Saving a YAML typo into a contract answers, instead of a 500 and no message.

    This is the failure the acceptance walk found and no unit test had: a save is
    followed by a re-read of the whole collection, the index validates every round, a
    round validates the contracts its cases point at, and a *syntax* error in one of
    them used to escape as a raw parser exception. The reviewer's screen went blank on
    the one action the editor exists for — typing YAML.

    So: the save reports its findings, the collection is still navigable, and the round
    that depends on the broken contract says `CONTRACT_UNREADABLE` and why.
    """
    root = _copy(tmp_path)
    client = _client(tmp_path, root)
    contract = (root / "contracts" / "qa-note-post.yaml").read_text(encoding="utf-8")

    response = client.post(
        "/api/source",
        json={"path": "contracts/qa-note-post.yaml", "text": contract + "\nrules: [\n"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["findings"][0]["code"] == "CONTRACT_UNREADABLE"

    # The collection is still readable, and the round that points at the broken
    # contract carries the reason rather than the index having given up.
    tree = client.get("/api/bootstrap", headers=_headers())
    assert tree.status_code == 200, tree.text
    assert tree.json()["tree"], "the collection went empty after a bad save"
    reopened = client.get("/api/source", params={"path": WALK_HN}, headers=_headers())
    assert reopened.status_code == 200, reopened.text
    assert "CONTRACT_UNREADABLE" in {item["code"] for item in reopened.json()["findings"]}

    # And the repair is a save like any other: no restart, no state to clear.
    fixed = client.post(
        "/api/source",
        json={"path": "contracts/qa-note-post.yaml", "text": contract},
        headers=_headers(),
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["valid"] is True


def test_the_source_routes_need_the_token(tmp_path: Path):
    client = _client(tmp_path, _copy(tmp_path))
    assert client.get("/api/source", params={"path": WALK_HN}).status_code == 401
    assert client.post(
        "/api/source", json={"path": WALK_HN, "text": ""}
    ).status_code == 401
