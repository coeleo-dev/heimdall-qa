"""The F5 §3.1 experiment, kept as a test: the wait is per declared source.

The measurement that started this rewrite, kept verbatim — the rule it produced is
in `contrib/architecture.md`, `## 7. Context propagation`:

    worker file NOW contains 'worker-hit': True
    snapshot.worker_lines: []
    snapshot.logs_incomplete: False
    -> policy asked to wait 500ms for the async worker; collected: 0 worker line(s)

The collector returned on the web hit, one poll after the request, while the
consumer that was supposed to settle the request had not written yet — and reported
the absence as "nothing to check". These tests write the worker's line *after* the
web's, on a real clock, and assert the wait outlives the first answer.

The last one is the accept criterion of the phase, read the other way round: when
the wait is not enough, the harness must say so. `fail` on a source that propagates,
`skipped` only when the project declared the trace does not reach it.
"""

import threading
import time
from pathlib import Path

from heimdall_qa.logs.collector import LogSourceTarget
from heimdall_qa.logs.collector import collect
from heimdall_qa.packs import PackContext
from heimdall_qa.packs import run_all
from heimdall_qa.schema.descriptor import LogSourceSpec

LINE = "2026-09-01 14:30:{second:02d} [vt] INFO  c.n.Foo [SANDBOX] - trace_id: [{trace}] - {msg}\n"
MARKER = r"trace_id: \[{trace_id}\]"

#: How late the consumer writes, and how long the step was told to wait for it.
WORKER_DELAY_S = 0.1
WAIT_MS = 500


def _line(trace: str, msg: str, offset_s: float = 0.0) -> str:
    return LINE.format(second=int(offset_s) % 60, trace=trace, msg=msg)


def _source(path: Path, source_id: str, **declared) -> LogSourceTarget:
    return LogSourceTarget(LogSourceSpec(id=source_id, path=str(path), **declared), path)


def _async_writes_later(worker: Path, web: Path) -> None:
    """The normal shape of an async endpoint: 202 now, the consumer settles later."""

    def write() -> None:
        time.sleep(WORKER_DELAY_S)
        worker.write_text(_line("trace-aaa", "settled", 1.0), encoding="utf-8")

    web.write_text(_line("trace-aaa", "accepted"), encoding="utf-8")
    threading.Thread(target=write, daemon=True).start()


def test_the_wait_outlives_the_first_source_that_answers(tmp_path: Path):
    """The defect in one assertion: returning on the web hit loses the worker."""
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    _async_writes_later(worker, web)

    collected = collect(
        trace_id="trace-aaa",
        targets=[
            _source(web, "web", marker=MARKER, role="sync"),
            _source(worker, "worker", marker=MARKER, role="async"),
        ],
        wait_logs_ms=WAIT_MS,
    )

    assert collected.lines("worker"), "the collector gave up on the worker at the web hit"
    assert collected.incomplete is False
    assert collected.reads[0].reason == "found"


def test_a_source_the_project_says_it_does_not_reach_is_not_waited_for(
    tmp_path: Path,
):
    """`propagate: false` is the whole point of being able to say it: no wait.

    A project without the file appender has no worker file at all, and spending the
    step's whole budget waiting for a line nobody promised is how "not instrumented"
    and "not waited for" became the same `skipped`.
    """
    web = tmp_path / "web.log"
    web.write_text(_line("trace-aaa", "accepted"), encoding="utf-8")
    started = time.monotonic()

    collected = collect(
        trace_id="trace-aaa",
        targets=[
            _source(web, "web", marker=MARKER),
            _source(tmp_path / "worker.log", "worker", marker=MARKER, propagate=False),
        ],
        wait_logs_ms=WAIT_MS,
    )

    spent_ms = (time.monotonic() - started) * 1000
    assert spent_ms < WAIT_MS / 2, "a source that propagates nothing was waited for"
    assert collected.incomplete is False


def test_a_declared_absence_is_skipped_with_its_reason(tmp_path: Path):
    """`propagate: false` ⇒ instrument skip, explained — never a product failure."""
    web = tmp_path / "web.log"
    web.write_text(_line("trace-aaa", "accepted"), encoding="utf-8")

    results = _packs(
        collect(
            trace_id="trace-aaa",
            targets=[
                _source(web, "web", marker=MARKER),
                _source(tmp_path / "worker.log", "worker", marker=MARKER, propagate=False),
            ],
        )
    )

    assert results["http.success"].status == "pass"
    assert results["observability"].status == "pass"


def test_an_absence_the_project_did_not_declare_is_a_product_failure(tmp_path: Path):
    """`propagate: true` + no line ⇒ fail. This is the case that was `skipped`."""
    web = tmp_path / "web.log"
    web.write_text("", encoding="utf-8")

    results = _packs(collect(trace_id="trace-aaa", targets=[_source(web, "web", marker=MARKER)]))

    assert results["observability"].status == "fail"
    assert "missing trace_id line in web logs" in results["observability"].detail
    assert results["http.success"].status == "fail"


def test_an_async_source_owes_a_line_only_when_the_step_expects_one(tmp_path: Path):
    """The 208 steps: the same silence, and the verdict depends on the declaration."""
    web = tmp_path / "web.log"
    worker = tmp_path / "worker.log"
    web.write_text(_line("trace-aaa", "accepted"), encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    targets = [
        _source(web, "web", marker=MARKER, role="sync"),
        _source(worker, "worker", marker=MARKER, role="async"),
    ]

    without = _packs(collect(trace_id="trace-aaa", targets=targets), awaits_async=False)
    with_async = _packs(collect(trace_id="trace-aaa", targets=targets), awaits_async=True)

    assert without["observability"].status == "pass"
    assert with_async["observability"].status == "fail"
    assert "missing trace_id line in worker logs" in with_async["observability"].detail


def test_nothing_declared_is_unmeasured_not_failed():
    """A project whose evidence is the HTTP exchange alone is not failing."""
    results = _packs(collect(trace_id="trace-aaa", targets=[]))

    assert results["observability"].status == "skipped"
    assert results["observability"].detail == "no log source declared, nothing to read"
    assert results["http.success"].status == "skipped"


def _packs(collected, *, awaits_async: bool = False):
    """The packs that read logs, run against only the collection."""
    context = PackContext(
        method="POST",
        url_path="/api/ingest",
        request_headers={},
        request_body={},
        status_code=202,
        response_headers={"X-Trace-Id": "trace-aaa"},
        response_text="{}",
        elapsed_ms=1.0,
        expect_status=202,
        trace_sent="trace-aaa",
        environment="sandbox",
        budget_ms=1500,
        fail_ms=1500,
        logs=collected,
        awaits_async=awaits_async,
    )
    return {item.pack_id: item for item in run_all(context)}
