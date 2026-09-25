"""The projects registry: where the client remembers the roots it has open.

The registry is the one piece of the harness that stores *the reviewer's* state rather
than the project's, so its tests are mostly about restraint: it must not write into a
home directory, it must not register a directory that is not a project, and it must
not lose the lines it cannot use.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from heimdall_qa.errors import HarnessError
from heimdall_qa.projects import ProjectEntry
from heimdall_qa.projects import ProjectsRegistry
from heimdall_qa.projects import id_for
from heimdall_qa.projects import looks_like_project
from heimdall_qa.projects import registry_path


def _project(root: Path, *, name: str = "api") -> Path:
    """A directory that passes the gate, as cheaply as possible."""
    (root / "campaigns").mkdir(parents=True)
    (root / name).write_text("", encoding="utf-8")
    return root


# -- where the file lives -------------------------------------------------


def test_the_environment_names_the_file_outright(tmp_path: Path, monkeypatch):
    override = tmp_path / "elsewhere" / "projects.yaml"
    monkeypatch.setenv("HEIMDALL_QA_REGISTRY", str(override))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert registry_path() == override


def test_xdg_config_home_is_honoured(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("HEIMDALL_QA_REGISTRY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert registry_path() == tmp_path / "xdg" / "heimdall-qa" / "projects.yaml"


def test_the_last_resort_is_the_config_directory_in_the_home(tmp_path: Path, monkeypatch):
    """XDG says `~/.config` when the variable is unset, and the registry follows it."""
    monkeypatch.delenv("HEIMDALL_QA_REGISTRY", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registry_path() == tmp_path / ".config" / "heimdall-qa" / "projects.yaml"


# -- the id ---------------------------------------------------------------


def test_the_id_is_stable_for_one_path(tmp_path: Path):
    root = _project(tmp_path / "api")
    assert id_for(root) == id_for(root)
    assert id_for(root) == id_for(root / ".")


def test_two_checkouts_of_one_name_are_two_ids(tmp_path: Path):
    """A bare folder name collides the moment somebody has two of one repository."""
    first = _project(tmp_path / "work" / "orders-api")
    second = _project(tmp_path / "sandbox" / "orders-api")
    assert first.name == second.name
    assert id_for(first) != id_for(second)


def test_the_id_is_legible_in_a_key(tmp_path: Path):
    entry_id = id_for(_project(tmp_path / "Orders API"))
    assert entry_id.startswith("orders-api-")
    assert " " not in entry_id
    assert ":" not in entry_id


# -- the gate -------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    ["qa/project.yaml", "campaigns", "rounds", "qa/campaigns", "qa/rounds"],
)
def test_each_marker_makes_a_directory_a_project(tmp_path: Path, marker: str):
    root = tmp_path / "api"
    target = root / marker
    if target.suffix:
        target.parent.mkdir(parents=True)
        target.write_text("version: 1\n", encoding="utf-8")
    else:
        target.mkdir(parents=True)
    assert looks_like_project(root) is True


def test_a_directory_without_a_marker_is_not_a_project(tmp_path: Path):
    root = tmp_path / "not-a-project"
    root.mkdir()
    (root / "README.md").write_text("hello", encoding="utf-8")
    assert looks_like_project(root) is False


def test_a_file_is_not_a_project(tmp_path: Path):
    (tmp_path / "api").write_text("", encoding="utf-8")
    assert looks_like_project(tmp_path / "api") is False


def test_a_missing_directory_is_not_a_project(tmp_path: Path):
    assert looks_like_project(tmp_path / "nowhere") is False


def test_registering_a_directory_that_is_not_a_project_is_refused(tmp_path: Path):
    """The gate is what keeps a slip in a file dialog out of the tree."""
    root = tmp_path / "random"
    root.mkdir()
    registry = ProjectsRegistry(tmp_path / "projects.yaml")

    with pytest.raises(HarnessError) as caught:
        registry.add(root)

    assert caught.value.code == "REGISTRY_NOT_A_PROJECT"
    assert caught.value.hint
    assert not (tmp_path / "projects.yaml").exists(), "a refused root writes nothing"


# -- the file -------------------------------------------------------------


def test_a_missing_file_is_an_empty_registry(tmp_path: Path):
    """The first launch of a fresh install has none, and that is not an error."""
    assert ProjectsRegistry(tmp_path / "nothing.yaml").load() == ()


def test_add_writes_and_load_reads_it_back(tmp_path: Path):
    root = _project(tmp_path / "api")
    registry = ProjectsRegistry(tmp_path / "projects.yaml")
    entry = registry.add(root)

    assert entry == ProjectEntry(id=id_for(root), name="api", root=root.resolve())
    assert registry.load() == (entry,)
    assert registry.list() == registry.load()


def test_the_file_is_a_plain_mapping_a_person_can_edit(tmp_path: Path):
    root = _project(tmp_path / "api")
    path = tmp_path / "projects.yaml"
    ProjectsRegistry(path).add(root)

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert list(payload) == ["projects"]
    assert payload["projects"][0]["root"] == str(root.resolve())


def test_adding_the_same_root_twice_is_one_line(tmp_path: Path):
    root = _project(tmp_path / "api")
    registry = ProjectsRegistry(tmp_path / "projects.yaml")
    registry.add(root)
    registry.add(root)
    assert len(registry.load()) == 1


def test_adding_again_refreshes_the_name(tmp_path: Path):
    root = _project(tmp_path / "api")
    registry = ProjectsRegistry(tmp_path / "projects.yaml")
    registry.add(root, name="first")
    registry.add(root, name="second")
    assert [entry.name for entry in registry.load()] == ["second"]


def test_registration_order_is_kept(tmp_path: Path):
    """It is the order the reviewer built the file in; re-sorting moves their tree."""
    registry = ProjectsRegistry(tmp_path / "projects.yaml")
    registry.add(_project(tmp_path / "one"))
    registry.add(_project(tmp_path / "two"))
    registry.add(_project(tmp_path / "three"))
    assert [entry.name for entry in registry.load()] == ["one", "two", "three"]


def test_adding_a_project_does_not_drop_the_others(tmp_path: Path):
    registry = ProjectsRegistry(tmp_path / "projects.yaml")
    registry.add(_project(tmp_path / "one"))
    registry.add(_project(tmp_path / "two"))
    assert len(registry.load()) == 2


def test_save_false_derives_the_entry_without_writing(tmp_path: Path):
    """`id_for` is pure, so a caller that only wants the id pays for no file."""
    root = _project(tmp_path / "api")
    path = tmp_path / "projects.yaml"
    entry = ProjectsRegistry(path).add(root, save=False)
    assert entry.id == id_for(root)
    assert not path.exists()


def test_remove_forgets_a_line_and_reports_whether_it_was_there(tmp_path: Path):
    root = _project(tmp_path / "api")
    registry = ProjectsRegistry(tmp_path / "projects.yaml")
    entry = registry.add(root)

    assert registry.remove(entry.id) is True
    assert registry.load() == ()
    assert registry.remove(entry.id) is False


def test_resolve_finds_one_entry_or_none(tmp_path: Path):
    root = _project(tmp_path / "api")
    registry = ProjectsRegistry(tmp_path / "projects.yaml")
    entry = registry.add(root)

    assert registry.resolve(entry.id) == entry
    assert registry.resolve("not-there") is None


def test_an_unreadable_file_is_a_named_error(tmp_path: Path):
    """Corrupt YAML is the harness's error with the path, not a traceback at startup."""
    path = tmp_path / "projects.yaml"
    path.write_text("projects: [oops\n", encoding="utf-8")

    with pytest.raises(HarnessError) as caught:
        ProjectsRegistry(path).load()

    assert caught.value.code == "REGISTRY_INVALID"
    assert str(path) in caught.value.message
    assert caught.value.hint


