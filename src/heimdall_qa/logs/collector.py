from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import re
import time
from collections.abc import Callable

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_POLL_S = 0.02
_WINDOW = timedelta(seconds=1)


@dataclass(frozen=True)
class LogCollection:
    web_lines: list[str]
    worker_lines: list[str]
    logs_incomplete: bool
    fallback: str | None


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect(
    *,
    trace_id: str,
    web_log: Path,
    worker_log: Path,
    wait_logs_ms: int = 0,
    request_at: datetime,
    web_start_offset: int = 0,
    worker_start_offset: int = 0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> LogCollection:
    marker = re.compile(r"trace_id: \[" + re.escape(trace_id) + r"\]")
    deadline = monotonic() + (wait_logs_ms / 1000.0)
    while True:
        web_slice = _read_tail(web_log, web_start_offset)
        worker_slice = _read_tail(worker_log, worker_start_offset)
        web_hit = [line for line in web_slice if marker.search(line)]
        worker_hit = [line for line in worker_slice if marker.search(line)]
        if web_hit:
            return LogCollection(web_hit, worker_hit, False, None)
        if wait_logs_ms <= 0 or monotonic() >= deadline:
            break
        sleep(_POLL_S)
    return _fallback(web_log, worker_log, web_start_offset, worker_start_offset, request_at)


def _fallback(
    web_log: Path,
    worker_log: Path,
    web_offset: int,
    worker_offset: int,
    request_at: datetime,
) -> LogCollection:
    return LogCollection(
        _window(_read_tail(web_log, web_offset), request_at),
        _window(_read_tail(worker_log, worker_offset), request_at),
        True,
        "window_plus_minus_1s",
    )


def _read_tail(path: Path, offset: int) -> list[str]:
    if not path.is_file():
        return []
    size = file_size(path)
    start = 0 if size < offset else offset
    with path.open(encoding="utf-8", errors="replace") as handle:
        handle.seek(start)
        text = handle.read()
    if not text:
        return []
    return text.splitlines()


def _window(lines: list[str], request_at: datetime) -> list[str]:
    pivot = request_at.replace(tzinfo=None) if request_at.tzinfo else request_at
    matched: list[str] = []
    for line in lines:
        stamp = _parse_ts(line)
        if stamp is None:
            continue
        delta = stamp - pivot
        if abs(delta.total_seconds()) <= _WINDOW.total_seconds():
            matched.append(line)
    return matched


def _parse_ts(line: str) -> datetime | None:
    found = _TS.match(line)
    if found is None:
        return None
    return datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S")
