from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import to_dict
from heimdall_qa.http_client import _UNREACHABLE_HINT
from heimdall_qa.run_store import create_run
from heimdall_qa.run_store import link_latest
from heimdall_qa.run_store import write_book
from heimdall_qa.run_store import write_evidence
from heimdall_qa.run_store import write_summary
from heimdall_qa.run_store import write_verdict
from heimdall_qa.runner import RoundStep
from heimdall_qa.runner import StepResult
from heimdall_qa.runner import _auto_verdict
from heimdall_qa.runner import _build_evidence
from heimdall_qa.runner import _build_summary
from heimdall_qa.runner import _load_included_cases
from heimdall_qa.runner import _load_round_file
from heimdall_qa.runner import _matches_dimensions
from heimdall_qa.runner import _prepare_case
from heimdall_qa.runner import _reject_placeholders
from heimdall_qa.runner import _require_coverage
from heimdall_qa.runner import _round_step
from heimdall_qa.runner import execute_step
from heimdall_qa.runner import optional_suite
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.suite_run import SuiteRun
from heimdall_qa.suite_run import VisibleStep
from heimdall_qa.suite_run import visible_steps

_UI_MODES = frozenset({"walk", "review"})


@dataclass(frozen=True)
class QueueItem:
    case_id: str
    status: str
    current: bool = False
    reachable: bool = False


@dataclass(frozen=True)
class SessionView:
    phase: str
    queue: tuple[QueueItem, ...]
    current_step_dir: Path | None
    run_dir: Path | None
    mode: str
    error: dict[str, object] | None = None
    round_id: str = ""
    environment: str = ""
    dimensions: tuple[str, ...] = ()
    progress: tuple[int, int] = (0, 0)
    focus_index: int = 0
    pending_index: int = 0
    can_prev: bool = False
    can_next: bool = False
    awaiting_verdict: bool = False


@dataclass(frozen=True)
class SuitePending:
    label: str
    step_dir: Path
    auto: dict[str, Any]
    failed: bool


