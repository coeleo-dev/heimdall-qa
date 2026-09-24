"""The collector's contract: read every declared source, wait for the ones that owe.

The measurement behind this file is written up in `contrib/architecture.md`,
`## 7. Context propagation`. The collector returned as soon as the *web* file answered, so the worker's
line — written a moment later, always — was read before it existed, and the pack
reported "nothing to check" about the very correlation the step was there to prove.
Every test below is one of the declarations that replaced the guesswork:

- `marker` / `marker_field`: correlation is a declared field, never a literal;
- `multiline.start`: a stack trace stays with the line that opened it;
- `timestamp`: declared, used to order entries and never to select them;
- `max_tail_bytes`: a read is bounded, and a bounded read says so;
- `propagate`: silence is a product failure unless the project declared otherwise.

The one thing no test here asserts is a time window: there is none left to assert.
"""

from datetime import UTC
from datetime import datetime
from pathlib import Path

from heimdall_qa.logs.collector import MARKER_NOT_FOUND
from heimdall_qa.logs.collector import SOURCE_MISSING
from heimdall_qa.logs.collector import LogSourceTarget
from heimdall_qa.logs.collector import collect
from heimdall_qa.logs.collector import file_size
from heimdall_qa.logs.collector import not_declared
from heimdall_qa.schema.descriptor import LogSourceSpec
from heimdall_qa.schema.descriptor import LogTimestampSpec

#: A Logback line, the shape `qa/project.yaml` declares a marker for.
LINE = "2026-09-01 14:30:00 [vt] {level} c.n.Foo [SANDBOX] - trace_id: [{trace}] - {msg}\n"

#: The declared shape of that stamp, and of the line that opens an entry.
STAMP = LogTimestampSpec(format="%Y-%m-%d %H:%M:%S", timezone="local")
MULTILINE = {"start": r"^\d{4}-\d{2}-\d{2} "}
MARKER = r"trace_id: \[{trace_id}\]"


def _line(trace: str, msg: str = "ok", *, level: str = "INFO ") -> str:
    return LINE.format(level=level, trace=trace, msg=msg)


def _source(path: Path, source_id: str = "web", **declared) -> LogSourceTarget:
    """One declared source: the spec, and the file it points at."""
    return LogSourceTarget(LogSourceSpec(id=source_id, path=str(path), **declared), path)


def _write(path: Path, *lines: str) -> Path:
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _line_at(trace: str, stamp: str) -> str:
    """A line whose stamp is the given moment, for the timeline tests."""
    return LINE.format(level="INFO ", trace=trace, msg="ok").replace(
        "2026-09-01 14:30:00", stamp, 1
    )


def test_a_project_with_no_declared_source_is_unmeasured_not_incomplete():
    """Unmeasured and incomplete are different, and the packs branch on which."""
    collected = not_declared()

    assert collected.measured is False
    assert collected.incomplete is False
    assert collected.reads == ()


def test_the_declared_marker_is_the_one_used(tmp_path: Path):
    """A W3C `traceparent` is just another marker, and the wrong one finds nothing.

    This is the test that would have caught the original defect: the marker was
    declared in `qa/project.yaml` and the collector hardcoded the Logback shape,
    so a project using any other one correlated against a literal that never
    appeared — and read the emptiness as "the service did not log".
    """
    w3c = _write(
        tmp_path / "w3c.log", "2026-09-01 14:30:00 c.n.Foo 00-trace-aaa-span-01 - ok\n"
    )
    logback = _write(tmp_path / "logback.log", _line("trace-aaa"))

    as_w3c = collect(trace_id="trace-aaa", targets=[_source(w3c, marker=r"00-{trace_id}-")])
    as_logback = collect(
        trace_id="trace-aaa", targets=[_source(logback, marker=MARKER)]
    )
    wrong_marker = collect(
        trace_id="trace-aaa", targets=[_source(w3c, marker=MARKER)]
    )

    assert as_w3c.reads[0].found
    assert as_logback.reads[0].found
    assert wrong_marker.reads[0].found is False


def test_a_trace_the_source_does_not_carry_is_marker_not_found(tmp_path: Path):
    log = _write(tmp_path / "app.log", _line("trace-bbb"))
    collected = collect(trace_id="trace-aaa", targets=[_source(log)])

    assert collected.reads[0].reason == MARKER_NOT_FOUND
    assert collected.incomplete is True


def test_a_source_that_is_not_there_is_named_as_missing(tmp_path: Path):
    """The file's absence and the line's absence are two findings, not one."""
    collected = collect(trace_id="trace-aaa", targets=[_source(tmp_path / "absent.log")])

    assert collected.reads[0].reason == SOURCE_MISSING
    assert collected.incomplete is True


def test_propagate_false_makes_silence_declared_not_incomplete(tmp_path: Path):
    """What the project said it does not send is not something the harness missed."""
    log = _write(tmp_path / "admin.log", _line("trace-bbb"))
    collected = collect(
        trace_id="trace-aaa", targets=[_source(log, "admin", propagate=False)]
    )

    assert collected.reads[0].found is False
    assert collected.incomplete is False


def test_every_declared_source_is_read_not_only_the_two_known_roles(tmp_path: Path):
    """`admin` was declared in the real descriptor and never collected."""
    web = _write(tmp_path / "web.log", _line("trace-aaa", "served"))
    admin = _write(tmp_path / "admin.log", _line("trace-aaa", "governed"))

    collected = collect(
        trace_id="trace-aaa",
        targets=[_source(web, "web"), _source(admin, "admin")],
    )

    assert [read.id for read in collected.reads] == ["web", "admin"]
    assert collected.lines("admin") == (_line("trace-aaa", "governed").rstrip("\n"),)


