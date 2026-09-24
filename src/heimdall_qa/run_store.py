import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from heimdall_qa.logs.collector import LogCollection
from heimdall_qa.logs.collector import SourceRead
from heimdall_qa.logs.collector import not_declared
from heimdall_qa.oracle import Oracle
from heimdall_qa.packs import PackResult
from heimdall_qa.redact import redact_obj


def create_run(
    runs_dir: Path,
    round_id: str,
    *,
    now: datetime | None = None,
) -> Path:
    stamp = (now or datetime.now()).strftime("%Y-%m-%dT%H%M")
    path = runs_dir / f"{stamp}-{round_id}"
    extra = 2
    while path.exists():
        path = runs_dir / f"{stamp}-{round_id}~{extra}"
        extra += 1
    path.mkdir(parents=True)
    return path


def write_step(
    run_dir: Path,
    step_index: int,
    case_id: str,
    *,
    request: dict[str, Any],
    response: dict[str, Any],
    timing: dict[str, Any],
    packs: list[PackResult],
    logs: LogCollection | None = None,
    patterns: Iterable[str] = (),
) -> Path:
    """One step's evidence: the exchange, the verdicts, and what each log answered.

    The log evidence is **one file per declared source** — `logs-web.txt`,
    `logs-worker.txt`, and whatever else the project declares, including the sources
    this used to drop on the floor. `logs.json` carries what the files cannot: which
    source owed a line and did not answer, and the merged timeline the declared
    timestamps order.

    `patterns` are the project's credential shapes, handed to the redactor so the
    exchange leaves behind no key of the product's — whatever that product's keys
    are shaped like.
    """
    step_dir = run_dir / "steps" / f"{step_index:03d}-{case_id}"
    step_dir.mkdir(parents=True, exist_ok=True)
    _write_json(step_dir / "request.json", redact_obj(request, patterns))
    _write_json(step_dir / "response.json", redact_obj(response, patterns))
    _write_json(step_dir / "timing.json", timing)
    collected = logs if logs is not None else not_declared()
    _write_json(
        step_dir / "packs.json",
        {
            "results": [asdict(item) for item in packs],
            "logs_incomplete": collected.incomplete,
        },
    )
    _write_json(step_dir / "logs.json", _log_payload(collected))
    for read in collected.reads:
        _write_lines(
            step_dir / f"logs-{read.id}.txt",
            [entry.text for entry in read.entries],
        )
    return step_dir


def _log_payload(collected: LogCollection) -> dict[str, Any]:
    return {
        "measured": collected.measured,
        "incomplete": collected.incomplete,
        "sources": [_source_payload(read) for read in collected.reads],
        "timeline": [
            {"source": entry.source, "at": _iso(entry.at), "text": entry.text}
            for entry in collected.timeline()
        ],
    }


def _source_payload(read: SourceRead) -> dict[str, Any]:
    """One source's answer, `reason` included.

    The reason is written down because "the file is not there", "the file says
    nothing about this trace" and "the project declared the trace does not reach
    it" are three different findings that used to render as the same emptiness.
    """
    return {
        "id": read.id,
        "role": read.role,
        "propagate": read.propagate,
        "found": read.found,
        "reason": read.reason,
        "truncated": read.truncated,
        "lines": len(read.entries),
    }


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def write_verdict(step_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(step_dir / "verdict.json", payload)


def write_summary(run_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(run_dir / "summary.json", payload)


def write_book(run_dir: Path, oracle: Oracle) -> None:
    """The book as evidence, whatever the project's oracle decided to record."""
    _write_json(run_dir / "book.json", oracle.as_dict())


def write_probe(run_dir: Path, probe_id: str, name: str, payload: Any) -> Path:
    folder = run_dir / "probes" / probe_id
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / name, payload)
    return folder


SHARED_CAPTURES = "shared-captures.json"


def shared_captures_path(runs_dir: Path) -> Path:
    return runs_dir / SHARED_CAPTURES


def _load_capture_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    loaded: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        text = str(value)
        if text:
            loaded[str(key)] = text
    return loaded


def read_shared_captures(runs_dir: Path) -> dict[str, str]:
    return _load_capture_file(shared_captures_path(runs_dir))


def merge_shared_captures(runs_dir: Path, captures: dict[str, str]) -> None:
    merged = read_shared_captures(runs_dir)
    merged.update({key: value for key, value in captures.items() if value})
    runs_dir.mkdir(parents=True, exist_ok=True)
    _write_json(shared_captures_path(runs_dir), merged)


def write_captures(run_dir: Path, captures: dict[str, str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "captures.json", captures)
    merge_shared_captures(run_dir.parent, captures)


def write_evidence(run_dir: Path, markdown: str) -> None:
    run_dir.joinpath("evidence.md").write_text(markdown, encoding="utf-8")


def link_latest(runs_dir: Path, run_dir: Path) -> None:
    latest = runs_dir / "latest"
    if latest.is_symlink() or latest.is_file():
        latest.unlink()
    latest.symlink_to(run_dir.name, target_is_directory=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_lines(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines)
    if lines:
        text += "\n"
    path.write_text(text, encoding="utf-8")
