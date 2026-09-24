from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import to_dict
from heimdall_qa.session import RoundSession
from heimdall_qa.session import SessionView
from heimdall_qa.workspace import WorkspaceSession
from heimdall_qa.workspace import WorkspaceView

_TEMPLATES = Path(__file__).parent / "templates"
_STATIC = Path(__file__).parent / "static"
_PACK_ORDER = {"fail": 0, "warn": 1, "skipped": 2, "waived": 3, "pass": 4}
_MONEY_IDS = frozenset({"wallet", "customer_metrics"})
_MONEY_PATHS = frozenset({"balance", "total_billed_lifetime"})


def create_app(
    *,
    session: RoundSession | None = None,
    workspace: WorkspaceSession | None = None,
) -> FastAPI:
    if workspace is None:
        if session is None:
            raise TypeError("create_app requires session or workspace")
        workspace = WorkspaceSession.wrap(session)
    app = FastAPI(
        title="Heimdall QA",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    templates = Jinja2Templates(directory=str(_TEMPLATES))
    templates.env.filters["pretty_json"] = _pretty_json
    templates.env.filters["q"] = lambda value: quote(str(value), safe="")
    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
    app.state.workspace = workspace
    app.state.templates = templates

    @app.get("/", response_class=HTMLResponse)
    def start_page(request: Request, node: str | None = None) -> Any:
        if node:
            try:
                workspace.select(node)
            except HarnessError as err:
                return _render(request, error=to_dict(err), status_code=400)
        return _render(request)

    @app.post("/start")
    def start_round(
        request: Request,
        mode: str = Form(...),
        node: str = Form(""),
    ) -> Any:
        try:
            wv = workspace.start(mode, node or None)
        except HarnessError as err:
            return _render(request, error=to_dict(err), status_code=400)
        return RedirectResponse(_path_for(wv.session), status_code=302)

    @app.get("/round", response_class=HTMLResponse)
    def round_page(request: Request, step: int | None = None) -> Any:
        if step is not None:
            try:
                workspace.focus(step)
            except HarnessError:
                return RedirectResponse("/", status_code=302)
        wv = workspace.view()
        if wv.session.phase != "step":
            return RedirectResponse(_path_for(wv.session), status_code=302)
        return _render(request)

    @app.post("/verdict")
    def submit_verdict(
        request: Request,
        status: str = Form(...),
        comment: str = Form(""),
        continue_round: str = Form("yes"),
    ) -> Any:
        resolved, follows = _resolve_verdict(status, continue_round)
        try:
            wv = workspace.apply_verdict(resolved, comment, follows)
        except HarnessError as err:
            payload = to_dict(err)
            extra = _step_payload(
                workspace.view().session,
                comment_required=_is_comment_error(payload),
            )
            return _render(
                request,
                extra=extra,
                error=payload,
                status_code=400,
            )
        return RedirectResponse(_path_for(wv.session), status_code=302)

    @app.get("/done", response_class=HTMLResponse)
    def done_page(request: Request) -> Any:
        wv = workspace.view()
        if wv.session.phase != "done":
            return RedirectResponse(_path_for(wv.session), status_code=302)
        return _render(request)

    return app


def _path_for(view: SessionView) -> str:
    if view.phase == "done":
        return "/done"
    if view.phase == "step":
        return "/round"
    return "/"


def _render(
    request: Request,
    *,
    extra: dict[str, Any] | None = None,
    error: dict[str, object] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    workspace: WorkspaceSession = request.app.state.workspace
    wv = workspace.view()
    view = wv.session
    payload = extra if extra is not None else _pane_extra(wv)
    current_case_id = next((item.case_id for item in view.queue if item.current), "")
    if wv.selected.kind == "case" and wv.selected.case_id:
        current_case_id = wv.selected.case_id
    awaiting_case_id = ""
    if view.phase == "step" and 0 <= view.pending_index < len(view.queue):
        awaiting_case_id = view.queue[view.pending_index].case_id
    templates: Jinja2Templates = request.app.state.templates
    context = {
        "request": request,
        "workspace": wv,
        "view": view,
        "error": error or wv.error or view.error,
        "run_path": str(view.run_dir.resolve()) if view.run_dir else "",
        "current_case_id": current_case_id,
        "awaiting_case_id": awaiting_case_id,
        **payload,
    }
    return templates.TemplateResponse(
        request,
        "workspace.html",
        context,
        status_code=status_code,
    )


def _pane_extra(wv: WorkspaceView) -> dict[str, Any]:
    if wv.pane in {"review", "historical"}:
        session = wv.session
        if wv.pane == "historical" and wv.historical_dir is not None:
            session = replace(
                session,
                current_step_dir=wv.historical_dir,
                awaiting_verdict=False,
                mode="",
            )
        extra = _step_payload(session)
        if wv.selected.kind == "case" and wv.selected.case_id:
            extra["case_label"] = wv.selected.case_id
        return extra
    if wv.pane == "done":
        return _done_extra(wv.session)
    return _empty_step() | {"summary": {}}


def _done_extra(view: SessionView) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if view.run_dir is not None:
        summary_path = view.run_dir / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {"summary": summary}


def _step_payload(
    view: SessionView,
    *,
    comment_required: bool = False,
) -> dict[str, Any]:
    step_dir = view.current_step_dir
    if step_dir is None:
        return {
            **_empty_step(),
            "comment_required_error": comment_required,
            "awaiting_verdict": view.awaiting_verdict,
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
    return {
        "request_doc": request_doc,
        "response_doc": response_doc,
        "timing": timing,
        "packs": packs,
        "pack_alerts": [item for item in packs if item.get("status") in {"fail", "warn"}],
        "pack_ok": [item for item in packs if item.get("status") not in {"fail", "warn"}],
        "logs_sources": log_sources,
        "logs_timeline": log_timeline,
        "probe": probe,
        "probe_rows": probe_rows,
        "is_probe": is_probe,
        "case_label": _case_label(view, step_dir),
        "http_status": http_status,
        "elapsed_ms": _elapsed_display(timing),
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
        "comment_required_error": comment_required,
        "awaiting_verdict": view.awaiting_verdict,
        "recorded_verdict": _recorded_verdict(step_dir),
    }


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
        "elapsed_ms": "",
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
        "comment_required_error": False,
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


def _case_label(view: SessionView, step_dir: Path) -> str:
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


def _elapsed_display(timing: Any) -> str:
    if not isinstance(timing, dict):
        return ""
    raw = timing.get("elapsed_ms")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return ""
    return str(int(round(raw)))


def _pause_reason(is_probe: bool, packs: list[dict[str, Any]], mode: str) -> str:
    if is_probe:
        return "Conferência de valores (probe)"
    failed = [item for item in packs if item.get("status") == "fail"]
    if failed:
        pack_id = failed[0].get("pack_id", "pack")
        return f"Pack {pack_id} falhou"
    if mode == "walk":
        return "walk — para em todo passo"
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


def _resolve_verdict(status: str, continue_round: str) -> tuple[str, bool]:
    if status == "fail_stop":
        return "fail", False
    if status == "pass":
        return "pass", True
    return "fail", continue_round == "yes"


def _is_comment_error(payload: dict[str, object]) -> bool:
    if payload.get("code") != "VERDICT_INVALID":
        return False
    message = str(payload.get("message") or "").lower()
    return "comment" in message or "reprove" in message


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)
