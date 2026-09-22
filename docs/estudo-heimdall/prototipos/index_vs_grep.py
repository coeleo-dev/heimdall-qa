#!/usr/bin/env python3
"""Disposable study prototype (F4) — is an FTS5 index actually faster than rg?

Not production code. Lives under docs/ so it is never imported by the package.

The plan's F4 hypothesis was "SQLite with FTS5 for log, rebuildable by
`heimdall-qa index`, gitignored", with the acceptance criterion "search for
'pack X failed in some run' and 'this log line is in which run' is sub-second".

That criterion is only meaningful if the current state FAILS it. This script
measures both sides on the same corpus: ripgrep, a plain walk, and an FTS5
index. Whichever loses, loses with a number.

Run from the repo root:
    .venv/bin/python docs/estudo-heimdall/prototipos/index_vs_grep.py
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROTO = Path(__file__).parent
REPO = PROTO.parent.parent.parent
RUNS = REPO / "runs"
INDEX = PROTO / "runs-index.sqlite3"

QUERIES = ("trace_id", "nk_test_", "MISSING_PROPERTY", "com.nokr")


def timed(label: str, fn) -> float:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    detail = f" ({result})" if result is not None else ""
    print(f"  {label:<34} {elapsed * 1000:8.1f} ms{detail}")
    return elapsed


def corpus() -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    for path in RUNS.rglob("*"):
        if not path.is_file() or path.suffix not in (".txt", ".json", ".log", ".yml"):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        relative = str(path.relative_to(REPO))
        for number, line in enumerate(text.splitlines(), 1):
            rows.append((relative, number, line))
    return rows


def build_index(rows: list[tuple[str, int, str]]) -> sqlite3.Connection:
    if INDEX.exists():
        INDEX.unlink()
    connection = sqlite3.connect(INDEX)
    connection.execute("CREATE VIRTUAL TABLE log USING fts5(path, line, text)")
    connection.executemany("INSERT INTO log VALUES (?, ?, ?)", rows)
    connection.commit()
    return connection


def count_rows(rows: list[tuple[str, int, str]], needle: str) -> int:
    return sum(1 for _, _, line in rows if needle in line)


def main() -> int:
    if not shutil.which("rg"):
        print("rg not found; cannot compare", file=sys.stderr)
        return 1

    print("=== building the corpus ===")
    rows: list[tuple[str, int, str]] = []
    timed("walk + read every run artifact", lambda: rows.extend(corpus()) or len(rows))
    print(f"  rows indexed: {len(rows):,}")
    timed("build FTS5 index", lambda: build_index(rows).close() or None)
    print(f"  index size on disk: {INDEX.stat().st_size:,} bytes")

    connection = sqlite3.connect(INDEX)
    for needle in QUERIES:
        print(f"\n=== query: {needle!r} (matching LINES on all three) ===")

        def rg() -> int:
            found = subprocess.run(
                ["rg", "--no-messages", needle, str(RUNS)],
                capture_output=True, text=True,
            )
            return len([line for line in found.stdout.splitlines() if line.strip()])

        def walk() -> int:
            return count_rows(rows, needle)

        def fts() -> int:
            # FTS5 tokenizes, so substring queries need a quoted phrase; the
            # point of this row is the LATENCY, and the count is reported so a
            # wrong semantic is visible rather than hidden.
            return connection.execute(
                "SELECT count(*) FROM log WHERE log MATCH ?", (f'"{needle}"',)
            ).fetchone()[0]

        timed("ripgrep (matching lines)", rg)
        timed("python walk (corpus in memory)", walk)
        timed("FTS5 MATCH (tokenized)", fts)

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
