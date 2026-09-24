"""Onboarding: the tree `init` writes has to work, or the boilerplate is a lie.

The templates are package data, which is the only reason this file can make the claim
at all: they are run here rather than described, so a template that stops validating
fails in the suite instead of in somebody's first five minutes with the harness.

What is *not* here is the round actually hitting an API — that needs a live server and
belongs in `examples/toy-provider/gate.sh`, which already has one running.
"""

from pathlib import Path

from heimdall_qa.onboarding import TEMPLATES
from heimdall_qa.onboarding import starter_tree
from heimdall_qa.onboarding import write_starter_tree
from heimdall_qa.operations import resolve_settings
from heimdall_qa.operations import validate_round_file

ROUND = "rounds/smoke.yaml"


def test_the_templates_are_package_data_and_all_present():
    """A wheel that forgot them is a broken `init`, and it fails here, not there."""
    source = starter_tree()

    assert source.is_dir()
    for relative in TEMPLATES:
        assert (source / relative).is_file(), relative


def test_init_writes_the_tree_it_reports(tmp_path: Path):
    reported = write_starter_tree(tmp_path)

    assert reported == [f"wrote {relative}" for relative in TEMPLATES]
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == sorted(
        Path(relative).name for relative in TEMPLATES
    )


def test_init_is_idempotent_and_keeps_what_a_person_edited(tmp_path: Path):
    """The second run is usually somebody who has already started, which is the point.

    A tool that overwrites a filled `qa/project.yaml` on a re-run teaches people not to
    re-run it, and that costs more than the convenience of a fresh tree.
    """
    write_starter_tree(tmp_path)
    descriptor = tmp_path / "qa" / "project.yaml"
    descriptor.write_text("version: 1\n# mine\n", encoding="utf-8")

    reported = write_starter_tree(tmp_path)

    assert reported == [f"kept {relative}" for relative in TEMPLATES]
    assert descriptor.read_text(encoding="utf-8") == "version: 1\n# mine\n"


def test_force_restores_the_boilerplate_and_still_touches_only_its_own(tmp_path: Path):
    """`--force` is opt-in, and even then it is not licence to wander."""
    write_starter_tree(tmp_path)
    (tmp_path / "qa" / "project.yaml").write_text("# mine\n", encoding="utf-8")
    mine = tmp_path / "notes.md"
    mine.write_text("do not touch\n", encoding="utf-8")

    reported = write_starter_tree(tmp_path, force=True)

    assert reported == [f"wrote {relative}" for relative in TEMPLATES]
    assert "starter" in (tmp_path / "qa" / "project.yaml").read_text(encoding="utf-8")
    assert mine.read_text(encoding="utf-8") == "do not touch\n"


def test_the_boilerplate_validates_where_it_lands(tmp_path: Path):
    """The claim that matters: a fresh tree is a passing round, not a broken one.

    Validating answers offline — nothing is started, nothing is reached — so this is
    the cheapest possible check that the four files agree with each other and with the
    schema the harness actually ships.
    """
    write_starter_tree(tmp_path)
    settings = resolve_settings(tmp_path)

    assert validate_round_file(settings, ROUND) == []


def test_the_starter_descriptor_is_the_six_lines_it_claims(tmp_path: Path):
    """The ramp is short on purpose, and it is a claim worth failing over.

    Every field a starter declares is a field a newcomer has to understand before the
    first run answers, so the length of this file is a feature with a number on it —
    and the number in this test's name is the one the README and both examples state,
    which is why it is worth pinning: they drift together or not at all.
    """
    write_starter_tree(tmp_path)
    lines = [
        line
        for line in (tmp_path / "qa" / "project.yaml").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert lines == [
        "version: 1",
        "project:",
        "  id: starter",
        "environments:",
        "  local:",
        "    base_url: http://127.0.0.1:8080",
    ]
