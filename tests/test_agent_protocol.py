"""The documents an agent is handed must keep saying what they must say.

Two audiences and two files, and the split decides which rule lives where:

* `AGENTS.md` — an agent working **in this repository**: the boundary, the CLI, the
  language. Product rules do not belong here, because a stranger cloning the core
  does not have a product.
* the core's skill — an agent doing **any API's work**.

A product's own paper — its skill, and the `AGENTS.md` of the API next door — is not
in this repository: it is the product's, and this suite has to pass without it. The
tests that read those documents live beside the product, for the same reason
`tests/provider_marks.py` skips a product's oracle rather than requiring it: a core
that cannot be tested without a product has not been split.

A rule stated in both is a rule that will diverge, so each is asserted only once.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_SKILL = ROOT / ".agents" / "skills" / "heimdall-qa" / "SKILL.md"


def test_agents_md_states_the_boundary_and_the_generic_rules():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "never names a product" in text
    assert "src/heimdall_qa/" in text
    assert "secrets.local.yaml" in text
    assert "DTO" in text
    assert "waive" in text.lower()
    assert "7878" in text
    assert "heimdall-qa fixture" in text
    assert "generate:" in text
    assert "gate.sh" in text
    assert "fastapi" not in text.lower()


def test_agents_md_does_not_carry_product_content():
    """A generic harness's front door must not describe one product's campaign."""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "nokr-qa.md" not in text
    assert "trilho-a" not in text.lower()
    assert "ext-ta-001" not in text


def test_core_skill_covers_the_whole_workflow_in_english():
    text = CORE_SKILL.read_text(encoding="utf-8")

    assert "heimdall-qa validate" in text
    assert "heimdall-qa last-run" in text
    assert "heimdall-qa campaign status" in text
    assert "contracts/" in text
    assert "rounds/" in text
    assert "7878" in text


def test_readme_points_at_the_examples_rather_than_documenting_them():
    """The front page teaches by pointing: the CLI here, the worked runs there."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "heimdall-qa campaign validate" in text
    assert "heimdall-qa campaign status" in text
    assert "heimdall-qa fixture" in text
    assert "examples/toy-provider/README.md" in text
    assert "examples/spring-fixture/README.md" in text


def test_the_shipped_documents_never_link_the_local_docs_directory():
    """`docs/` is not published, so a link into it is a link into nothing.

    The harness's documentation is `contrib/`. A reader of a clone has no `docs/` at
    all — it is the working papers of whoever built this, and it stays with them —
    which makes a pointer into it the same defect as a dangling source reference. It
    is the kind that survives review, too, because it resolves on the machine that
    wrote it.

    Every document a clone carries, and not only the three the root front door is made
    of: a link is a link wherever it is written, and `CONTRIBUTING.md` is where a
    contributor starts.
    """
    shipped = (
        "README.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "contrib/README.md",
        "contrib/architecture.md",
    )
    offenders: list[str] = []
    for name in shipped:
        path = ROOT / name
        assert path.is_file(), f"{name} is named here but does not exist"
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if "docs/" in target:
                offenders.append(f"{name} -> {target}")
    assert offenders == [], f"links into docs/, which is not published: {offenders}"


def test_the_front_door_does_not_even_name_the_local_docs_directory():
    """The three an agent is handed: no link, and no mention either.

    Weaker claims than the one above would be enough for a person — the prose in
    `contrib/README.md` explains *why* there is no `docs/`, which is worth saying and
    is why the link check is not a substring check. An agent reading `README.md`,
    `AGENTS.md` or the skill is deciding where to look, and a directory it is told
    about is a directory it will try.
    """
    for path in (ROOT / "README.md", ROOT / "AGENTS.md", CORE_SKILL):
        text = path.read_text(encoding="utf-8")
        assert "docs/" not in text, f"{path.name} points into docs/, which is not published"


def test_the_contribution_hub_exists_and_names_its_entry_point():
    """One directory is the documentation, and it has to be reachable from the root."""
    text = (ROOT / "contrib" / "README.md").read_text(encoding="utf-8")

    assert "(architecture.md)" in text
    assert "bin/audit-remote" in text
    assert (ROOT / "contrib" / "architecture.md").is_file()
    assert (ROOT / "CONTRIBUTING.md").is_file()


def test_the_readme_descriptor_example_is_one_that_loads(tmp_path: Path):
    """The front page's YAML is a claim about the schema, so it is loaded like one.

    It was wrong once, and in the two ways that hurt most: a flat `id:` where the
    schema wants `project.id`, and `- path:` where the key is `prefix`. Both are
    refused with `extra="forbid"`, so a reader who copied the example got
    `DESCRIPTOR_INVALID` and no reason to suspect the page rather than themselves.

    The length is pinned too, because the README's sentence, both examples and
    `tests/test_onboarding.py` all state the same number, and a number stated in four
    places drifts in three of them. They move together here or the suite says so.
    """
    from heimdall_qa.descriptor import load_descriptor

    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## The project descriptor", 1)[1].split("\n## ", 1)[0]
    blocks = re.findall(r"```yaml\n(.*?)```", section, re.DOTALL)
    assert blocks, "the README no longer shows a descriptor"

    path = tmp_path / "project.yaml"
    path.write_text(blocks[0], encoding="utf-8")
    descriptor = load_descriptor(path)

    assert descriptor.project.id == "toy"
    lines = [
        line
        for line in blocks[0].splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) == 6, lines
    assert "is six lines" in section, "the README's sentence and its example disagree"

    # The fragment below it is not a document — `project` and `environments` are
    # required — so it is one addition to the six lines above, and the key it teaches
    # is the one the schema actually reads.
    assert "- prefix: /toy" in section
    assert "- path:" not in section
