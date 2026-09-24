"""The starter tree, and the rule that makes it safe to re-run.

A project's first encounter with this harness is a directory of YAML it did not write.
`init` writes the smallest one that runs — a health endpoint, one case, one round — so
that the shape is visible before the documentation is needed and the first command
somebody types answers instead of refusing.

The templates are package data rather than files generated in code, because the
boilerplate and the harness drift the same way documentation does: silently. Shipping
them as data means `tests/test_onboarding.py` can run them, and a template that stops
validating fails the suite rather than a stranger's first five minutes.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from heimdall_qa.errors import HarnessError

#: Everything `init` writes, in the order a person reads it. Kept as a list so the
#: report is deterministic and a test can assert the tree without walking it.
TEMPLATES: tuple[str, ...] = (
    "README.md",
    "qa/project.yaml",
    "contracts/health.yaml",
    "baselines/empty.json",
    "cases/health.yaml",
    "suites/smoke.yaml",
    "rounds/smoke.yaml",
    "secrets.example.yaml",
)


def starter_tree() -> Traversable:
    """The templates, wherever the distribution put them."""
    return files("heimdall_qa") / "templates"


def write_starter_tree(root: Path, *, force: bool = False) -> list[str]:
    """Write the starter tree under `root`, and name what was written.

    Idempotent by default: an existing file is left exactly as it is, because the
    second run of `init` is usually somebody who has already started editing. The
    files that already exist are reported as kept, so the answer to "did this do
    anything" is the return value rather than a timestamp.

    `force` is opt-in and still only overwrites files this function ships. A tree
    with a filled `qa/project.yaml` is never clobbered silently.
    """
    source = starter_tree()
    if not source.is_dir():  # pragma: no cover - a broken wheel, not a user error
        raise HarnessError(
            code="TEMPLATES_MISSING",
            message="the starter templates are missing from this installation",
            hint="reinstall heimdall-qa; the templates are package data",
        )
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for relative in TEMPLATES:
        template = source / relative
        destination = root / relative
        if destination.exists() and not force:
            written.append(f"kept {relative}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            template.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        written.append(f"wrote {relative}")
    return written