def test_an_offset_hides_the_previous_steps_lines(tmp_path: Path):
    log = _write(tmp_path / "app.log", _line("trace-aaa", "old"))
    offset = file_size(log)
    _write(log, _line("trace-aaa", "old"), _line("trace-aaa", "new"))

    collected = collect(trace_id="trace-aaa", targets=[_source(log).starting_at(offset)])

    assert [entry.text for entry in collected.reads[0].entries] == [
        _line("trace-aaa", "new").rstrip("\n")
    ]


def test_an_offset_past_a_shrunk_file_reads_from_the_beginning(tmp_path: Path):
    """Rotation truncates: the lines that are there now are the ones worth having."""
    log = _write(tmp_path / "app.log", _line("trace-aaa", "rotated"))

    collected = collect(trace_id="trace-aaa", targets=[_source(log).starting_at(10_000)])

    assert collected.reads[0].found


def test_multiline_keeps_the_stack_trace_with_its_header(tmp_path: Path):
    log = _write(
        tmp_path / "app.log",
        _line("trace-aaa", "boom", level="ERROR"),
        "\tat com.example.Foo.bar(Foo.java:1)\n",
        "\tat com.example.Baz.qux(Baz.java:2)\n",
        _line("trace-bbb", "other"),
    )
    collected = collect(
        trace_id="trace-aaa",
        targets=[_source(log, marker=MARKER, multiline=MULTILINE)],
    )

    assert len(collected.reads[0].entries) == 1
    assert "Baz.java:2" in collected.reads[0].entries[0].text
    assert "trace-bbb" not in collected.reads[0].entries[0].text


def test_without_a_multiline_declaration_every_line_is_its_own_entry(tmp_path: Path):
    log = _write(
        tmp_path / "app.log",
        _line("trace-aaa", "boom", level="ERROR"),
        "\tat com.example.Foo.bar(Foo.java:1)\n",
    )
    collected = collect(trace_id="trace-aaa", targets=[_source(log, marker=MARKER)])

    assert len(collected.reads[0].entries) == 1
    assert "Foo.java" not in collected.reads[0].entries[0].text


def test_a_declared_timestamp_orders_the_timeline_across_sources(tmp_path: Path):
    """Ordering across services is the whole honest use of a timestamp."""
    web = _write(tmp_path / "web.log", _line_at("trace-aaa", "2026-09-01 14:30:05"))
    worker = _write(tmp_path / "worker.log", _line_at("trace-aaa", "2026-09-01 14:30:01"))

    collected = collect(
        trace_id="trace-aaa",
        targets=[
            _source(web, "web", marker=MARKER, timestamp=STAMP),
            _source(worker, "worker", marker=MARKER, role="async", timestamp=STAMP),
        ],
    )

    assert [entry.source for entry in collected.timeline()] == ["worker", "web"]
    assert collected.timeline()[1].at == datetime(2026, 9, 1, 14, 30, 5).astimezone()


def test_a_source_without_a_declared_timestamp_has_no_moment(tmp_path: Path):
    """The collector never guesses one from its own clock — that was the window."""
    log = _write(tmp_path / "web.log", _line("trace-aaa", "served"))
    collected = collect(trace_id="trace-aaa", targets=[_source(log, marker=MARKER)])

    assert collected.reads[0].entries[0].at is None


def test_a_stamp_read_as_utc_is_aware_even_when_the_line_carries_no_offset(
    tmp_path: Path,
):
    log = _write(tmp_path / "web.log", _line("trace-aaa", "served"))
    collected = collect(
        trace_id="trace-aaa",
        targets=[
            _source(
                log,
                marker=MARKER,
                timestamp=LogTimestampSpec(format="%Y-%m-%d %H:%M:%S", timezone="utc"),
            )
        ],
    )

    assert collected.reads[0].entries[0].at == datetime(
        2026, 9, 1, 14, 30, 0, tzinfo=UTC
    )


def test_a_marker_field_correlates_a_json_line(tmp_path: Path):
    log = _write(
        tmp_path / "billing.jsonl",
        '{"time": "2026-09-01T14:30:00+0000", "traceId": "trace-aaa", "msg": "charged"}\n',
        '{"time": "2026-09-01T14:30:01+0000", "traceId": "trace-bbb", "msg": "other"}\n',
    )
    collected = collect(
        trace_id="trace-aaa",
        targets=[
            _source(
                log,
                "billing",
                format="json-lines",
                marker_field="traceId",
                timestamp=LogTimestampSpec(format="%Y-%m-%dT%H:%M:%S%z", field="time"),
            )
        ],
    )

    assert len(collected.reads[0].entries) == 1
    assert '"msg": "charged"' in collected.reads[0].entries[0].text
    assert collected.reads[0].entries[0].at == datetime(
        2026, 9, 1, 14, 30, tzinfo=UTC
    )


def test_max_tail_bytes_bounds_a_read_and_says_so(tmp_path: Path):
    """A ceiling that bites is reported: the silence may be the harness's own."""
    log = _write(
        tmp_path / "app.log",
        _line("trace-aaa", "served"),
        *(_line("trace-zzz", f"noise {index}") for index in range(400)),
    )

    bounded = collect(
        trace_id="trace-aaa",
        targets=[_source(log, marker=MARKER, max_tail_bytes=200)],
    )
    whole = collect(trace_id="trace-aaa", targets=[_source(log, marker=MARKER)])

    assert bounded.reads[0].found is False
    assert bounded.reads[0].truncated is True
    assert bounded.incomplete is True
    assert whole.reads[0].found is True
