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

from heimdall_qa.collection import TreeNode
from heimdall_qa.collection import round_nodes
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

    Two sources behind one shape, and the order matters. A round the engine is still
    holding (its run directory is the one on the session) is read from the session's
    queue: that is the only thing that knows which *row* a step painted, and a suite
    lists the same step six times. Any other round — one selected after the engine
    moved on, or one whose plan ran before the app opened — is read from the run
    directory the tree already indexed, because the queue is gone and disk is what is
    left. Without that second half, of a campaign's forty rounds exactly one could show
    what its run said.
    """
    view = wv.session
    if view.run_dir is not None:
        summary = _read_json(view.run_dir / "summary.json")
        return {
            "summary": summary if isinstance(summary, dict) else {},
            "failed_cases": _failed_cases(view),
            "run_path": str(view.run_dir.resolve()),
        }
    node = wv.selected
    run = node.run if node.kind == "round" else None
    if run is None:
        return {"summary": {}, "failed_cases": [], "run_path": ""}
    return {
        "summary": run.summary,
        "failed_cases": failed_steps(run.path, node),
        "run_path": str(run.path.resolve()),
    }


def failed_steps(run_dir: Path, node: TreeNode) -> list[dict[str, Any]]:
    """The steps of a finished run that failed, as rows the pane can link.

    Read off the run directory because this is the path taken for a round the engine
    is no longer holding: its queue is gone and the evidence on disk is what is left.
    Steps come first and probes after, each in the run's own order, which is the order
    the reviewer watched them happen.

    `key` is resolved by walking the round's own cases *forward* and never by looking
    an id up in a map: a suite lists the same step label six times, and a lookup would
    hand every one of those rows the first one's link. Walking the two lists together
    gives the third loop the third case's row. A step that resolves to nothing — a run
    whose round changed shape since — comes back with an empty key and its directory
    instead of a button that opens the wrong case.
    """
    cases = node.children if node.kind == "round" else ()
    cursor = 0
    rows: list[dict[str, Any]] = []
    for step_dir in _step_dirs(run_dir):
        verdict = _read_json(step_dir / "verdict.json")
        status = str(verdict.get("status") or "") if isinstance(verdict, dict) else ""
        if status not in _FAILING:
            continue
        case_id = _step_case_id(step_dir)
        key = ""
        next_cursor = None
        for index in range(cursor, len(cases)):
            if cases[index].case_id == case_id:
                key, next_cursor = cases[index].key, index + 1
                break
        if next_cursor is not None:
            cursor = next_cursor
        rows.append(
            {
                "case_id": case_id,
                "status": status,
                "key": key,
                "reason": _failed_packs(step_dir),
                "step_dir": str(step_dir.resolve()),
            }
        )
    return rows


def _step_dirs(run_dir: Path) -> list[Path]:
    """Every step directory of a run, in the order the run wrote them."""
    found: list[Path] = []
    for parent in (run_dir / "steps", run_dir / "probes"):
        if not parent.is_dir():
            continue
        found.extend(sorted(child for child in parent.iterdir() if child.is_dir()))
    return found


def _step_case_id(step_dir: Path) -> str:
    """The case id a step directory carries in its name.

    `write_step` names a step `<index:03d>-<case id>`, and a probe's folder is the
    probe's own id. A queue spells a probe `probe <id>`, so that is what this answers,
    which is what makes a probe row match the round's probe row.
    """
    if step_dir.parent.name == "probes":
        return f"probe {step_dir.name}"
    name = step_dir.name
    return name.split("-", 1)[1] if "-" in name else name


def _failed_packs(step_dir: Path) -> str:
    """Which packs decided against this step, named so the row says why."""
    payload = _read_json(step_dir / "packs.json")
    results = payload.get("results", []) if isinstance(payload, dict) else []
    ids = [
        str(item.get("pack_id"))
        for item in results
        if isinstance(item, dict) and item.get("status") == "fail"
    ]
    return ", ".join(ids)


def rollup_extra(node: TreeNode) -> dict[str, Any]:
    """A campaign, folder, directory or project's rounds, as one table and its totals.

    The rows are the tree's own rounds in the tree's own order, each read from the run
    its node was indexed with — no second sweep of `runs/`, and no second opinion about
    which run is the latest.

    The totals are **additive only**: counts of cases, of pack alerts and of runs that
    never happened. Coverage and latency are deliberately *not* summed, because a
    p95 over a campaign is not the average of forty p95s and a harness that printed one
    would be inventing a number it cannot weight. The client shows both per row and
    says so, which is the honest shape of the answer.
    """
    rounds = round_nodes(node)
    units = [round_row(item) for item in rounds]
    return {
        "units": units,
        "totals": _rollup_totals(units),
        "rounds_total": len(units),
        "rounds_run": sum(1 for row in units if row["found"]),
    }


def round_row(node: TreeNode) -> dict[str, Any]:
    """One round's latest run, as a row of the roll-up table.

    Everything here comes off the node's indexed `RoundRun` or off the node itself, so
    a forty-round campaign costs a forty-item walk. `found` is not the same question as
    "is the status green": a round whose run is still going has a directory and no
    result, and the row says `found` with empty counts rather than passing for a run
    that finished.
    """
    run = node.run
    summary = run.summary if run is not None else {}
    latency = summary.get("latency_ms")
    return {
        "key": node.key,
        "label": node.label,
        "round_id": node.round_id or "",
        "endpoint": node.endpoint or "",
        "run_path": str(run.path.resolve()) if run is not None else "",
        "found": run is not None and bool(summary),
        "status": node.status,
        "counts": _int_map(summary.get("counts")),
        "packs": _int_map(summary.get("packs")),
        "coverage_pct": _as_float(summary.get("coverage_pct")),
        "latency_ms": _float_map(latency),
        "mode": str(summary.get("mode") or ""),
        "stamp": run.path.name if run is not None else "",
        "logs_incomplete": _as_int(summary.get("logs_incomplete")),
        "failed": sum(1 for child in node.children if child.status in _FAILING),
    }


def _rollup_totals(units: list[dict[str, Any]]) -> dict[str, int]:
    """The additive half of a roll-up: case counts, pack alerts, misses.

    `not_run` is the rounds a campaign lists that have no completed run — the number a
    reviewer reads as "how far along is this really", which no single status badge can
    carry and which summing anything else would not produce.
    """
    totals = dict.fromkeys(
        ("pass", "fail", "skip", "http_5xx", "instrument", "packs_fail", "packs_warn",
         "logs_incomplete", "not_run"),
        0,
    )
    for row in units:
        counts = row["counts"]
        for name in ("pass", "fail", "skip", "http_5xx", "instrument"):
            totals[name] += counts.get(name, 0)
        packs = row["packs"]
        totals["packs_fail"] += packs.get("fail", 0)
        totals["packs_warn"] += packs.get("warn", 0)
        totals["logs_incomplete"] += int(row.get("logs_incomplete") or 0)
        if not row["found"]:
            totals["not_run"] += 1
    return totals


def _int_map(value: Any) -> dict[str, int]:
    """A counts dict off disk as ints, with anything unreadable dropped.

    A summary is a file a reader may have half-written or a hand might have edited, and
    a roll-up that raised on one odd value would take the whole campaign's table down
    with it. Dropping the key is the honest answer: the count is not zero, it is
    unknown, and it must not silently become a zero in a total.
    """
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _as_int(item)
        for key, item in value.items()
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    }


def _float_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _as_float(item)
        for key, item in value.items()
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    }


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _as_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


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
