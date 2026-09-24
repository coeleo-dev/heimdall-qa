"""Reading the correlation line of one trace out of every declared log source.

The shape of this module is the answer to one measured defect: the collector used
to return as soon as the *web* file answered, so the worker's line — the one the
async steps exist to correlate — was read before it was written, and the absence
was reported as "nothing to check" instead of as a finding. `contrib/architecture.md`,
`## 7. Context propagation` is where that rule is written down.

Three rules follow, and every function here obeys them:

- **Every declared source is read, and each one is waited for until the deadline.**
  A source that answers does not end the wait; only the whole set answering does.
- **The marker is the descriptor's.** `marker` (a regex, with `{trace_id}`
  substituted literally) or `marker_field` (a path into a JSON line) — never a
  literal this module happens to know.
- **The clock decides nothing.** There is no window, no fallback: a line is either
  there or it is not, and the harness says which. `timestamp` orders the entries of
  a step for the reader; it never selects them.
"""

import json
import re
import time
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from heimdall_qa.schema.descriptor import LogSourceSpec

#: How often a source is re-read while waiting. Small enough that a line written
#: mid-wait is seen almost immediately, large enough not to spin a core.
_POLL_S = 0.02

#: The marker a source that declares none is correlated by: the id itself. A source
#: that carries the trace in a shape nobody declared is a source whose marker the
#: project should declare, not one this module should guess at.
_DEFAULT_MARKER = "{trace_id}"

_TRACE = "{trace_id}"

#: Why a source has no line. The reason travels with the evidence because "the file
#: is not there" and "the file is there and says nothing about this trace" are
#: different findings, and the old collector reported both as the same silence.
FOUND = "found"
MARKER_NOT_FOUND = "marker_not_found"
SOURCE_MISSING = "source_missing"


@dataclass(frozen=True)
class LogSourceTarget:
    """One declared source: what it declares, and the file it points at.

    Built by `ProjectView.log_sources()`, which resolves the path; `offset` is the
    step's, and exists so a step cannot read the previous step's lines as its own.
    """

    spec: LogSourceSpec
    path: Path
    offset: int = 0

    @property
    def id(self) -> str:
        return self.spec.id

    def starting_at(self, offset: int) -> "LogSourceTarget":
        return replace(self, offset=offset)


@dataclass(frozen=True)
class LogEntry:
    """One log entry — a line, or a stack trace and the header that opened it."""

    text: str
    #: Which declared source wrote it. The timeline merges sources, so an entry
    #: that could not name its own would have to be looked up by value.
    source: str = ""
    #: When the source stamped it, if the source declares how to read a stamp.
    at: datetime | None = None


@dataclass(frozen=True)
class SourceRead:
    """What one declared source answered for one trace."""

    id: str
    role: str
    propagate: bool
    entries: tuple[LogEntry, ...] = ()
    reason: str = MARKER_NOT_FOUND
    #: Whether the read had to drop bytes to stay inside `max_tail_bytes`.
    truncated: bool = False

    @property
    def found(self) -> bool:
        return bool(self.entries)


@dataclass(frozen=True)
class LogCollection:
    """Every declared source, read once for one step's trace."""

    reads: tuple[SourceRead, ...] = ()
    #: Whether anything was looked at. A project that declares no log source is not
    #: *incomplete*, it is unmeasured, and the packs that read a trace line have to
    #: say so instead of reporting a failure the project never had.
    measured: bool = True

    @property
    def incomplete(self) -> bool:
        """Whether a source the project says carries this trace did not answer.

        A source that declares `propagate: false` is not incomplete when it is
        silent — the project said the trace does not reach it, so nothing is owed.
        """
        return any(
            (not read.found and read.propagate) or read.truncated for read in self.reads
        )

    def lines(self, source_id: str) -> tuple[str, ...]:
        """The text of every entry one source answered with."""
        for read in self.reads:
            if read.id == source_id:
                return tuple(entry.text for entry in read.entries)
        return ()

    def timeline(self) -> tuple[LogEntry, ...]:
        """Every entry of every source, oldest first.

        This is what `timestamp` is for, and its only use: a step that crossed two
        services reads in the order the services wrote, not in the order the files
        were polled. An entry whose stamp the source does not declare — or cannot
        parse — keeps its file position and sorts after the dated ones, because a
        guess at its time would be worse than saying "the end".
        """
        dated = [entry for read in self.reads for entry in read.entries if entry.at]
        undated = [entry for read in self.reads for entry in read.entries if not entry.at]
        return (*sorted(dated, key=lambda entry: entry.at), *undated)


