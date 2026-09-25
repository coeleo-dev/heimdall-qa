"""The bundled demo project: materialization and the service that owns its socket.

The sample is package data, so these tests are mostly about what travels *out* of the
wheel: the tree copies, the one descriptor line is rewritten to the port the mock
actually bound, the shipped evidence lands in an empty `runs/` and never over one the
reviewer made, and the whole thing passes the same project gate a directory added by
hand goes through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heimdall_qa import operations
from heimdall_qa.demo.sample import DEMO_DIR_NAME
from heimdall_qa.demo.sample import DemoService
from heimdall_qa.demo.sample import RUNS_DIR_NAME
from heimdall_qa.demo.sample import demo_root
from heimdall_qa.demo.sample import materialize_demo
from heimdall_qa.demo.sample import sample_dir
from heimdall_qa.projects import ProjectsRegistry
from heimdall_qa.projects import data_home
from heimdall_qa.projects import id_for

_DATA_DIR = "demo-data"
_BASE_URL = "http://127.0.0.1:45678"


def _registry(tmp_path: Path) -> ProjectsRegistry:
    return ProjectsRegistry(tmp_path / "projects.yaml")


# -- where the tree lands -------------------------------------------------


def test_the_root_is_the_data_home_unless_the_caller_says_otherwise(tmp_path: Path):
    assert demo_root(tmp_path) == tmp_path / DEMO_DIR_NAME
    assert demo_root() == data_home() / DEMO_DIR_NAME


def test_the_sample_ships_the_rounds_it_documents(tmp_path: Path):
    """The evidence is committed under `recorded/`, and each round's file is real."""
    rounds = {path.name for path in (sample_dir() / "rounds").glob("*.yaml")}
    assert {"smoke.yaml", "chain.yaml", "boom.yaml", "warn.yaml", "red.yaml"} <= rounds
    recorded = {path.name for path in (sample_dir() / "recorded").iterdir()}
    assert any(name.endswith("-smoke") for name in recorded)


# -- materialization ------------------------------------------------------


def test_materialize_copies_rewrites_and_registers(tmp_path: Path):
    registry = _registry(tmp_path)
    ref = materialize_demo(base_url=_BASE_URL, data_dir=tmp_path, registry=registry)

    assert ref.id == id_for(tmp_path / DEMO_DIR_NAME)
    assert ref.root == (tmp_path / DEMO_DIR_NAME).resolve()
    descriptor = ref.root / "qa" / "project.yaml"
    assert descriptor.is_file()
    # The fixed 8130 the sample ships with is gone; the caller's origin is the only
    # `base_url` left, which is the whole point of materializing rather than reading.
    text = descriptor.read_text(encoding="utf-8")
    assert f"base_url: {_BASE_URL}" in text
    assert "127.0.0.1:8130" not in text
    # And it went through the gate: the file holds the line the tree will draw.
    assert [entry.id for entry in registry.load()] == [ref.id]


def test_the_shipped_evidence_only_lands_in_an_empty_runs(tmp_path: Path):
    """The shipped evidence is copied once, into an empty `runs/`. A reviewer who has
    already run the demo keeps their own evidence."""
    runs = tmp_path / DEMO_DIR_NAME / RUNS_DIR_NAME
    theirs = runs / "2026-01-01T0000-smoke"
    theirs.mkdir(parents=True)
    (theirs / "summary.json").write_text("{}", encoding="utf-8")

    materialize_demo(base_url=_BASE_URL, data_dir=tmp_path, registry=_registry(tmp_path))

    assert {path.name for path in runs.iterdir()} == {theirs.name}


def test_materialize_replaces_the_the_tree_it_already_wrote(tmp_path: Path):
    """Idempotent for the content: a second press refreshes the files it owns."""
    materialize_demo(base_url=_BASE_URL, data_dir=tmp_path, registry=_registry(tmp_path))
    descriptor = tmp_path / DEMO_DIR_NAME / "qa" / "project.yaml"
    descriptor.write_text(descriptor.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    (tmp_path / DEMO_DIR_NAME / "rounds" / "stray.yaml").write_text("", encoding="utf-8")

    materialize_demo(base_url="http://127.0.0.1:40000", data_dir=tmp_path, registry=_registry(tmp_path))

    text = descriptor.read_text(encoding="utf-8")
    assert "# tampered" not in text
    assert "base_url: http://127.0.0.1:40000" in text


def test_the_materialized_project_opens(tmp_path: Path):
    """`open_project` resolves the descriptor, so a broken sample fails here and not
    in the window after someone pressed a button labelled "demo"."""
    ref = materialize_demo(base_url=_BASE_URL, data_dir=tmp_path, registry=_registry(tmp_path))
    opened = operations.open_project(
        operations.ProjectsRegistry(path=tmp_path / "projects.yaml").load()[0]
    )
    assert opened.root == ref.root
    assert opened.config.project.descriptor is not None
    assert opened.config.project.descriptor.project.id == "demo"


# -- the service ----------------------------------------------------------


def test_the_service_is_stopped_before_it_is_started(tmp_path: Path):
    service = DemoService(data_dir=tmp_path / _DATA_DIR)
    state = service.state()
    assert state.state == "stopped"
    assert state.port == 0
    assert state.enabled is False
    assert state.log_path == str(service.root / "demo.log")


def test_the_service_starts_binds_and_stops(tmp_path: Path):
    service = DemoService(data_dir=tmp_path / _DATA_DIR)
    state, ref = service.start(_registry(tmp_path))
    try:
        assert state.state == "running"
        assert state.port > 0
        assert state.base_url == f"http://{state.host}:{state.port}"
        assert ref is not None
        # The descriptor points at the port the socket really bound, not at `:0`.
        descriptor = (service.root / "qa" / "project.yaml").read_text(encoding="utf-8")
        assert f"base_url: {state.base_url}" in descriptor
    finally:
        stopped = service.stop()
    assert stopped.state == "stopped"
    assert stopped.port == 0


def test_stopping_keeps_the_files(tmp_path: Path):
    """A demo the reviewer has been reading is not theirs to lose because they closed
    the switch."""
    service = DemoService(data_dir=tmp_path / _DATA_DIR)
    service.start(_registry(tmp_path))
    service.stop()
    assert (service.root / "qa" / "project.yaml").is_file()
    assert any((service.root / RUNS_DIR_NAME).iterdir())
