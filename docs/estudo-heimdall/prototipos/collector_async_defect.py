#!/usr/bin/env python3
"""Disposable study prototype (F5) — reproduce the async-worker log defect.

Not production code.

`logs/collector.py` returns as soon as the *web* log matches, discarding the
remaining `wait_logs_ms` wait. On an async endpoint the worker line always
lands after the web line, so `worker_lines` is empty and `logs_incomplete`
still reports False.

This script demonstrates it with two clocks: the collector's real behaviour, and
what the declared policy (`wait_logs_ms=500`) promises.

Run from the repo root:
    .venv/bin/python docs/estudo-heimdall/prototipos/collector_async_defect.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO / "src"))

from nokr_qa.logs.collector import collect  # noqa: E402

LINE = "{ts} [vt] INFO c.n.Foo [SANDBOX] - trace_id: [{trace}] - {msg}\n"
STAMP = datetime(2026, 9, 1, 14, 30, 0).strftime("%Y-%m-%d %H:%M:%S")

WEB_DELAY_S = 0.02
WORKER_DELAY_S = 0.15
WAIT_LOGS_MS = 500


def line(trace_id: str, message: str) -> str:
    return LINE.format(ts=STAMP, trace=trace_id, msg=message)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        web = Path(directory) / "web.log"
        worker = Path(directory) / "worker.log"
        web.write_text("", encoding="utf-8")
        worker.write_text("", encoding="utf-8")

        def writer() -> None:
            time.sleep(WEB_DELAY_S)
            web.write_text(line("aaa", "web-hit"), encoding="utf-8")
            time.sleep(WORKER_DELAY_S - WEB_DELAY_S)
            worker.write_text(line("aaa", "worker-hit"), encoding="utf-8")

        threading.Thread(target=writer, daemon=True).start()

        started = time.perf_counter()
        snapshot = collect(
            trace_id="aaa",
            web_log=web,
            worker_log=worker,
            wait_logs_ms=WAIT_LOGS_MS,
            request_at=datetime(2026, 9, 1, 14, 30, 0),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        # Let the late worker line land, then look at the file on disk.
        time.sleep(WORKER_DELAY_S)

        print("=== declared policy ===")
        print(f"  wait_logs_ms           : {WAIT_LOGS_MS}")
        print("  sources                : web (sync), worker (async)")
        print()
        print("=== observed ===")
        print(f"  collect() returned in  : {elapsed_ms:.0f} ms "
              f"(worker line lands at ~{WORKER_DELAY_S * 1000:.0f} ms)")
        print(f"  worker line on disk now: {'worker-hit' in worker.read_text()}")
        print(f"  snapshot.web_lines     : {len(snapshot.web_lines)}")
        print(f"  snapshot.worker_lines  : {len(snapshot.worker_lines)}  {snapshot.worker_lines}")
        print(f"  snapshot.logs_incomplete: {snapshot.logs_incomplete}")
        print()

        defective = not snapshot.worker_lines and not snapshot.logs_incomplete
        print("=== verdict ===")
        if defective:
            print("  DEFECT REPRODUCED: the harness stopped before the worker wrote,")
            print("  collected zero worker lines, and reported logs_incomplete=False.")
            print("  The observability pack will therefore report `skipped: worker logs")
            print("  incomplete` on every async endpoint - the case where cross-service")
            print("  correlation is the whole point.")
            return 1
        print("  no defect: worker lines were collected.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
