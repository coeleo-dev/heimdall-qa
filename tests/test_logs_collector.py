from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
import threading
import time

from nokr_qa.logs.collector import collect
from nokr_qa.logs.collector import file_size

LINE = (
    "{ts} [vt] {level} c.n.Foo [SANDBOX] - trace_id: [{trace}] - {msg}\n"
)


def _line(
    trace: str,
    *,
    ts: datetime | None = None,
    level: str = "INFO ",
    msg: str = "ok",
) -> str:
    stamp = (ts or datetime(2026, 9, 1, 14, 30, 0)).strftime("%Y-%m-%d %H:%M:%S")
    return LINE.format(ts=stamp, level=level, trace=trace, msg=msg)


def test_collect_aaa_excludes_bbb(tmp_path: Path):
    web = tmp_path / "nokr-web.log"
    worker = tmp_path / "nokr-worker.log"
    web.write_text(_line("aaa") + _line("bbb"), encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    snapshot = collect(
        trace_id="aaa",
        web_log=web,
        worker_log=worker,
        request_at=datetime(2026, 9, 1, 14, 30, 0),
    )
    joined = "".join(snapshot.web_lines)
    assert "trace_id: [aaa]" in joined
    assert "trace_id: [bbb]" not in joined
    assert snapshot.logs_incomplete is False


def test_offset_ignores_old_aaa(tmp_path: Path):
    web = tmp_path / "nokr-web.log"
    worker = tmp_path / "nokr-worker.log"
    prefix = _line("aaa", msg="old")
    web.write_text(prefix, encoding="utf-8")
    offset = file_size(web)
    web.write_text(prefix + _line("aaa", msg="new"), encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    snapshot = collect(
        trace_id="aaa",
        web_log=web,
        worker_log=worker,
        request_at=datetime(2026, 9, 1, 14, 30, 0),
        web_start_offset=offset,
    )
    joined = "".join(snapshot.web_lines)
    assert "new" in joined
    assert "old" not in joined


def test_wait_logs_ms_picks_up_late_line(tmp_path: Path):
    web = tmp_path / "nokr-web.log"
    worker = tmp_path / "nokr-worker.log"
    web.write_text("", encoding="utf-8")
    worker.write_text("", encoding="utf-8")

    def writer() -> None:
        time.sleep(0.05)
        web.write_text(_line("aaa"), encoding="utf-8")

    threading.Thread(target=writer, daemon=True).start()
    snapshot = collect(
        trace_id="aaa",
        web_log=web,
        worker_log=worker,
        wait_logs_ms=200,
        request_at=datetime(2026, 9, 1, 14, 30, 0),
    )
    assert any("trace_id: [aaa]" in line for line in snapshot.web_lines)


def test_fallback_window_excludes_far_line(tmp_path: Path):
    web = tmp_path / "nokr-web.log"
    worker = tmp_path / "nokr-worker.log"
    request_at = datetime(2026, 9, 1, 14, 30, 0, tzinfo=timezone.utc)
    near = request_at.replace(tzinfo=None)
    far = near + timedelta(seconds=5)
    web.write_text(_line("zzz", ts=near, msg="near") + _line("zzz", ts=far, msg="far"), encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    snapshot = collect(
        trace_id="aaa",
        web_log=web,
        worker_log=worker,
        request_at=request_at,
    )
    joined = "".join(snapshot.web_lines)
    assert "near" in joined
    assert "far" not in joined
    assert snapshot.logs_incomplete is True
    assert snapshot.fallback == "window_plus_minus_1s"


def test_shrunk_file_resets_offset_to_zero(tmp_path: Path):
    web = tmp_path / "nokr-web.log"
    worker = tmp_path / "nokr-worker.log"
    web.write_text(_line("aaa", msg="rotated"), encoding="utf-8")
    worker.write_text("", encoding="utf-8")
    snapshot = collect(
        trace_id="aaa",
        web_log=web,
        worker_log=worker,
        request_at=datetime(2026, 9, 1, 14, 30, 0),
        web_start_offset=10_000,
    )
    assert any("rotated" in line for line in snapshot.web_lines)
