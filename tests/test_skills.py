"""Every agent's copy of a skill must not drift from `.agents/skills/`.

`.agents/skills/` is the source; `.cursor/skills/` and `.kiro/skills/` are generated
by `bin/sync-skills`, one directory per agent because an agent only reads its own.
The pair in this repository had already diverged — one copy into links that resolved
nowhere — and a third consumer multiplies that: a test is what turns "remember to run
the sync script" into a rule.

Kiro also reads `AGENTS.md` on its own, without a copy; what it cannot find by itself
is a skill's trigger, which is why the copy exists at all.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / ".agents" / "skills"
TARGETS = [REPO / ".cursor" / "skills", REPO / ".kiro" / "skills"]


def _skill_files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.md"))
    }


def test_every_generated_copy_matches_its_source():
    source = _skill_files(SOURCE)
    for target in TARGETS:
        assert _skill_files(target) == source, target


def test_the_sync_check_agrees_that_nothing_is_pending():
    """The script and this test must not be two opinions about the same fact."""
    result = subprocess.run(
        [str(REPO / "bin" / "sync-skills"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_the_core_ships_exactly_one_skill():
    """One skill for the core's audience; a product's skill is the product's paper.

    The core's skill is the one this repository owns. A product's own skill sits
    beside it on the machine that has the product, and its absence is the split
    working rather than a gap: the suite has to pass without it.
    """
    assert "heimdall-qa/SKILL.md" in _skill_files(SOURCE)


def test_the_core_skill_does_not_name_a_product():
    """A stranger's skill must not know whose API it is being pointed at.

    The whole file, not the closing section: the closing used to be exempt because
    its job was to name the product once and send the agent over there. It cannot do
    that any more — there is no product here to send anyone to — so the exemption
    went with it and the assertion got stronger.
    """
    body = (SOURCE / "heimdall-qa" / "SKILL.md").read_text(encoding="utf-8")

    assert "nokr" not in body.lower()
    assert "trilho" not in body.lower()
