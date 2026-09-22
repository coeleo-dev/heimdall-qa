from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from nokr_qa.packs import PackResult
from nokr_qa.redact import redact_obj


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
    logs_web: list[str] | None = None,
    logs_worker: list[str] | None = None,
    logs_incomplete: bool = False,
    log_fallback: str | None = None,
) -> Path:
    step_dir = run_dir / "steps" / f"{step_index:03d}-{case_id}"
    step_dir.mkdir(parents=True, exist_ok=True)
    _write_json(step_dir / "request.json", redact_obj(request))
    _write_json(step_dir / "response.json", redact_obj(response))
    _write_json(step_dir / "timing.json", timing)
    _write_json(
        step_dir / "packs.json",
        {
            "results": [asdict(item) for item in packs],
            "logs_incomplete": logs_incomplete,
            "log_fallback": log_fallback,
        },
    )
    _write_lines(step_dir / "logs-web.txt", logs_web or [])
    _write_lines(step_dir / "logs-worker.txt", logs_worker or [])
    return step_dir


def write_verdict(step_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(step_dir / "verdict.json", payload)


def write_summary(run_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(run_dir / "summary.json", payload)


def write_book(run_dir: Path, book: Any) -> None:
    _write_json(run_dir / "book.json", book.to_dict())


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
