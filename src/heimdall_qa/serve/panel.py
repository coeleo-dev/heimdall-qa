"""What each panel needs, read from the run directory and nothing else.

The client draws three things — the live strip, the collection and the detail — and this
module is the one place that decides what they contain. It reads a step directory and
answers with plain data; `serve/contract.py` renames that data into the models the client
is promised, so the shape the screen draws is declared in exactly one file and checked by
Pydantic on the way out.

Everything here reads a step directory. Nothing here replays anything, which is what
keeps a re-read cheap: the evidence is already on disk by the time a step is on screen.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from heimdall_qa.plan import scopes_for
from heimdall_qa.serve.labels import KIND_LABEL
from heimdall_qa.serve.labels import PHASE_LABEL
from heimdall_qa.serve.labels import SCOPE_LABEL
from heimdall_qa.serve.labels import SCOPE_RUNNING
from heimdall_qa.serve.labels import STATUS_LABEL
from heimdall_qa.serve.labels import STATUS_PILL
from heimdall_qa.serve.labels import STEP_KIND_LABEL
from heimdall_qa.serve.labels import STEP_NOTE
from heimdall_qa.serve.labels import STEP_SCOPE_LABEL
from heimdall_qa.workspace import WorkspaceView

_PACK_ORDER = {"fail": 0, "warn": 1, "skipped": 2, "waived": 3, "pass": 4}
_MONEY_IDS = frozenset({"wallet", "customer_metrics"})
_MONEY_PATHS = frozenset({"balance", "total_billed_lifetime"})

#: The statuses a failing count is made of, worst first. Used by the tree's badges
#: and the end-of-run list, so the number in the tree and the number of rows in the
#: list are the same number.
_FAILING = ("http_5xx", "fail")


def labels() -> dict[str, Any]:
    """The Portuguese words, as the payload the client is handed once per bootstrap.

    Here and not in the client because they are the words the harness speaks: the CLI,
    the machine output and the screen must not disagree about what a status or a scope
    is called, and a `status_label` map in TypeScript would be a second copy of
    `serve/labels.py` that nothing checks.
    """
    return {
        "status_label": STATUS_LABEL,
        "status_pill": STATUS_PILL,
        "kind_label": KIND_LABEL,
        "step_kind_label": STEP_KIND_LABEL,
        "phase_label": PHASE_LABEL,
        "scope_label": SCOPE_LABEL,
        "scope_running": SCOPE_RUNNING,
        "step_note": STEP_NOTE,
        "step_scope_label": STEP_SCOPE_LABEL,
    }


def scopes_of(node: Any) -> tuple[str, ...]:
    """The run buttons the unit card offers for a node."""
    return scopes_for(node)


def scope_labels(node: Any) -> dict[str, str]:
    """What those buttons say, in the words the selected node has earned.

    The scope table is the same for a case and for a suite step — a step offers
    `case_forward` and `round` — but only one word differs: a step's forward button
    must not promise a case that does not exist. Everything else is inherited from
    the shared table, so this overrides rather than restates.
    """
    if not node.step_kind:
        return SCOPE_LABEL
    return {**SCOPE_LABEL, **STEP_SCOPE_LABEL}


def fail_counts(tree: tuple[Any, ...]) -> dict[str, int]:
    """How many descendants of each node are failing, keyed by node key.

    A campaign of 41 rounds is unreadable as a flat list of statuses; the number that
    matters is "how many holes are there under this thing", and that is not a status
    a single node can carry.
    """
    counts: dict[str, int] = {}
    for node in tree:
        counts[node.key] = _count_failing(node)
    return counts


def _count_failing(node: Any) -> int:
    total = 1 if node.status in _FAILING else 0
    for child in node.children:
        total += _count_failing(child)
    return total


# -- panes -----------------------------------------------------------------


def review_session(wv: WorkspaceView) -> Any:
    """The session whose step is on screen: the live one, or the historical run's.

    A historical pane shows a step from a run that is over, so it is the live session
    with its pointers moved to that run's directory and nothing waiting on a verdict.
    Shared by the Jinja pane and the API so the two cannot disagree about which step
    they are showing — which, for a review tool, is the only thing that matters.
    """
    if wv.pane == "historical" and wv.historical_dir is not None:
        return replace(
            wv.session,
            current_step_dir=wv.historical_dir,
            awaiting_verdict=False,
            mode="",
        )
    return wv.session


def done_extra(wv: WorkspaceView) -> dict[str, Any]:
    """The end-of-run numbers, and the cases that are worth reopening.

    A run of 470 cases ends with a wall of identical stat cards today, which answers
    "how did it go" and not "what do I do now". The list is the answer to the second
    question: the cases that failed, each one a link back to its step.
    """
    view = wv.session
    summary: dict[str, Any] = {}
    if view.run_dir is not None:
        summary_path = view.run_dir / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "summary": summary,
        "failed_cases": _failed_cases(view),
        "run_path": str(view.run_dir.resolve()) if view.run_dir else "",
    }


def _failed_cases(view: Any) -> list[dict[str, Any]]:
    """The cases of a finished run that failed, as rows the page can link.

    Read off the session's queue and not off disk: the queue is the order the cases
    ran in, which is the order a reviewer wants to reopen them, and it already knows
    which ones the run decided against.

    `key` is the tree node to reopen, taken by position from the same walk that painted
    the queue. A case id is not a row — a suite lists the same step six times — so the
    link is carried rather than looked up, and a row the walk did not paint comes back
    with an empty key and is drawn as plain text.
    """
    return [
        {
            "case_id": item.case_id,
            "status": item.status,
            "key": view.row_keys[index] if index < len(view.row_keys) else "",
        }
        for index, item in enumerate(view.queue)
        if item.status in _FAILING
    ]


def step_payload(view: Any) -> dict[str, Any]:
    step_dir = view.current_step_dir
    if step_dir is None:
        return {
            **_empty_step(),
            "awaiting_verdict": _awaiting_verdict(view),
        }
    request_doc = _read_json(step_dir / "request.json")
    response_doc = _read_json(step_dir / "response.json")
    timing = _read_json(step_dir / "timing.json")
    packs_payload = _read_json(step_dir / "packs.json")
    results = packs_payload.get("results", []) if isinstance(packs_payload, dict) else []
    packs = _sort_packs(results if isinstance(results, list) else [])
    oracle = _read_json(step_dir / "oracle.json")
    probe = oracle if isinstance(oracle, dict) and oracle.get("surfaces") else {}
    probe_rows = _probe_rows(probe)
    is_probe = bool(probe_rows)
    http_status = response_doc.get("status") if isinstance(response_doc, dict) else None
    log_sources, log_timeline = _log_evidence(step_dir)
    failed = any(item.get("status") == "fail" for item in packs)
    return {
        "request_doc": request_doc,
        "response_doc": response_doc,
        "timing": timing,
        "packs": packs,
        "pack_alerts": [item for item in packs if item.get("status") in {"fail", "warn"}],        "pack_ok": [item for item in packs if item.get("status") not in {"fail", "warn"}],
        "logs_sources": log_sources,
        "logs_timeline": log_timeline,
        "probe": probe,
        "probe_rows": probe_rows,
        "is_probe": is_probe,
        "case_label": _case_label(view, step_dir),
        "http_status": http_status,
        "elapsed_ms": _elapsed_ms(timing),
        "request_method": request_doc.get("method") if isinstance(request_doc, dict) else "",
        "request_url": request_doc.get("url") if isinstance(request_doc, dict) else "",
        "request_headers": request_doc.get("headers") if isinstance(request_doc, dict) else {},
        "request_body": request_doc.get("body") if isinstance(request_doc, dict) else None,
        "response_headers": (
            response_doc.get("headers") if isinstance(response_doc, dict) else {}
        ),
        "response_body": response_doc.get("body") if isinstance(response_doc, dict) else None,
        "has_http": bool(request_doc) or http_status not in {None, ""},
        "pause_reason": _pause_reason(is_probe, packs, view.mode),
        "awaiting_verdict": _awaiting_verdict(view),
        "recorded_verdict": _recorded_verdict(step_dir),
        # A step that failed opens its bodies without being asked; a step that passed
        # does not. The reviewer's first question about a red step is "what came
        # back", and making that a click is making them ask twice.
        "body_open": failed,
    }


def _awaiting_verdict(view: Any) -> bool:
    """Whether *this* step is the one a verdict applies to.

    The rule — "the step on screen is the one a verdict is due on" — is the session's
    own, and `SessionView.awaiting_verdict` already carries it: `session.view()` computes
    it as *phase is step* **and** *the focused step is the pending one* **and** *something
    is actually pending*. So a reviewer who walks back to a case already decided sees no
    verdict form, which is the thing that must not be got wrong: a verdict recorded
    against a case nobody was looking at is silent and attributed to the wrong thing.

    This function exists so that fact has a name on this side of the boundary, and so the
    next reader does not add a second `focus_index == pending_index` check here — the
    duplicate would look harmless and would be the copy that drifts.
    """
    return bool(view.awaiting_verdict)


def _empty_step() -> dict[str, Any]:
    return {
        "request_doc": {},
        "response_doc": {},
        "timing": {},
        "packs": [],
        "pack_alerts": [],
        "pack_ok": [],
        "logs_sources": [],
        "logs_timeline": [],
        "probe": {},
        "probe_rows": [],
        "is_probe": False,
        "case_label": "passo",
        "http_status": None,
        "elapsed_ms": None,
        "request_method": "",
        "request_url": "",
        "request_headers": {},
        "request_body": None,
        "response_headers": {},
        "response_body": None,
        "has_http": False,
        "pause_reason": "",
        "awaiting_verdict": False,
        "recorded_verdict": {},
        "body_open": False,
    }


def _recorded_verdict(step_dir: Path) -> dict[str, Any]:
    payload = _read_json(step_dir / "verdict.json")
    return payload if isinstance(payload, dict) else {}


def _log_evidence(step_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What each declared source answered, and the trace's merged timeline.

    The metadata comes from `logs.json` because the *reason* a source is empty is
    the whole point: the page has to say "the file is not there" and "the project
    declared the trace does not reach it", not render both as a blank panel.
    """
    payload = _read_json(step_dir / "logs.json")
    sources = payload.get("sources", []) if isinstance(payload, dict) else []
    timeline = payload.get("timeline", []) if isinstance(payload, dict) else []
    rows: list[dict[str, Any]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        rows.append(
            {
                **source,
                "text": _read_text(step_dir / f"logs-{source.get('id', '')}.txt"),
            }
        )
    return rows, [item for item in timeline if isinstance(item, dict)]


def _case_label(view: Any, step_dir: Path) -> str:
    for item in view.queue:
        if item.current:
            return item.case_id
    return step_dir.name


def _sort_packs(results: list[Any]) -> list[dict[str, Any]]:
    packs = [item for item in results if isinstance(item, dict)]
    return sorted(
        packs,
        key=lambda item: _PACK_ORDER.get(str(item.get("status")), 9),
    )


def _elapsed_ms(timing: Any) -> int | None:
    """The step's own timing as a number, or `None` when it recorded none.

    A number and not the display string it used to be: the string existed because a
    template wants `{% if elapsed_ms %}`, and the client wants to compare two steps.
    Formatting belongs where the digits are drawn.
    """
    if not isinstance(timing, dict):
        return None
    raw = timing.get("elapsed_ms")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(round(raw))


def _pause_reason(is_probe: bool, packs: list[dict[str, Any]], mode: str) -> str:
    """Why this step is on screen. Distinct from the mode selector's own wording on
    purpose: "walk" appears in both, and the step's reason is about this case."""
    if is_probe:
        return "Conferência de valores (probe)"
    failed = [item for item in packs if item.get("status") == "fail"]
    if failed:
        pack_id = failed[0].get("pack_id", "pack")
        return f"Pack {pack_id} falhou"
    if mode == "walk":
        return "walk — pausa a cada caso"
    return ""


def _probe_rows(probe: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for surface in probe.get("surfaces") or []:
        if not isinstance(surface, dict):
            continue
        money = _is_money_surface(surface)
        rows.append(
            {
                **surface,
                "money": money,
                "matched": _surface_matched(surface),
            }
        )
    return rows


def _is_money_surface(surface: dict[str, Any]) -> bool:
    surface_id = str(surface.get("id") or "")
    path = str(surface.get("jsonpath") or "").rsplit(".", 1)[-1]
    return surface_id in _MONEY_IDS or path in _MONEY_PATHS


def _surface_matched(surface: dict[str, Any]) -> bool:
    expect = surface.get("expect")
    before = surface.get("before")
    after = surface.get("after")
    if expect == "increase":
        return _as_float(after) > _as_float(before)
    if expect == "unchanged":
        return str(before) == str(after)
    return str(surface.get("esperado")) == str(surface.get("lido"))


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
