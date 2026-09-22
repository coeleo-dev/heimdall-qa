"""Execution of one `ui` step (emenda 11, A.19 / §7.5).

This is the browser analogue of `runner.execute_step`: it drives the screen,
collects the JVM log lines for its own `trace_id` through the **existing**
`logs/collector.py`, runs the transport packs over the response the browser
produced, and writes the A.13 artifacts.

It is deliberately not `execute_step` itself: that function needs a `CaseFile`
contract, a baseline and an HTTP target, none of which a `ui` step has.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from time import monotonic
from typing import Any
from typing import Callable

import httpx

from nokr_qa.browser import UiDriver
from nokr_qa.browser import preflight_dashboard
from nokr_qa.config import HarnessConfig
from nokr_qa.errors import HarnessError
from nokr_qa.logs.collector import collect
from nokr_qa.logs.collector import file_size
from nokr_qa.packs import PackContext
from nokr_qa.packs import PackResult
from nokr_qa.packs import run_all
from nokr_qa.runner import RoundStep
from nokr_qa.runner import _resolve_log
from nokr_qa.run_store import write_ui_step
from nokr_qa.run_store import write_verdict
from nokr_qa.schema.models import UiStep

# Failures that belong to the instrument, not to the product: they are recorded
# as a failed step with cause `instrument` so the run leaves evidence, instead of
# aborting the whole round with a stack trace.
INSTRUMENT_CODES = frozenset(
    {
        "DASHBOARD_DOWN",
        "PLAYWRIGHT_MISSING",
        "UI_NAVIGATION_FAILED",
        "UI_PAGE_TIMEOUT",
        "UI_REGION_MISSING",
        "UI_ARIA_UNAVAILABLE",
        # E3: the harness could not even run the check. A *mismatch* or a
        # violation is a product finding and never lands here.
        "UI_BASELINE_MISSING",
        "A11Y_SCRIPT_MISSING",
        "A11Y_RUN_FAILED",
    }
)


@dataclass(frozen=True)
class UiOutcome:
    """What the suite needs back from one browser step."""

    step_dir: Path
    record: RoundStep
    verdict: dict[str, Any]
    packs: list[PackResult]
    failed: bool


def execute_ui_step(
    step: UiStep,
    *,
    run_dir: Path,
    root: Path,
    step_index: int,
    run_id: str,
    config: HarnessConfig,
    client: httpx.Client,
    driver_factory: Callable[[], UiDriver],
    environment: str,
) -> UiOutcome:
    started = monotonic()
    trace_id = f"nokrqa-{run_id}-{step_index}"
    timeout_ms = step.timeout_ms or config.ui.page_ms
    web_log = _resolve_log(config.log_files.web)
    worker_log = _resolve_log(config.log_files.worker)
    web_start = file_size(web_log)
    worker_start = file_size(worker_log)
    request_at = datetime.now(timezone.utc)

    baseline = _load_baseline(step, root)
    if baseline is _MISSING_BASELINE:
        return _instrument_failure(
            step,
            run_dir=run_dir,
            step_index=step_index,
            trace_id=trace_id,
            reason=(
                f"UI_BASELINE_MISSING: {step.baseline} was declared but does not exist "
                f"under {root}"
            ),
            elapsed_ms=(monotonic() - started) * 1000.0,
        )

    probe = preflight_dashboard(config, client)
    if not probe.reachable:
        return _instrument_failure(
            step,
            run_dir=run_dir,
            step_index=step_index,
            trace_id=trace_id,
            reason=probe.reason,
            elapsed_ms=(monotonic() - started) * 1000.0,
        )

    # The browser is booted only after the preflight: burning Chromium on a dead
    # dashboard would turn a clear `instrument` signal into a startup timeout.
    driver = driver_factory()
    driver.set_trace_id(trace_id)
    try:
        read = driver.read_screen(
            path=step.path,
            wait_for=step.wait_for,
            region=step.region,
            timeout_ms=timeout_ms,
            screenshot=config.ui.screenshot,
            trace_id=trace_id,
            baseline=baseline,
            a11y=config.ui.a11y,
        )
    except HarnessError as err:
        if err.code not in INSTRUMENT_CODES:
            raise
        return _instrument_failure(
            step,
            run_dir=run_dir,
            step_index=step_index,
            trace_id=trace_id,
            reason=f"{err.code}: {err.message}",
            elapsed_ms=(monotonic() - started) * 1000.0,
        )

    snapshot = collect(
        trace_id=trace_id,
        web_log=web_log,
        worker_log=worker_log,
        wait_logs_ms=config.ui.logs_ms,
        request_at=request_at,
        web_start_offset=web_start,
        worker_start_offset=worker_start,
    )
    # Structural packs must run even when the screen produced no traced backend
    # response: the ARIA tree, the console and the a11y scan exist regardless, and
    # E3's whole job is to judge them. `has_primary` tells the transport packs
    # whether they have anything to judge.
    packs = run_all(
        _pack_context(
            step,
            read=read,
            snapshot=snapshot,
            config=config,
            environment=environment,
        )
    )
    failures = [item.pack_id for item in packs if item.status == "fail"]
    verdict = {
        "status": "fail" if failures else "pass",
        "actor": "auto",
        "comment": f"pack {failures[0]} failed" if failures else "",
        "continue": True,
        "cause": "product" if failures else None,
    }
    step_dir = write_ui_step(
        run_dir,
        step_index,
        step.id,
        navigation=_navigation(step, read),
        aria=read.aria,
        a11y=read.a11y,
        values=read.values,
        console=read.console,
        network=read.network,
        screenshot=read.screenshot,
        packs=packs,
        timing={"elapsed_ms": read.elapsed_ms},
        logs_web=snapshot.web_lines,
        logs_worker=snapshot.worker_lines,
        logs_incomplete=snapshot.logs_incomplete,
        log_fallback=snapshot.fallback,
    )
    write_verdict(step_dir, verdict)
    return UiOutcome(
        step_dir=step_dir,
        record=_record(step, read.elapsed_ms, packs, verdict, snapshot.logs_incomplete),
        verdict=verdict,
        packs=packs,
        failed=bool(failures),
    )


def _instrument_failure(
    step: UiStep,
    *,
    run_dir: Path,
    step_index: int,
    trace_id: str,
    reason: str,
    elapsed_ms: float,
) -> UiOutcome:
    """Records a broken instrument as a hard failure — never a SKIP, never a pass."""
    verdict = {
        "status": "fail",
        "actor": "auto",
        "comment": reason,
        "continue": False,
        "cause": "instrument",
    }
    step_dir = write_ui_step(
        run_dir,
        step_index,
        step.id,
        navigation={
            "step": step.id,
            "declared_path": step.path,
            "path": step.path,
            "url": "",
            "region": step.region,
            "trace_id": trace_id,
            "reachable": False,
            "status": None,
            "traces": 0,
            "console_errors": 0,
            "elapsed_ms": elapsed_ms,
            "error": reason,
        },
        aria=f"# unreachable: {reason}\n",
        values={},
        console=[],
        network=[],
        packs=[],
        timing={"elapsed_ms": elapsed_ms},
    )
    write_verdict(step_dir, verdict)
    return UiOutcome(
        step_dir=step_dir,
        record=RoundStep(
            case_id=f"ui {step.id}",
            status_code=0,
            verdict="fail",
            pack_fails=(),
            pack_warns=(),
            elapsed_ms=elapsed_ms,
            logs_incomplete=False,
            cause="instrument",
        ),
        verdict=verdict,
        packs=[],
        failed=True,
    )


def _navigation(step: UiStep, read: Any) -> dict[str, Any]:
    """`ui.json`: what was declared, what was read, and what the backend answered."""
    primary = read.primary or {}
    console_errors = sum(
        1 for item in read.console if str(item.get("type")) in {"error", "pageerror"}
    )
    a11y = read.a11y or {}
    return {
        "step": step.id,
        # Both paths matter: `/auth/login` is declared, `/overview` is where the
        # guard actually lands, and that is the screen whose values were read.
        "declared_path": step.path,
        "path": read.path,
        "url": read.url,
        "region": read.region,
        "trace_id": read.trace_id,
        "reachable": True,
        "status": primary.get("status"),
        "traces": len(read.network),
        "console_errors": console_errors,
        "elapsed_ms": read.elapsed_ms,
        "error": None,
        "primary": primary,
        # The structural comparison is a property of this read; the baseline it
        # compares against stays in the repo and is referenced by path.
        "structure": dict(read.structure or {"enabled": False, "matched": None, "error": None}),
        "a11y": {
            "enabled": bool(a11y.get("enabled")),
            "violations": int(a11y.get("count") or 0),
        },
    }


def _pack_context(
    step: UiStep,
    *,
    read: Any,
    snapshot: Any,
    config: HarnessConfig,
    environment: str,
) -> PackContext:
    primary = read.primary or {}
    status = primary.get("status")
    body = primary.get("body")
    structure = dict(read.structure or {})
    if step.baseline:
        # Recorded here because only the step knows the declared path, and the
        # pack detail must point at the file a reviewer has to edit.
        structure["baseline"] = step.baseline
    return PackContext(
        method=str(primary.get("method") or "GET"),
        url_path=str(primary.get("path") or step.path),
        request_headers={},
        request_body=None,
        status_code=status if isinstance(status, int) else 0,
        response_headers=dict(primary.get("headers") or {}),
        response_text=body if isinstance(body, str) else "",
        elapsed_ms=read.elapsed_ms,
        # The step claims no expected status: it reports the one it observed, so
        # http.baseline judges the backend instead of the harness's declaration.
        expect_status=status if isinstance(status, int) else 0,
        trace_sent=read.trace_id,
        environment=environment,
        # A ClickHouse overview query is not a hot-path transaction; the page
        # timeout is the only budget that means anything here (A.19).
        budget_ms=config.ui.page_ms,
        fail_ms=config.ui.page_ms,
        web_log_lines=snapshot.web_lines,
        worker_log_lines=snapshot.worker_lines,
        logs_incomplete=snapshot.logs_incomplete,
        require_worker_logs=False,
        case_kind="ui",
        # The step's own waives, so a `ui.visual` waiver is scoped to this screen
        # instead of leaking to every `ui` step in the round.
        waives=[item.pack for item in step.waive if item.pack],
        has_primary=bool(primary),
        ui_path=read.path,
        ui_region=read.region,
        ui_console=list(read.console),
        ui_network=list(read.network),
        ui_structure=structure,
        ui_a11y=read.a11y,
    )


_MISSING_BASELINE = object()


def _load_baseline(step: UiStep, root: Path) -> str | None | object:
    """Reads the committed ARIA template, resolved against the run root.

    Returns the template text, `None` when nothing was declared, or the
    `_MISSING_BASELINE` sentinel when the step pointed at a file that is not
    there. The sentinel is deliberate: a declared-but-absent baseline is a broken
    instrument, and collapsing it into `None` would quietly downgrade the check to
    `skipped`.
    """
    if step.baseline is None:
        return None
    path = root / step.baseline
    if not path.is_file():
        return _MISSING_BASELINE
    return path.read_text(encoding="utf-8")


def _record(
    step: UiStep,
    elapsed_ms: float,
    packs: list[PackResult],
    verdict: dict[str, Any],
    logs_incomplete: bool,
) -> RoundStep:
    return RoundStep(
        case_id=f"ui {step.id}",
        status_code=200,
        verdict=str(verdict["status"]),
        pack_fails=tuple(item.pack_id for item in packs if item.status == "fail"),
        pack_warns=tuple(item.pack_id for item in packs if item.status == "warn"),
        elapsed_ms=elapsed_ms,
        logs_incomplete=logs_incomplete,
        cause=verdict.get("cause"),
    )
