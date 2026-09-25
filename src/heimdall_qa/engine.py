"""The thing that actually runs, off the request thread.

The review UI used to execute inside its own HTTP handlers: `POST /start` called
`RoundSession.start`, which ran steps until one wanted a human, and only then did
the browser get a response. In `review` mode that is several steps per request; on a
probe it is up to `overview_ms` (30s) of a browser with no spinner, no phase and no
answer to "is it stuck". The reply was the progress report, so there was none.

The engine inverts that. A request starts a plan and returns immediately; a worker
thread owns the run; the UI polls a snapshot. That is also what lets a campaign be
started at all — the request that starts 41 rounds returns in milliseconds, and
closing the tab does not stop the run, because nobody's browser is driving it.

One plan at a time. The invariant the UI used to express with `ROUND_BUSY` ("a round
is waiting for a verdict") generalises to the plan: one worker, one writer, and the
verdict handoff is a condition variable rather than a second thread.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import httpx

from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError
from heimdall_qa.errors import to_dict
from heimdall_qa.plan import RunPlan
from heimdall_qa.plan import RunUnit
from heimdall_qa.projects import ProjectRef
from heimdall_qa.schema.load import resolve_path
from heimdall_qa.session import RoundSession
from heimdall_qa.session import SessionView

#: Terminal phases: the worker is not coming back without a new `start`.
_TERMINAL = frozenset({"done", "cancelled", "error"})

#: Phases in which nothing is executing: safe to start another plan, safe to read.
_SETTLED = frozenset({"idle", "awaiting"}) | _TERMINAL

#: Phases that count as "a run is going".
_BUSY = frozenset({"running", "awaiting"})

#: How many per-case lines the feed keeps. A campaign emits hundreds, and the
#: snapshot is serialised on every poll, so the log is bounded rather than complete.
_EVENT_LIMIT = 400

_DECIDED = ("pass", "fail", "skip")


@dataclass(frozen=True)
class EngineEvent:
    """One line of the live feed, in the order it happened."""

    at: str
    kind: str
    text: str
    status: str = "info"


@dataclass(frozen=True)
class EngineView:
    """Everything the strip needs, and nothing that requires reading a step."""

    phase: str
    scope: str
    plan_label: str
    unit_index: int
    unit_total: int
    unit_label: str
    unit_round: str
    unit_skips: tuple[str, ...]
    case_total: int
    cases_done: int
    step_index: int
    step_total: int
    case_id: str
    run_dir: Path | None
    counts: dict[str, int]
    awaiting: bool
    error: dict[str, object] | None
    events: tuple[EngineEvent, ...]
    elapsed_ms: int
    #: A monotonic counter bumped on every observable change. It is the dirty flag the
    #: event stream waits on: `GET /api/events` blocks on `wait_for_change` instead of
    #: re-sending a snapshot every half second to say nothing happened. `elapsed_ms` is
    #: deliberately not part of it — time passing is not a change.
    revision: int = 0

    @property
    def busy(self) -> bool:
        return self.phase in _BUSY

    @property
    def finished(self) -> bool:
        """The worker is not coming back without a new `start`."""
        return self.phase in _TERMINAL


class RunEngine:
    """A worker thread that walks a `RunPlan`, and a snapshot anyone can read.

    The lock guards the engine's own state and the events deque. The worker is the
    only writer of a run directory, which is what makes passing the shared
    `httpx.Client` to one unit at a time safe.

    The engine holds *fallback* wiring rather than the wiring: with one project open
    the two are the same thing, and with several the project rides on `start` and
    overrides it. `ROUND_BUSY` stays global because the worker does — a second plan
    while the first runs would put two writers on one `runs/`, whichever project it
    came from.
    """

    def __init__(
        self,
        *,
        root: Path,
        config: HarnessConfig,
        client: httpx.Client,
        runs_dir: Path,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self._root = root
        self._config = config
        self._client = client
        self._runs_dir = runs_dir
        self._secrets = secrets or {}
        #: The project the running plan belongs to, adopted at `start`. Read only by
        #: `_run_unit`, which is the one place that has to build a session and therefore
        #: has to know whose root, config, run directory and secrets it is building for.
        self._project: ProjectRef | None = None
        self._lock = threading.RLock()
        self._settled = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._plan: RunPlan | None = None
        self._mode = "review"
        self._session: RoundSession | None = None
        self._session_view: SessionView | None = None
        self._unit: RunUnit | None = None
        self._unit_index = 0
        self._unit_counts: dict[int, dict[str, int]] = {}
        self._phase = "idle"
        self._verdict: tuple[str, str, bool] | None = None
        self._cancel = threading.Event()
        self._error: dict[str, object] | None = None
        self._start_error: HarnessError | None = None
        self._started = 0.0
        #: When the plan stopped, so `elapsed_ms` reports how long it took instead of
        #: how long ago it started. A finished run whose clock keeps climbing is read
        #: as "still going" on every surface that shows it.
        self._stopped = 0.0
        self._events: deque[EngineEvent] = deque(maxlen=_EVENT_LIMIT)
        self._seen: set[str] = set()
        #: Control inputs handed over by the front end, and the highest one the worker
        #: has finished acting on. `awaiting` looks identical before and after a
        #: verdict, so "is it settled" cannot be read from the phase alone — these two
        #: counters are what make `wait_settled` a wait and not a glance. The front end
        #: raises the target; the worker marks it reached at every settled point, and
        #: never ahead of it, which is why a park that happened before a newer verdict
        #: does not count for it.
        self._target = 0
        self._reached = 0
        #: Bumped under `_settled` by `_touch` at every point the observable state
        #: moves. The event stream reads it to decide whether there is anything to
        #: send, which is what makes the stream push rather than poll.
        self._revision = 0

    # -- front end ---------------------------------------------------------

    def revision(self) -> int:
        with self._settled:
            return self._revision

    def wait_for_change(self, after: int, timeout: float) -> int:
        """Block until the state moves past `after`, or the timeout expires.

        Returns the current revision either way, so a caller that timed out simply
        sees the number it passed in and can send a heartbeat. Timeout is the safety
        net: a change this missed would be late, never lost, because the next call
        re-reads the counter rather than trusting a queue of notifications.
        """
        with self._settled:
            self._settled.wait_for(lambda: self._revision != after, timeout=timeout)
            return self._revision

    def _touch(self) -> None:
        """Record that the observable state changed. Callers hold `_settled`."""
        self._revision += 1
        self._settled.notify_all()

    def start(
        self,
        plan: RunPlan,
        mode: str,
        project: ProjectRef | None = None,
    ) -> EngineView:
        """Hand a plan to a worker and return while it runs.

        The one thing worth being strict about: a second plan while the first is
        executing would put two writers on `runs/` and interleave their captures.
        `ROUND_BUSY` is the same answer the UI already knows how to show.

        `project` is whose root, config, run directory and secrets the units are built
        with. Absent it, the engine's own wiring is used, which is what a single-project
        caller has always relied on.
        """
        with self._settled:
            if self._phase in _BUSY:
                raise HarnessError(
                    code="ROUND_BUSY",
                    message="a run is already going",
                    hint="wait for it to finish, or cancel it, before starting another",
                )
            self._plan = plan
            self._mode = mode
            self._project = project
            self._unit_index = 0
            self._unit = None
            self._unit_counts = {}
            self._session = None
            self._session_view = None
            self._verdict = None
            self._error = None
            self._start_error = None
            self._cancel.clear()
            self._events.clear()
            self._seen.clear()
            self._started = time.perf_counter()
            self._stopped = 0.0
            self._phase = "running"
            self._target = 1
            self._reached = 0
            self._event("plan", f"{plan.label}: {len(plan.units)} unidade(s)", "info")
            for unit in plan.skipped:
                self._event("skip", f"{unit.label}: {unit.skip}", "skip")
            self._thread = threading.Thread(
                target=self._work,
                name="heimdall-qa-run",
                daemon=True,
            )
            self._thread.start()
            return self._snapshot()

    def submit_verdict(
        self,
        status: str,
        comment: str,
        continue_round: bool,
    ) -> EngineView:
        """Answer the step the worker is waiting on, and let it move on."""
        with self._settled:
            if self._phase != "awaiting":
                raise HarnessError(
                    code="VERDICT_INVALID",
                    message="no step is waiting for a verdict",
                    hint="start a run and wait for the step that wants one",
                )
            # Validated on this thread, before the worker is woken: "reprove requires
            # a comment" belongs on the page that submitted the verdict, with the box
            # still holding what the reviewer wrote. Validating in the worker would
            # raise after the redirect and lose it.
            if self._session is not None:
                self._session.validate_verdict(status, comment)
            self._verdict = (status, comment, continue_round)
            self._target += 1
            self._touch()
            self._settled.notify_all()
            return self._snapshot()

    def cancel(self) -> EngineView:
        """Ask the worker to stop after the step in flight.

        A step already on the wire is not interrupted — there is no safe way to
        abandon an HTTP exchange whose evidence is half written — so this settles
        between steps: immediately while awaiting a verdict, and at the next case
        boundary while running. What already ran keeps its summary and its evidence,
        which is the same contract a `walk` run stopped by hand has.
        """
        with self._settled:
            if self._phase not in _BUSY:
                return self._snapshot()
            self._cancel.set()
            self._verdict = None
            self._target += 1
            self._touch()
            self._settled.notify_all()
            return self._snapshot()

    def focus(self, index: int) -> EngineView:
        """Move the step under review, back or forward, within the live round."""
        session = self._session
        if session is not None:
            session.focus(index)
            self._publish(session.view(), self._unit)
        return self.snapshot()

    def snapshot(self) -> EngineView:
        with self._settled:
            return self._snapshot()

    def session_view(self) -> SessionView | None:
        """The live round's own view, for callers that render its queue and steps."""
        with self._settled:
            return self._session_view

    def wait_settled(self, timeout: float | None = None) -> EngineView:
        """Block until every handed-over input has been acted on and nothing runs.

        This is the seam that keeps the suite honest. A test starts a plan, answers a
        verdict, cancels — and waits here instead of sleeping and hoping, because the
        worker is a thread and the alternative to this method is a `sleep`.

        Reading the phase alone would not be enough: the engine is `awaiting` both
        before a verdict is submitted and after the worker has consumed it, and the
        two are a world apart to whoever is about to assert on the next step. The
        acknowledgement count is what tells them apart.
        """
        with self._settled:
            self._settled.wait_for(
                lambda: self._phase in _SETTLED and self._reached >= self._target,
                timeout=timeout,
            )
            return self._snapshot()

    def _settle(self) -> None:
        """The worker reached a settled state with everything so far acted on."""
        self._reached = self._target
        self._touch()

    # -- worker ------------------------------------------------------------

    def _end(self, phase: str) -> None:
        """The worker's last word: the phase, and the clock stopped with it.

        Every exit from the worker goes through here rather than assigning `_phase`
        directly, because the clock is part of what "finished" means. `elapsed_ms`
        that keeps counting after the phase turns terminal makes a done run look
        busy on the strip and, on the campaign roll-up, look live enough to hide the
        Start buttons behind "a plan is already running here".
        """
        self._phase = phase
        self._stopped = time.perf_counter()

    def _work(self) -> None:
        try:
            self._walk()
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            with self._settled:
                self._error = to_dict(_as_harness_error(exc))
                self._end("error")
                self._event("error", str(exc), "fail")
        finally:
            with self._settled:
                if self._phase == "running":
                    self._end("done")
                self._settle()

    def _walk(self) -> None:
        assert self._plan is not None
        for index, unit in enumerate(self._plan.units):
            if self._cancel.is_set():
                self._finish_cancelled()
                return
            with self._settled:
                self._unit_index = index
                self._unit = unit
            if not unit.runnable:
                # Already announced at `start`; the reason is part of the plan, so
                # the strip can show every hole before the first request goes out.
                continue
            self._run_unit(unit)
            if self._cancel.is_set():
                self._finish_cancelled()
                return
        with self._settled:
            if not self._unit_counts and self._plan.runnable:
                # Every runnable unit failed to start. `done` would read as "it ran and
                # finished", and the honest answer is the reason the collection could
                # not see. One unit failing out of forty is different: that campaign did
                # run, and the failure is already a line in the feed.
                self._error = to_dict(self._start_error or _nothing_ran(self._plan))
                self._end("error")
                self._touch()
                return
            self._end("done")
            self._event("done", f"{self._plan.label}: fim", "info")

    def _run_unit(self, unit: RunUnit) -> None:
        with self._settled:
            self._event("unit", f"{unit.label} — início", "info")
            project = self._project
        # Captured under the lock into locals before the session is built. A plan that
        # starts while this one is still winding down must not be able to swap the
        # wiring out from under a unit that is halfway through resolving its round.
        root = project.root if project is not None else self._root
        config = project.config if project is not None else self._config
        runs_dir = project.runs_dir if project is not None else self._runs_dir
        secrets = dict(project.secrets) if project is not None else self._secrets
        session = RoundSession(
            resolve_path(root, unit.round, config.project),
            root=root,
            config=config,
            client=self._client,
            runs_dir=runs_dir,
            secrets=secrets,
            only_case=unit.only_case,
            from_case=unit.from_case,
            from_step=unit.from_step,
            selection=unit.selection,
            # The window's own publish step, wired straight into the session's case
            # boundaries. Without it a `review` run of a forty-case round published
            # twice — one frame when it started and one when it ended — and the twelve
            # minutes in between were a frozen screen. The callback runs on this
            # worker thread, which is the thread that owns the session, and `_publish`
            # takes the lock itself, so nothing here is re-entrant.
            on_progress=lambda view: self._publish(view, unit),
        )
        with self._settled:
            self._session = session
        view = self._guarded_start(session, unit)
        if view is None:
            return
        self._publish(view, unit)
        while view.phase == "step":
            verdict = self._await_verdict()
            if verdict is None:
                view = session.cancel()
                self._publish(view, unit)
                self._store_unit(unit, view)
                return
            view = session.apply_verdict(*verdict)
            self._publish(view, unit)
        self._store_unit(unit, view)

    def _guarded_start(self, session: RoundSession, unit: RunUnit) -> SessionView | None:
        """Start one unit, and let a broken round cost only itself.

        The collection already refuses to start a not-ready round, so a failure here
        is something the tree could not see. Failing the whole campaign for it would
        throw away the rounds that did run, and the reason is reported as an event. If
        it happens to *every* unit, `_walk` turns the empty run into an error — an
        event list is not where a reviewer looks for "nothing ran".
        """
        try:
            return session.start(self._mode)
        except HarnessError as err:
            with self._settled:
                self._start_error = err
                self._event("error", f"{unit.label}: {err.message}", "fail")
            return None

    def _await_verdict(self) -> tuple[str, str, bool] | None:
        with self._settled:
            if self._cancel.is_set():
                self._settle()
                return None
            self._phase = "awaiting"
            # Parked: the worker is waiting for a human, which is a settled state.
            self._settle()
            self._settled.wait_for(
                lambda: self._verdict is not None or self._cancel.is_set()
            )
            self._phase = "running"
            # Consumed: whatever was handed over has been acted on.
            self._settle()
            if self._cancel.is_set() or self._verdict is None:
                return None
            verdict = self._verdict
            self._verdict = None
            return verdict

    def _store_unit(self, unit: RunUnit, view: SessionView) -> None:
        counts = _decided(view)
        with self._settled:
            self._unit_counts[self._unit_index] = counts
            self._phase = "running"
            self._event(
                "result",
                f"{unit.label} — pass={counts.get('pass', 0)} fail={counts.get('fail', 0)}",
                "fail" if counts.get("fail") else "pass",
            )
            self._settled.notify_all()

    def _finish_cancelled(self) -> None:
        session = self._session
        if session is not None and session.view().phase == "step":
            view = session.cancel()
            self._publish(view, self._unit)
            self._store_unit(_unit_or_blank(self._unit), view)
        with self._settled:
            self._end("cancelled")
            self._event("cancelled", "cancelado", "skip")

    # -- state -------------------------------------------------------------

    def _publish(self, view: SessionView, unit: RunUnit | None) -> None:
        """Adopt a session view and turn the cases it just resolved into feed lines.

        The queue is the only live source of per-case outcomes — `summary.json` is
        written when the round ends — so the diff against what was already seen is
        what makes a case's result appear while the round is still going.
        """
        with self._settled:
            self._session_view = view
            for item in view.queue:
                if item.status.lower() not in {"pass", "fail", "skip"}:
                    continue
                key = f"{unit.label if unit else ''}:{item.case_id}"
                if key in self._seen:
                    continue
                self._seen.add(key)
                self._event(
                    "case",
                    f"{item.case_id} — {item.status.lower()}",
                    item.status.lower(),
                )
            # Not only the feed lines: adopting a view also moves the step under
            # review, which `focus` changes without emitting an event. Touching here
            # is what makes a focus move visible to the stream.
            self._touch()

    def _event(self, kind: str, text: str, status: str) -> None:
        self._events.append(
            EngineEvent(
                at=time.strftime("%H:%M:%S"),
                kind=kind,
                text=text,
                status=status,
            )
        )
        # Every feed line is also a state change, which is why this is the one place
        # the revision advances for the common case. Callers hold `_settled`.
        self._touch()

    def _snapshot(self) -> EngineView:
        plan = self._plan
        view = self._session_view
        total = len(plan.units) if plan else 0
        counts = self._totals()
        step_index, step_total = view.progress if view is not None else (0, 0)
        return EngineView(
            phase=self._phase,
            scope=plan.scope if plan else "",
            plan_label=plan.label if plan else "",
            unit_index=self._unit_index + 1 if total else 0,
            unit_total=total,
            unit_label=self._unit.label if self._unit else "",
            unit_round=self._unit.round if self._unit else "",
            unit_skips=tuple(
                f"{unit.label}: {unit.skip}" for unit in (plan.skipped if plan else ())
            ),
            case_total=plan.case_total if plan else 0,
            cases_done=sum(counts.get(name, 0) for name in _DECIDED),
            step_index=step_index,
            step_total=step_total,
            case_id=_focused_case(view),
            run_dir=view.run_dir if view is not None else None,
            counts=counts,
            awaiting=self._phase == "awaiting",
            error=self._error,
            events=tuple(self._events),
            elapsed_ms=self._elapsed_ms(),
            revision=self._revision,
        )

    def _elapsed_ms(self) -> int:
        """How long the plan took, frozen once it has stopped.

        The end of the clock is `_stopped` when the worker said it was done, and now
        while it is still going. Reading the phase would be enough only if every
        terminal path remembered to stop the clock; `_end` is that one place.
        """
        if not self._started:
            return 0
        end = self._stopped or time.perf_counter()
        return int((end - self._started) * 1000)

    def _totals(self) -> dict[str, int]:
        """Finished units plus the one in flight, counted exactly once each.

        A unit's counts are stored when it ends, so the live queue is folded in only
        while the current unit has not been stored yet — otherwise a finished round
        would be counted twice on the poll right after it finished.
        """
        tally: Counter[str] = Counter()
        for counts in self._unit_counts.values():
            tally.update(counts)
        view = self._session_view
        if view is not None and self._unit_index not in self._unit_counts:
            tally.update(_decided(view))
        return {name: tally.get(name, 0) for name in _DECIDED}