def test_a_file_that_is_not_a_mapping_is_a_named_error(tmp_path: Path):
    path = tmp_path / "projects.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(HarnessError) as caught:
        ProjectsRegistry(path).load()

    assert caught.value.code == "REGISTRY_INVALID"


def test_lines_without_a_root_are_skipped(tmp_path: Path):
    """A hand-edited file must not take the client down over a half-typed line."""
    root = _project(tmp_path / "api")
    path = tmp_path / "projects.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "projects": [
                    {"id": "broken"},
                    {"id": id_for(root), "name": "api", "root": str(root)},
                    "not-a-mapping",
                ]
            }
        ),
        encoding="utf-8",
    )

    entries = ProjectsRegistry(path).load()
    assert [entry.name for entry in entries] == ["api"]


def test_a_line_without_an_id_derives_one_from_its_root(tmp_path: Path):
    root = _project(tmp_path / "api")
    path = tmp_path / "projects.yaml"
    path.write_text(
        yaml.safe_dump({"projects": [{"root": str(root)}]}),
        encoding="utf-8",
    )

    assert [entry.id for entry in ProjectsRegistry(path).load()] == [id_for(root)]


def test_an_unwritable_directory_is_a_named_error(tmp_path: Path):
    """This runs on the request that flipped a switch; it needs the path, not a 500."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    root = _project(tmp_path / "api")

    with pytest.raises(HarnessError) as caught:
        ProjectsRegistry(blocker / "projects.yaml").add(root)

    assert caught.value.code == "REGISTRY_UNWRITABLE"
    assert caught.value.hint


def test_the_write_is_atomic_and_leaves_no_temporary_behind(tmp_path: Path):
    """An interrupted write keeps the old registry, which needs the rename."""
    root = _project(tmp_path / "api")
    directory = tmp_path / "config"
    registry = ProjectsRegistry(directory / "projects.yaml")
    registry.add(root)
    registry.add(_project(tmp_path / "other"))

    leftovers = [item for item in directory.iterdir() if item.name != "projects.yaml"]
    assert leftovers == []
