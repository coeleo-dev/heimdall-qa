from __future__ import annotations

import shutil
from collections.abc import Callable
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
    """One row of the round, as the tree, the strip and the step pane read it.

    Two facts and not one, because they answer different questions. `current` is the
    step *on screen* — the pane draws it, and the end-of-run list highlights it.
    `live` is where the run is: `"running"` for the case on the wire right now, the one
    a reviewer watching an automatic stretch is actually waiting on, and `"awaiting"`
    for the one parked on a verdict. They coincide while a `walk` run waits for a
    verdict and while the pane trails the last step that landed; they differ exactly
    when a request is in flight, which is when the difference is worth drawing.

    `live` is a string and not a boolean because the two states are drawn differently
    and because *where* the mark goes is a question only this object's position can
    answer: a suite names the same step six times, so a consumer holding only the case
    id cannot tell which of the six rows the run is on. Whoever paints a row takes the
    mark from the row.

    `current` and `reachable` are read by the pane; `live` is read by the tree.
    """

    case_id: str
    status: str
    current: bool = False
    reachable: bool = False
    live: str = ""

    @property
    def inflight(self) -> bool:
        """The case this row is about is on the wire right now."""
        return self.live == "running"


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
    #: The tree key of each queue row, in queue order, filled in by the workspace — the
    #: session has no tree and does not want one. Empty when nothing is live, which is
    #: what a client reads as "this list is not pointing anywhere yet".
    #:
    #: It exists because a case id is not a row id. A suite lists the same step six
    #: times and a dimension-filtered round lists a subset of what the tree holds, so
    #: "the row for `loop slow ×1`" is not a question the client can answer from the id;
    #: the positional walk that paints the verdicts is the only thing that can, and this
    #: carries its answer across the wire.
    row_keys: tuple[str, ...] = ()


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
        only_case: str | None = None,
        from_case: str | None = None,
        from_step: str | None = None,
        selection: str = "",
        on_progress: Callable[[SessionView], None] | None = None,
    ) -> None:
        self._round_path = round_path
        self._root = root
        self._config = config
        self._client = client
        self._runs_dir = runs_dir
        self._secrets = secrets or {}
        #: Told every time a case is resolved and every time one goes out on the wire.
        #: The engine passes its own publish step here, which is what turns a long
        #: automatic stretch — `review` resolves case after case without returning —
        #: from a frozen window into a run the reviewer can watch. `None` is the CLI
        #: and the tests, and costs one identity check per boundary.
        self._on_progress = on_progress
        #: What this session replays, spelled the way the run directory spells it
        #: (`case-<id>`, `from-<id>`, or nothing for the whole round). It travels into
        #: the run name and into `summary.json`, so a partial run is never mistaken
        #: for the round's verdict.
        self._selection = selection
        self._round_file = _load_round_file(round_path)
        project = config.project
        cases = _load_included_cases(self._round_file, root, project)
        _reject_placeholders(cases)
        self._suite = optional_suite(self._round_file, root, project)
        #: The suite's visible steps this run replays, in order. For a `from_step`
        #: run it is the tail of `visible_steps`; for anything else it is all of
        #: them, and for a non-suite round it stays empty — the queue comes from
        #: `_prepared` instead. Set here, not in `start`, so an unknown step dies
        #: before a run directory exists for it.
        self._visible: list[VisibleStep] = []
        if self._suite is None and from_step is not None:
            raise HarnessError(
                code="CASE_INVALID",
                message=f"{self._round_file.id} is not a suite, so it has no step {from_step}",
                hint="pick a step of a suite round, or a case of this round",
            )
        if self._suite is None:
            # Coverage is checked against the whole round, before any selection
            # narrows it: a single-case run may legitimately skip the kinds it does
            # not include, and the round's own coverage is not the runner's to relax.
            _require_coverage(cases, root, project)
        else:
            # The slice is computed here rather than in `start` so that a step that
            # does not exist fails before a run directory is created for it. It is
            # only a window over the steps: everything else about the suite — the
            # baseline photographs, the pending queue, the records — is unchanged,
            # because a run that starts mid-suite is still a run of that suite.
            self._visible = _select_steps(
                visible_steps(self._suite),
                from_step=from_step,
                round_id=self._round_file.id,
            )
        self._cases = cases
        selected = _select_cases(
            cases,
            only_case=only_case,
            from_case=from_case,
            round_id=self._round_file.id,
        )
        selected = [
            case
            for case in selected
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
        #: The case on the wire, or `None` between cases. It is what makes a `review`
        #: stretch legible: the session used to run case after case with no value
        #: anywhere saying which one was in flight, so the strip could name the round
        #: and nothing else, and the tree had nothing to pulse.
        self._inflight: int | None = None
        #: Whether the step pane follows the run. True from `start` until the reviewer
        #: picks a step by hand: following is what makes the pane show each case as it
        #: lands instead of standing still on the first one for the whole stretch, and
        #: `focus` is where that is given up — browsing a run is not stopping it.
        self._follow = True
        self._pending: tuple[CaseFile, StepResult] | None = None
        self._suite_pending: SuitePending | None = None
        self._records: list[RoundStep] = []
        self._human: list[str] = []
        self._error: dict[str, object] | None = None
        self._ctx: SuiteRun | None = None
        self._captures: dict[str, str] = {}
        self._pacer: dict[str, float] = {}
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
        self._run_dir = create_run(
            self._runs_dir,
            self._round_file.id,
            label=self._selection or None,
        )
        shutil.copy(self._round_path, self._run_dir / "round.yaml")
        self._next_index = 0
        self._pending = None
        self._suite_pending = None
        self._records = []
        self._human = []
        self._item_verdicts = []
        self._captures = {}
        self._pacer = {}
        self._inflight = None
        self._follow = True
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
        self._item_dirs = [None] * self._step_total()
        self._focus_index = 0
        return self._advance()

    def validate_verdict(self, status: str, comment: str) -> None:
        """Whether this verdict can be recorded, without recording it.

        Split out of `apply_verdict` because the engine owns the run now: the verdict
        is applied by the worker thread, so a rejection raised there would surface
        after the browser had been redirected — and the comment the reviewer typed
        would be gone with the page. The request that submits a verdict is the last
        moment the error can still be shown next to the box that caused it.
        """
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

    def apply_verdict(
        self,
        status: str,
        comment: str,
        continue_round: bool,
    ) -> SessionView:
        self.validate_verdict(status, comment)
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
        """Move the step under review.

        Allowed after the round ends as well as during it: the end-of-run list links
        back into the cases that failed, and a finished round is exactly when a
        reviewer wants to reopen one. Bounding the index to what actually ran is what
        keeps that from pointing at a step that was never executed.

        Asking for a step by hand also stops the pane from following the run. That is
        the whole of the "browse while it goes" contract: the reviewer looks at case
        two while case nine is on the wire, and the pane stays on case two until they
        ask for another step or start something else.
        """
        if self._phase not in {"step", "running", "done"}:
            return self.view()
        self._follow = False
        self._focus_index = max(0, min(index, self._max_focus()))
        return self.view()

    def cancel(self) -> SessionView:
        """Stop where the round stands and keep what already ran.

        The step in flight is not undone: its evidence is on disk and its verdict was
        never asked for, so it simply is not counted — the same shape a `walk` run
        stopped by hand leaves behind. `summary.json` and `evidence.md` are written
        for the steps that did decide, because a cancelled run that produced no
        readable artifact would be a run nobody could review.
        """
        if self._phase != "step":
            return self.view()
        self._pending = None
        self._suite_pending = None
        return self._finish()

    def view(self) -> SessionView:
        total = self._step_total()
        focus = self._effective_focus()
        highest = self._max_focus()
        awaiting = (
            self._phase == "step"
            and focus == self._next_index
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
            progress=self._progress(total, focus),
            focus_index=focus,
            pending_index=self._next_index,
            can_prev=self._phase in {"step", "running", "done"} and focus > 0,
            can_next=self._phase in {"step", "running", "done"} and focus < highest,
            awaiting_verdict=awaiting,
        )

    def _advance(self) -> SessionView:
        if self._suite is not None:
            return self._advance_suite()
        while self._next_index < len(self._prepared):
            if self._pending is None:
                # Announced *before* the request goes out, because the wait is most of
                # the time a run takes: a window that only heard about a case once it
                # had finished could not say which one was in flight, and that is the
                # one thing a reviewer watches for. `_phase` is `running` for the whole
                # automatic stretch — it is what the collection overlay keys off to
                # paint verdicts onto the tree while the round is still going — and
                # `_inflight` names the case that is actually on the wire.
                self._phase = "running"
                self._inflight = self._next_index
                self._notify()
                self._pending = self._execute_current()
                self._inflight = None
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
            # And once it is decided: this is the line that moves the counters, the
            # feed and the tree's own verdict for the case that just landed.
            self._phase = "running"
            self._notify()
        return self._finish()

    def _advance_suite(self) -> SessionView:
        assert self._ctx is not None
        while self._next_index < len(self._visible):
            if self._ctx.stopped and self._suite_pending is None:
                return self._finish()
            if self._suite_pending is None:
                self._phase = "running"
                self._inflight = self._next_index
                self._notify()
                self._suite_pending = self._execute_visible()
                self._inflight = None
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
            self._phase = "running"
            self._notify()
        return self._finish()

    def _notify(self) -> None:
        """Tell the watcher where the round has got to.

        Called at every case boundary — once before a case goes out and once after it
        is decided — and it is deliberately the *view* that is handed over rather than
        an event: whoever wants to know has the same question the screen does, and
        answering it twice would be two implementations of "what does this run look
        like right now". A session with no watcher — the CLI, a test — pays one `is
        None` per boundary.
        """
        if self._on_progress is not None:
            self._on_progress(self.view())

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
                selection=self._selection,
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
                    self._live_mark(index),
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
                    self._live_mark(index),
                )
            )
        return tuple(items)

    def _is_focused(self, index: int) -> bool:
        if self._phase not in {"step", "running"}:
            return False
        return index == self._effective_focus()

    def _live_mark(self, index: int) -> str:
        """Where the run is, as one row's own word for it.

        `"running"` is the case on the wire and `"awaiting"` is the one parked on a
        verdict. The second is not `awaiting_verdict` on the view: that flag is about
        the pane — it is only true when the step *on screen* is the pending one, so a
        reviewer who walked back to case two sees a parked run and no form. The row
        that is owed a verdict is the pending one whatever the pane is showing, and
        that is what the tree marks.

        Positional, and deliberately so: a suite's rows share a label, so the index is
        the only thing that can say *which* row is running. Everything that draws a
        row takes the mark off the row for exactly that reason.
        """
        if self._is_inflight(index):
            return "running"
        parked = self._pending is not None or self._suite_pending is not None
        if self._phase == "step" and parked and index == self._next_index:
            return "awaiting"
        return ""

    def _is_inflight(self, index: int) -> bool:
        return self._inflight is not None and index == self._inflight

    def _is_reachable(self, index: int) -> bool:
        return index < len(self._item_dirs) and self._item_dirs[index] is not None

    def _remember(self, index: int, path: Path) -> None:
        if index >= len(self._item_dirs):
            self._item_dirs.extend([None] * (index + 1 - len(self._item_dirs)))
        self._item_dirs[index] = path

    def _effective_focus(self) -> int:
        """Which step the pane is on.

        Following — the default, and the only readable thing during an automatic
        stretch — means the last step that landed. That is deliberately one behind the
        case on the wire: a step directory is written when its exchange is over, so
        pointing at the in-flight case would blank the pane for the length of every
        request and then fill it, which is a flicker, not feedback. What is in flight
        is said in the strip, on the tree row, and by `inflight` on the queue.

        Pinned means the index `focus` last chose, and it does not drift. `_max_focus`
        bounds it to steps that actually ran, so a pin can never point at nothing.
        """
        if not self._follow:
            return self._focus_index
        last = self._last_written()
        return last if last is not None else self._focus_index

    def _last_written(self) -> int | None:
        for index in range(len(self._item_dirs) - 1, -1, -1):
            if self._item_dirs[index] is not None:
                return index
        return None

    def _focused_dir(self) -> Path | None:
        focus = self._effective_focus()
        if 0 <= focus < len(self._item_dirs):
            stored = self._item_dirs[focus]
            if stored is not None:
                return stored
        if focus != self._next_index:
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

    def _progress(self, total: int, focus: int) -> tuple[int, int]:
        """`[step on screen, total]` — the "passo j/m" the strip reads.

        It is the pane's own index and not the count of decided cases: the two are a
        case apart while a request is on the wire, and a readout that disagrees with
        the pane it sits under is a readout nobody trusts. `EngineView.cases_done` is
        the number that counts verdicts, and the progress bar draws it.

        It read `(0, total)` for the whole of a long automatic stretch, which is why
        "passo 0/6" was the only thing the strip could say while six cases ran.
        """
        if self._phase == "start":
            return 0, total
        return min(focus + 1, total), total