class RoundSession:
    def __init__(
        self,
        round_path: Path,
        *,
        root: Path,
        config: HarnessConfig,
        client: httpx.Client,
        runs_dir: Path,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self._round_path = round_path
        self._root = root
        self._config = config
        self._client = client
        self._runs_dir = runs_dir
        self._secrets = secrets or {}
        self._round_file = _load_round_file(round_path)
        project = config.project
        cases = _load_included_cases(self._round_file, root, project)
        _reject_placeholders(cases)
        self._suite = optional_suite(self._round_file, root, project)
        if self._suite is None:
            _require_coverage(cases, root, project)
        self._cases = cases
        selected = [
            case
            for case in cases
            if _matches_dimensions(case, self._round_file.dimensions)
        ]
        self._prepared = [
            _prepare_case(
                case,
                root,
                self._secrets,
                project=project,
                runs_dir=self._runs_dir,
            )
            for case in selected
        ]
        self._mode = ""
        self._phase = "start"
        self._run_dir: Path | None = None
        self._started = 0.0
        self._next_index = 0
        self._pending: tuple[CaseFile, StepResult] | None = None
        self._suite_pending: SuitePending | None = None
        self._records: list[RoundStep] = []
        self._human: list[str] = []
        self._error: dict[str, object] | None = None
        self._ctx: SuiteRun | None = None
        self._captures: dict[str, str] = {}
        self._pacer: dict[str, float] = {}
        self._visible: list[VisibleStep] = []
        self._item_verdicts: list[str] = []
        self._item_dirs: list[Path | None] = []
        self._focus_index = 0

    def start(self, mode: str) -> SessionView:
        if mode not in _UI_MODES:
            raise HarnessError(
                code="MODE_REQUIRES_UI",
                message=f"mode {mode} requires the Fase 7 UI",
                hint="choose walk or review in the review UI, or use heimdall-qa run --mode headless",
            )
        self._mode = mode
        self._started = perf_counter()
        self._run_dir = create_run(self._runs_dir, self._round_file.id)
        shutil.copy(self._round_path, self._run_dir / "round.yaml")
        self._next_index = 0
        self._pending = None
        self._suite_pending = None
        self._records = []
        self._human = []
        self._item_verdicts = []
        self._captures = {}
        self._pacer = {}
        if self._suite is not None:
            self._ctx = SuiteRun(
                suite=self._suite,
                round_file=self._round_file,
                root=self._root,
                config=self._config,
                client=self._client,
                run_dir=self._run_dir,
                secrets=self._secrets,
                captures=self._captures,
                pacer=self._pacer,
            )
            self._visible = visible_steps(self._suite)
        self._item_dirs = [None] * self._step_total()
        self._focus_index = 0
        return self._advance()

    def apply_verdict(
        self,
        status: str,
        comment: str,
        continue_round: bool,
    ) -> SessionView:
        if self._phase != "step" or self._run_dir is None:
            raise HarnessError(
                code="VERDICT_INVALID",
                message="no step is waiting for a verdict",
                hint="start the round and wait for the current step",
            )
        if self._suite_pending is None and self._pending is None:
            raise HarnessError(
                code="VERDICT_INVALID",
                message="no step is waiting for a verdict",
                hint="start the round and wait for the current step",
            )
        if status not in {"pass", "fail"}:
            raise HarnessError(
                code="VERDICT_INVALID",
                message=f"verdict status {status} is not allowed",
                hint="use pass or fail",
            )
        if status == "fail" and not comment.strip():
            raise HarnessError(
                code="VERDICT_INVALID",
                message="reprove requires a comment",
                hint="write why the step failed, then choose Seguir or Parar",
            )
        payload = {
            "status": status,
            "actor": "human",
            "comment": comment.strip(),
            "continue": continue_round,
        }
        if self._suite_pending is not None:
            write_verdict(self._suite_pending.step_dir, payload)
            self._item_verdicts.append(status)
            self._human.append(status)
            self._suite_pending = None
            self._next_index += 1
            if not continue_round or (self._ctx is not None and self._ctx.stopped):
                return self._finish()
            return self._advance()
        case, result = self._pending
        write_verdict(result.step_dir, payload)
        self._records.append(_round_step(case.id, result, status))
        self._human.append(status)
        self._pending = None
        self._next_index += 1
        if not continue_round:
            return self._finish()
        return self._advance()

    def focus(self, index: int) -> SessionView:
        if self._phase != "step":
            return self.view()
        self._focus_index = max(0, min(index, self._max_focus()))
        return self.view()

    def view(self) -> SessionView:
        total = self._step_total()
        highest = self._max_focus()
        awaiting = (
            self._phase == "step"
            and self._focus_index == self._next_index
            and (self._pending is not None or self._suite_pending is not None)
        )
        return SessionView(
            phase=self._phase,
            queue=self._queue(),
            current_step_dir=self._focused_dir(),
            run_dir=self._run_dir,
            mode=self._mode,
            error=self._error,
            round_id=self._round_file.id,
            environment=self._round_file.environment,
            dimensions=tuple(self._round_file.dimensions),
            progress=self._progress(total),
            focus_index=self._focus_index,
            pending_index=self._next_index,
            can_prev=self._phase == "step" and self._focus_index > 0,
            can_next=self._phase == "step" and self._focus_index < highest,
            awaiting_verdict=awaiting,
        )

    def _advance(self) -> SessionView:
        if self._suite is not None:
            return self._advance_suite()
        while self._next_index < len(self._prepared):
            if self._pending is None:
                self._pending = self._execute_current()
            case, result = self._pending
            auto = _auto_verdict(case, result)
            if auto.get("status") != "skip" and (
                self._mode == "walk" or not _should_auto(case, auto)
            ):
                self._phase = "step"
                self._focus_index = self._next_index
                return self.view()
            write_verdict(result.step_dir, auto)
            self._records.append(_round_step(case.id, result, auto))
            self._pending = None
            self._next_index += 1
        return self._finish()

    def _advance_suite(self) -> SessionView:
        assert self._ctx is not None
        while self._next_index < len(self._visible):
            if self._ctx.stopped and self._suite_pending is None:
                return self._finish()
            if self._suite_pending is None:
                self._suite_pending = self._execute_visible()
                self._remember(self._next_index, self._suite_pending.step_dir)
            pending = self._suite_pending
            if self._mode == "walk" or pending.failed:
                self._phase = "step"
                self._focus_index = self._next_index
                return self.view()
            write_verdict(pending.step_dir, pending.auto)
            self._item_verdicts.append(pending.auto["status"])
            self._suite_pending = None
            self._next_index += 1
        return self._finish()

    def _execute_visible(self) -> SuitePending:
        assert self._ctx is not None
        item = self._visible[self._next_index]
        self._drain_begins(item.suite_index)
        if item.loop is not None:
            self._ctx.snapshot_missing()
            outcome = self._ctx.run_loop(item.loop, auto=True)
            self._ctx.records.extend(outcome.records)
            if outcome.stop_suite:
                self._ctx._failed = True
            failed = outcome.stop_suite or outcome.pack_failed
            auto = {
                "status": "fail" if failed else "pass",
                "actor": "auto",
                "comment": "ingest poll still PENDING" if outcome.stop_suite else "",
                "continue": not outcome.stop_suite,
            }
            step_dir = outcome.last_dir or self._run_dir
            assert step_dir is not None
            return SuitePending(item.label, step_dir, auto, failed)
        assert item.probe is not None
        outcome = self._ctx.run_probe(item.probe, auto=False)
        self._ctx.records.append(outcome.record)
        return SuitePending(item.label, outcome.probe_dir, outcome.auto, outcome.failed)

    def _drain_begins(self, suite_index: int) -> None:
        assert self._ctx is not None
        for index, step in enumerate(self._suite.steps if self._suite else []):
            if index >= suite_index:
                break
            if step.probe_begin is not None:
                self._ctx.snapshot_begin(step.probe_begin)

    def _execute_current(self) -> tuple[CaseFile, StepResult]:
        case, contract, baseline = self._prepared[self._next_index]
        result = execute_step(
            case=case,
            contract=contract,
            baseline=baseline,
            config=self._config,
            client=self._client,
            run_dir=self._run_dir,
            step_index=self._next_index + 1,
            run_id=self._round_file.id,
            secrets=self._secrets,
            dimensions=self._round_file.dimensions,
            environment=self._round_file.environment,
            captures=self._captures,
            pacer=self._pacer,
        )
        self._error = _banner_for(result)
        self._remember(self._next_index, result.step_dir)
        return case, result

    def _finish(self) -> SessionView:
        assert self._run_dir is not None
        records = self._ctx.records if self._ctx is not None else self._records
        if self._ctx is not None:
            write_book(self._run_dir, self._ctx.oracle)
        write_summary(
            self._run_dir,
            _build_summary(
                self._round_file,
                self._mode,
                self._cases,
                records,
                self._started,
                self._root,
                project=self._config.project,
                human_reject_rate=_human_reject_rate(self._human),
            ),
        )
        write_evidence(self._run_dir, _build_evidence(records))
        link_latest(self._runs_dir, self._run_dir)
        self._phase = "done"
        self._pending = None
        self._suite_pending = None
        return self.view()

    def _queue(self) -> tuple[QueueItem, ...]:
        if self._suite is not None:
            return self._suite_queue()
        done = {item.case_id: item.verdict for item in self._records}
        items: list[QueueItem] = []
        for index, (case, _contract, _baseline) in enumerate(self._prepared):
            items.append(
                QueueItem(
                    case.id,
                    done.get(case.id, "pending"),
                    self._is_focused(index),
                    self._is_reachable(index),
                )
            )
        return tuple(items)

    def _suite_queue(self) -> tuple[QueueItem, ...]:
        items: list[QueueItem] = []
        for index, visible in enumerate(self._visible):
            if index < len(self._item_verdicts):
                status = self._item_verdicts[index]
            else:
                status = "pending"
            items.append(
                QueueItem(
                    visible.label,
                    status,
                    self._is_focused(index),
                    self._is_reachable(index),
                )
            )
        return tuple(items)

    def _is_focused(self, index: int) -> bool:
        return self._phase == "step" and index == self._focus_index

    def _is_reachable(self, index: int) -> bool:
        return index < len(self._item_dirs) and self._item_dirs[index] is not None

    def _remember(self, index: int, path: Path) -> None:
        if index >= len(self._item_dirs):
            self._item_dirs.extend([None] * (index + 1 - len(self._item_dirs)))
        self._item_dirs[index] = path

    def _focused_dir(self) -> Path | None:
        if 0 <= self._focus_index < len(self._item_dirs):
            stored = self._item_dirs[self._focus_index]
            if stored is not None:
                return stored
        if self._focus_index != self._next_index:
            return None
        if self._suite_pending is not None:
            return self._suite_pending.step_dir
        if self._pending is not None:
            return self._pending[1].step_dir
        return None

    def _max_focus(self) -> int:
        last = -1
        for index, path in enumerate(self._item_dirs):
            if path is not None:
                last = index
        if self._phase == "step":
            last = max(last, self._next_index)
        return max(last, 0)

    def _step_total(self) -> int:
        if self._suite is not None:
            if self._visible:
                return len(self._visible)
            return len(visible_steps(self._suite))
        return len(self._prepared)

    def _progress(self, total: int) -> tuple[int, int]:
        if self._phase == "done":
            return total, total
        if self._phase == "step":
            return self._focus_index + 1, total
        return 0, total


def _should_auto(case: CaseFile, auto: dict[str, Any]) -> bool:
    if auto.get("status") == "skip":
        return True
    if case.gate == "human":
        return False
    return auto["status"] == "pass"


def _human_reject_rate(human: list[str]) -> float:
    if not human:
        return 0.0
    return sum(1 for status in human if status == "fail") / len(human)


def _banner_for(result: StepResult) -> dict[str, object] | None:
    if not result.error:
        return None
    code = "HTTP_TIMEOUT" if "timed out" in result.error else "HTTP_UNREACHABLE"
    return to_dict(
        HarnessError(
            code=code,
            message=result.error,
            hint=_UNREACHABLE_HINT,
        )
    )