def _decided(view: SessionView) -> dict[str, int]:
    """A round's own tally, from the verdicts in its queue.

    Read off the queue rather than `summary.json` so it is available the moment a
    case is decided, and so a cancelled round — which has no summary yet — still
    counts what it managed to decide.
    """
    tally: Counter[str] = Counter()
    for item in view.queue:
        status = item.status.lower()
        if status in _DECIDED:
            tally[status] += 1
    return dict(tally)


def _focused_case(view: SessionView | None) -> str:
    """The case the strip names as "now".

    The one on the wire outranks everything: that is what a reviewer waiting on a
    request is waiting for, and it is the only thing that can be called "running".
    With nothing in flight this is the step the pane is on, which is what a parked
    run needs — the reviewer walked to a step to read it, and the strip should agree
    with the pane rather than shout over it.
    """
    if view is None:
        return ""
    for item in view.queue:
        if item.inflight:
            return item.case_id
    for item in view.queue:
        if item.current:
            return item.case_id
    return ""


def _unit_or_blank(unit: RunUnit | None) -> RunUnit:
    return unit or RunUnit(round="", round_id="", label="")


def _nothing_ran(plan: RunPlan) -> HarnessError:
    return HarnessError(
        code="RUN_FAILED",
        message=f"{plan.label}: no unit could start",
        hint="read the feed for the first reason, then fix the round or the case selection",
    )


def _as_harness_error(exc: BaseException) -> HarnessError:
    if isinstance(exc, HarnessError):
        return exc
    return HarnessError(
        code="RUN_FAILED",
        message=f"{type(exc).__name__}: {exc}",
        hint="run the plan from the CLI to see the full traceback",
    )