def _select_cases(
    cases: list[CaseFile],
    *,
    only_case: str | None,
    from_case: str | None,
    round_id: str,
) -> list[CaseFile]:
    """The slice of a round a case-scoped run replays.

    `from_case` exists because a case is not always runnable alone: A0 mints the JWT
    that A1..A4 spend, and `capture` is how a later case receives it. Running from
    `A1` forward is the smallest scope that can still work, and the alternative —
    re-authenticating behind the author's back — would be the runner inventing a
    setup the corpus never declared.

    The index is taken from the round's include order, which is the order the tree
    shows and the order the cases run in, so "from here" means the same thing to the
    reviewer looking at the tree and to the session that replays it.
    """
    wanted = only_case or from_case
    if wanted is None:
        return cases
    ids = [case.id for case in cases]
    if wanted not in ids:
        raise HarnessError(
            code="CASE_INVALID",
            message=f"{wanted} is not a case of round {round_id}",
            hint="reload the collection and pick the case from the tree",
        )
    start = ids.index(wanted)
    return [cases[start]] if only_case else cases[start:]


def _select_steps(
    visible: list[VisibleStep],
    *,
    from_step: str | None,
    round_id: str,
) -> list[VisibleStep]:
    """The tail of a suite a `from_step` run replays.

    `from_step` exists because a suite's steps are not cases: a loop spends the
    ingest queue and a probe compares against the photograph `probe_begin` took, so
    "the loop and the conference after it" is the smallest slice that still means
    something. The slice starts at the step's *visible* index but the steps it runs
    are everything from its real index on — which is why the runner drains the
    `probe_begin` steps before the first visible one before it starts. Nothing else
    is copied: the suite is replayed by the same `SuiteRun` that would run all of it.
    """
    if from_step is None:
        return list(visible)
    labels = [item.label for item in visible]
    if from_step not in labels:
        raise HarnessError(
            code="CASE_INVALID",
            message=f"{from_step} is not a step of suite {round_id}",
            hint="reload the collection and pick the step from the tree",
        )
    return visible[labels.index(from_step):]


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