def not_declared() -> LogCollection:
    """The collection for a project with nowhere to look.

    `incomplete` stays false on purpose: nothing was missed, because nothing was
    expected. Named as its own constructor rather than a bare `LogCollection()`
    because the distinction it encodes — *unmeasured* versus *incomplete* — is the
    one thing a caller must not get wrong.
    """
    return LogCollection((), measured=False)


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect(
    *,
    trace_id: str,
    targets: Sequence[LogSourceTarget],
    wait_logs_ms: int = 0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> LogCollection:
    """Read every target, waiting until the sources that owe a line have answered.

    A source that declares `propagate: false` is read on every pass and never
    awaited: there is nothing coming, so waiting for it would spend a step's whole
    budget on a line the project said it does not send.
    """
    if not targets:
        return not_declared()
    plans = [(target, _marker(target.spec, trace_id)) for target in targets]
    deadline = monotonic() + max(wait_logs_ms, 0) / 1000.0
    while True:
        reads = tuple(_read(target, trace_id, marker) for target, marker in plans)
        if not _owed(reads) or monotonic() >= deadline:
            return LogCollection(reads)
        sleep(_POLL_S)


def _owed(reads: Sequence[SourceRead]) -> bool:
    """Whether some source that owes a line has not answered yet."""
    return any(read.propagate and not read.found for read in reads)


def _read(target: LogSourceTarget, trace_id: str, marker: re.Pattern[str]) -> SourceRead:
    if not target.path.is_file():
        return SourceRead(
            target.id, target.spec.role, target.spec.propagate, reason=SOURCE_MISSING
        )
    text, truncated = _tail(target)
    entries = _entries(target.spec, target.id, text, trace_id, marker)
    return SourceRead(
        target.id,
        target.spec.role,
        target.spec.propagate,
        entries=entries,
        reason=FOUND if entries else MARKER_NOT_FOUND,
        truncated=truncated,
    )


def _marker(spec: LogSourceSpec, trace_id: str) -> re.Pattern[str]:
    """The declared marker, with the id substituted where it says `{trace_id}`."""
    pattern = (spec.marker or _DEFAULT_MARKER).replace(_TRACE, re.escape(trace_id))
    return re.compile(pattern)


def _tail(target: LogSourceTarget) -> tuple[str, bool]:
    """The bytes this step may see, newest last.

    `max_tail_bytes` is a ceiling on one read, so a step that writes megabytes is
    not re-read whole every 20 ms. When the ceiling bites, the **end** of the file
    is kept: it is the only part that can hold this step's line, and the bytes the
    offset asked for and the ceiling dropped are what `truncated` reports.
    """
    size = file_size(target.path)
    floor = 0 if size < target.offset else target.offset
    ceiling = max(size - target.spec.max_tail_bytes, floor)
    with target.path.open(encoding="utf-8", errors="replace") as handle:
        handle.seek(ceiling)
        text = handle.read()
    return text, ceiling > floor


def _entries(
    spec: LogSourceSpec,
    source_id: str,
    text: str,
    trace_id: str,
    marker: re.Pattern[str],
) -> tuple[LogEntry, ...]:
    """The entries whose text carries the trace, grouped and stamped."""
    found: list[LogEntry] = []
    for group in _groups(spec, text):
        if not _carries(spec, group, trace_id, marker):
            continue
        found.append(LogEntry(group, source_id, _stamped(spec, group)))
    return tuple(found)


def _groups(spec: LogSourceSpec, text: str) -> list[str]:
    """`text` split into entries, so a stack trace stays with its header.

    A line matching the declared `multiline.start` opens an entry and the lines
    under it belong to it; without a declaration every line is its own entry, which
    is what a single-line logger writes anyway.
    """
    lines = text.splitlines()
    if spec.multiline is None:
        return lines
    opens = re.compile(spec.multiline.start)
    grouped: list[str] = []
    for line in lines:
        if opens.match(line) or not grouped:
            grouped.append(line)
        else:
            grouped[-1] = f"{grouped[-1]}\n{line}"
    return grouped


def _carries(
    spec: LogSourceSpec,
    group: str,
    trace_id: str,
    marker: re.Pattern[str],
) -> bool:
    if spec.marker_field is None:
        return marker.search(group) is not None
    record = _record(group)
    return record is not None and _at_path(record, spec.marker_field) == trace_id


def _record(group: str) -> Mapping[str, object] | None:
    """The first line of a group as a JSON object, or `None` when it is not one."""
    head = group.splitlines()[0] if group else ""
    try:
        loaded = json.loads(head)
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _at_path(record: Mapping[str, object], path: str) -> object | None:
    current: object = record
    for step in path.split("."):
        if not isinstance(current, Mapping) or step not in current:
            return None
        current = current[step]
    return current


def _stamped(spec: LogSourceSpec, group: str) -> datetime | None:
    """When the source says the entry was written, or `None` when it does not say.

    A declaration is needed in both directions: a source without a `timestamp` has
    no readable moment, and this never guesses one from the harness's own clock.
    """
    stamp = spec.timestamp
    if stamp is None:
        return None
    raw = _stamp_text(spec, stamp, group)
    if raw is None:
        return None
    try:
        parsed = datetime.strptime(raw, stamp.format)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed
    if stamp.timezone == "utc":
        return parsed.replace(tzinfo=UTC)
    # `local`: a stamp that carries no offset is read in the host's zone, which is
    # what Python's own `astimezone()` presumes and what the declaration means. It
    # keeps every entry comparable, so two sources with different declarations
    # still sort against each other instead of raising on naive-versus-aware.
    return parsed.astimezone()


def _stamp_text(spec: LogSourceSpec, stamp, group: str) -> str | None:
    if stamp.field is None:
        return _leading_stamp(group, stamp.format)
    record = _record(group)
    if record is None:
        return None
    found = _at_path(record, stamp.field)
    return found if isinstance(found, str) else None


def _leading_stamp(group: str, fmt: str) -> str | None:
    """The stamp at the head of a line, found by the format the source declared.

    A `strptime` format is a shape, so it is turned into the regex that finds how
    far into the line the stamp reaches: `%Y-%m-%d %H:%M:%S` must not be read as
    the first whitespace-delimited word, which is only the date.
    """
    found = _stamp_pattern(fmt).match(group)
    return found.group(0) if found else None


@lru_cache(maxsize=64)
def _stamp_pattern(fmt: str) -> re.Pattern[str]:
    return re.compile(_DIRECTIVES.sub(_directive, re.escape(fmt)))


def _directive(match: re.Match[str]) -> str:
    return _SHAPES.get(match.group(0), re.escape(match.group(0)))


#: What each `strptime` directive may look like, for the regex that finds a stamp.
#: Only the directives a log stamp plausibly uses: an unknown one is matched
#: literally, and `strptime` is still the one that decides if a stamp is valid.
_SHAPES: dict[str, str] = {
    "%Y": r"\d{4}",
    "%y": r"\d{2}",
    "%m": r"\d{1,2}",
    "%d": r"\d{1,2}",
    "%e": r"\s?\d{1,2}",
    "%H": r"\d{1,2}",
    "%I": r"\d{1,2}",
    "%M": r"\d{2}",
    "%S": r"\d{2}",
    "%f": r"\d{1,9}",
    "%p": r"[APap]\.?[Mm]\.?",
    "%z": r"[+-]\d{2}:?\d{2}",
    "%Z": r"[A-Za-z][A-Za-z_/+-]*",
    "%b": r"[A-Za-z]{3}",
    "%B": r"[A-Za-z]+",
    "%a": r"[A-Za-z]{3}",
    "%A": r"[A-Za-z]+",
    "%j": r"\d{3}",
    "%T": r"\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?",
    "%F": r"\d{4}-\d{2}-\d{2}",
    "%%": "%",
}

_DIRECTIVES = re.compile("%.")
