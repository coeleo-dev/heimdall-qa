import json
from pathlib import Path

from heimdall_qa import keys
from heimdall_qa.collection import find_node
from heimdall_qa.collection import index_workspace
from heimdall_qa.collection import inspect_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The id these tests index under. Not a real registry id — nothing derives it from a
#: path here — but the index and the lookups below have to agree on *something*, and
#: going through `keys` is what keeps this file from being a second implementer of the
#: key format.
PROJECT = "fixtures"


def test_index_workspace_groups_campaign_and_orphans():
    tree = index_workspace(FIXTURES, FIXTURES / "runs-missing", project_id=PROJECT)
    keys_seen = [node.key for node in tree]
    assert keys.for_campaign(PROJECT, "example-campaign") in keys_seen
    labels = {node.label: node for node in tree}
    assert "example-campaign" in labels
    campaign = labels["example-campaign"]
    assert campaign.kind == "campaign"
    folder = campaign.children[0]
    assert folder.kind == "folder"
    assert folder.label == "A4"
    example = folder.children[0]
    assert example.path == "rounds/example.yaml"
    assert example.status == "not_reviewed"
    assert example.startable is True
    assert any(child.case_id == "echo-H01" for child in example.children)
    orphan_ids = [node.round_id for node in tree if node.kind == "round"]
    assert "walk-hn" in orphan_ids
    assert "ingest-h01-only" in orphan_ids
    assert "example" not in orphan_ids


def test_missing_round_is_missing_and_not_startable():
    tree = index_workspace(FIXTURES, FIXTURES / "no-runs", project_id=PROJECT)
    missing_campaign = find_node(tree, keys.for_campaign(PROJECT, "missing-round"))
    assert missing_campaign is not None
    round_node = missing_campaign.children[0].children[0]
    assert round_node.status == "missing"
    assert round_node.startable is False


def test_incomplete_round_is_not_ready():
    node = inspect_round(
        "rounds/h01-only.yaml",
        FIXTURES,
        FIXTURES / "no-runs",
        endpoint=None,
        matrix=None,
    )
    assert node.status == "not_ready"
    assert node.startable is False
    assert node.round_id == "ingest-h01-only"


def test_index_pass_from_fake_run(tmp_path: Path):
    run_dir = tmp_path / "2026-09-01T1200-example"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps({"counts": {"pass": 1, "fail": 0, "skip": 0, "http_5xx": 0}}),
        encoding="utf-8",
    )
    step = run_dir / "steps" / "001-echo-H01"
    step.mkdir(parents=True)
    (step / "verdict.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )
    node = inspect_round(
        "rounds/example.yaml",
        FIXTURES,
        tmp_path,
        endpoint="POST /qa/echo",
        matrix="A4",
    )
    assert node.status == "pass"
    assert node.children[0].status == "pass"


def test_index_http_5xx_from_fake_run(tmp_path: Path):
    run_dir = tmp_path / "2026-09-01T1500-example"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps({"counts": {"pass": 0, "fail": 0, "skip": 0, "http_5xx": 1}}),
        encoding="utf-8",
    )
    node = inspect_round(
        "rounds/example.yaml",
        FIXTURES,
        tmp_path,
        endpoint="POST /qa/echo",
        matrix="A4",
    )
    assert node.status == "http_5xx"
