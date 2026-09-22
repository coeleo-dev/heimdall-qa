#!/usr/bin/env python3
"""Disposable study prototype (F4) — measure the case-consolidation trade-off.

Not production code. Lives under docs/ so it is never imported by the package.

Hypothesis from the plan: "1 file per contract with the cases inline, 539 -> ~48
files, keeping line diff in PR". This script checks both halves:

  1. does 539 -> 48 actually hold, and what does it cost in bytes?
  2. does editing ONE case still produce a small PR diff?

The second half is the one that decides. A consolidation that makes review
diffs unreadable trades a navigation problem for a review problem.

Run from the repo root:
    .venv/bin/python docs/estudo-heimdall/prototipos/consolidate_cases.py
"""

from __future__ import annotations

import difflib
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

PROTO = Path(__file__).parent
REPO = PROTO.parent.parent.parent
CASES = REPO / "cases"


def load_case(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def consolidate() -> dict[str, str]:
    """Return {contract_name: consolidated_yaml_text} for every case directory."""
    out: dict[str, str] = {}
    for directory in sorted(p for p in CASES.iterdir() if p.is_dir()):
        cases = [load_case(p) for p in sorted(directory.glob("*.yaml"))]
        cases.sort(key=lambda c: str(c.get("id", "")))
        document = {"contract": f"contracts/{directory.name}.yaml", "cases": cases}
        out[directory.name] = yaml.safe_dump(
            document, sort_keys=False, allow_unicode=True, width=100
        )
    return out


def measure_shape(consolidated: dict[str, str]) -> None:
    before_files = sum(1 for _ in CASES.rglob("*.yaml"))
    before_bytes = sum(p.stat().st_size for p in CASES.rglob("*.yaml"))
    after_bytes = sum(len(t.encode()) for t in consolidated.values())
    print("=== shape ===")
    print(f"  files: {before_files} -> {len(consolidated)}")
    print(f"  bytes: {before_bytes:,} -> {after_bytes:,} "
          f"({after_bytes / before_bytes:.2f}x)")
    biggest = max(consolidated.items(), key=lambda kv: len(kv[1]))
    print(f"  biggest consolidated file: {biggest[0]}.yaml "
          f"({len(biggest[1].splitlines())} lines, "
          f"{len(biggest[1].encode()):,} bytes)")


def measure_diff(consolidated: dict[str, str]) -> None:
    """Simulate a one-field edit in one case and measure the review diff."""
    target = "ingest"
    original = consolidated[target]
    document = yaml.safe_load(original)

    # The smallest realistic authoring change: one boundary value in one case.
    touched = None
    for case in document["cases"]:
        if case.get("kind") == "N-over-transaction_id":
            case.setdefault("expect", {})
            case["note"] = "study prototype: simulated one-field edit"
            touched = case["id"]
            break
    if touched is None:  # fall back: touch the first case
        document["cases"][0]["note"] = "study prototype: simulated one-field edit"
        touched = document["cases"][0].get("id")

    edited = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    diff = list(
        difflib.unified_diff(
            original.splitlines(), edited.splitlines(),
            fromfile=f"{target}.yaml (before)", tofile=f"{target}.yaml (after)", lineterm="",
        )
    )
    changed = [line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    print("=== diff for a one-case edit ===")
    print(f"  case touched: {touched}")
    print(f"  diff lines: {len(diff)}  (+/- lines: {len(changed)})")
    for line in diff[:24]:
        print(f"    {line}")
    if len(diff) > 24:
        print(f"    ... ({len(diff) - 24} more)")


def measure_reorder() -> None:
    """Does the consolidated form reshuffle unrelated cases on a real edit?"""
    print("=== stability of the consolidated form ===")
    source = CASES / "ingest"
    files = sorted(source.glob("*.yaml"))
    ids = [str(load_case(p).get("id", "")) for p in files]
    print(f"  cases on disk, sorted by filename: {len(files)}")
    print(f"  ids already sorted by filename? {ids == sorted(ids)}")
    if ids != sorted(ids):
        inversions = sum(1 for a, b in zip(ids, sorted(ids)) if a != b)
        print(f"  -> {inversions} of {len(ids)} would change position if sorted by id")
        print("  -> a consolidated file sorted by id produces a reshuffle diff,")
        print("     so the prototype must preserve on-disk order, not sort.")


def main() -> int:
    consolidated = consolidate()
    measure_shape(consolidated)
    print()
    measure_diff(consolidated)
    print()
    measure_reorder()
    return 0


if __name__ == "__main__":
    sys.exit(main())
