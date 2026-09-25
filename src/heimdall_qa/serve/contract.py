"""The panel payloads, translated into the API's contract.

`serve/panel.py` decides what the screen contains and `serve/models.py` says what the
client is promised; this is the one place the two meet. It is deliberately thin — it
reads no files and decides nothing about what a panel shows — because the moment it
starts computing, there are two answers to "what does a pack row mean" and only one of
them is tested.

The reason it is not folded into `api.py`: the translation is testable without HTTP,
and a test that builds a `BootstrapModel` from a probe's directory is a far better
guard against the contract drifting than a test that starts a server and greps JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from heimdall_qa.collection import TreeNode
from heimdall_qa.demo.runtime import DemoApiState
from heimdall_qa.engine import EngineView
from heimdall_qa.mcp.runtime import McpState
from heimdall_qa.projects import ProjectEntry
from heimdall_qa.serve import panel
from heimdall_qa.serve.models import BootstrapModel
from heimdall_qa.serve.models import DemoModel
from heimdall_qa.serve.models import EngineEventModel
from heimdall_qa.serve.models import EngineModel
from heimdall_qa.serve.models import HarnessErrorModel
from heimdall_qa.serve.models import LabelsModel
from heimdall_qa.serve.models import McpModel
from heimdall_qa.serve.models import ProbeRowModel
from heimdall_qa.serve.models import ProjectModel
from heimdall_qa.serve.models import ProjectsModel
from heimdall_qa.serve.models import QueueItemModel
from heimdall_qa.serve.models import RollupRunsModel
from heimdall_qa.serve.models import RoundRunRowModel
from heimdall_qa.serve.models import RunAggregateModel
from heimdall_qa.serve.models import SessionModel
from heimdall_qa.serve.models import SourceFindingModel
from heimdall_qa.serve.models import SourceModel
from heimdall_qa.serve.models import StepModel
from heimdall_qa.serve.models import TreeNodeModel
from heimdall_qa.serve.models import UnitCardModel
from heimdall_qa.session import SessionView
from heimdall_qa.source import SourceDocument
from heimdall_qa.workspace import WorkspaceView

#: The panes that show a step, and so carry a `step` in the bootstrap.
_STEP_PANES = frozenset({"review", "historical"})


def labels_model() -> LabelsModel:
    """The Portuguese words, as a typed payload instead of Jinja globals."""
    return LabelsModel(**panel.labels())


def source_model(document: SourceDocument) -> SourceModel:
    """A file the editor holds, with the findings the validator gave it.

    The findings are copied field by field rather than by `dataclasses.asdict`: the
    dataclass is frozen and internal, and letting its shape define the wire format
    would mean a later field on it silently joins the contract.
    """
    return SourceModel(
        path=document.path,
        kind=document.kind,
        text=document.text,
        editable=document.editable,
        valid=document.valid,
        findings=[
            SourceFindingModel(
                code=item.code,
                where=item.where,
                message=item.message,
                fix=item.fix,
                why=item.why,
            )
            for item in document.findings
        ],
    )


def error_model(payload: dict[str, Any] | None) -> HarnessErrorModel | None:
    """A harness error as a model, or `None` when there is nothing to report.

    `HarnessError` carries `exit_code`, which means nothing over HTTP; it is kept
    anyway rather than dropped, so that the CLI's rendering and the screen's are the
    same object and a test can assert on both.
    """
    if not payload:
        return None
    return HarnessErrorModel(
        code=str(payload.get("code", "")),
        message=str(payload.get("message", "")),
        hint=str(payload.get("hint", "")),
        details=[str(item) for item in payload.get("details", []) or []],
        exit_code=int(payload.get("exit_code", 1) or 1),
    )


def engine_model(view: EngineView) -> EngineModel:
    """The status bar's world, including the revision the stream compares."""
    return EngineModel(
        phase=view.phase,
        scope=view.scope,
        plan_label=view.plan_label,
        unit_index=view.unit_index,
        unit_total=view.unit_total,
        unit_label=view.unit_label,
        unit_round=view.unit_round,
        unit_skips=list(view.unit_skips),
        case_total=view.case_total,
        cases_done=view.cases_done,
        step_index=view.step_index,
        step_total=view.step_total,
        case_id=view.case_id,
        run_dir=str(view.run_dir.resolve()) if view.run_dir else "",
        counts=dict(view.counts),
        awaiting=view.awaiting,
        busy=view.busy,
        finished=view.finished,
        error=error_model(view.error),
        events=[
            EngineEventModel(at=item.at, kind=item.kind, text=item.text, status=item.status)
            for item in view.events
        ],
        elapsed_ms=view.elapsed_ms,
        revision=view.revision,
    )


def session_model(view: SessionView) -> SessionModel:
    """The live round: the queue, where the review is, and what is pending.

    `current_step_dir` is shipped as an absolute path on purpose — it is how a
    reviewer, and a bug report, names the step they are looking at without opening
    the run directory and counting.
    """
    return SessionModel(
        phase=view.phase,
        queue=[
            QueueItemModel(
                case_id=item.case_id,
                status=item.status,
                current=item.current,
                reachable=item.reachable,
                # The row's own key, by position: `row_keys` is index-aligned with the
                # queue and empty when the round on screen is not the one running.
                key=view.row_keys[index] if index < len(view.row_keys) else "",
                live=item.live,
            )
            for index, item in enumerate(view.queue)
        ],
        run_dir=str(view.run_dir.resolve()) if view.run_dir else "",
        current_step_dir=(
            str(view.current_step_dir.resolve()) if view.current_step_dir else ""
        ),
        mode=view.mode,
        error=error_model(view.error),
        round_id=view.round_id,
        environment=view.environment,
        dimensions=list(view.dimensions),
        progress=list(view.progress),
        focus_index=view.focus_index,
        pending_index=view.pending_index,
        can_prev=view.can_prev,
        can_next=view.can_next,
        awaiting_verdict=view.awaiting_verdict,
    )


def tree_model(node: TreeNode, fail_counts: dict[str, int]) -> TreeNodeModel:
    """One collection node, with the failing-descendant count the badge shows.

    Recursive because the collection is: a campaign's children are folders', whose
    children are rounds'. The counts are passed down rather than recomputed, so the
    number on a node and the number in its parent's badge are the same count.
    """
    return TreeNodeModel(
        key=node.key,
        kind=node.kind,
        label=node.label,
        status=node.status,
        children=[tree_model(child, fail_counts) for child in node.children],
        path=node.path,
        round_id=node.round_id,
        endpoint=node.endpoint,
        matrix=node.matrix,
        case_id=node.case_id,
        reason=node.reason,
        startable=node.startable,
        expanded=node.expanded,
        environment=node.environment,
        step_kind=node.step_kind,
        fail_count=fail_counts.get(node.key, 0),
        project=node.project,
        live=node.live,
    )


def unit_card_model(wv: WorkspaceView) -> UnitCardModel:
    """What is selected, the five scopes it offers, and what each button says.

    The scopes and their words come from `panel` rather than being restated here: a
    suite step's forward button reads differently from a case's, and the Jinja card
    and the SPA must give the same answer about which.
    """
    node = wv.selected
    return UnitCardModel(
        key=node.key,
        kind=node.kind,
        label=node.label,
        status=node.status,
        path=node.path,
        round_id=node.round_id,
        case_id=node.case_id,
        endpoint=node.endpoint,
        environment=node.environment,
        step_kind=node.step_kind,
        startable=node.startable,
        reason=node.reason,
        scopes=list(panel.scopes_of(node)),
        scope_labels=panel.scope_labels(node),
        step_note=panel.STEP_NOTE.get(node.step_kind, ""),
        previous=wv.previous if isinstance(wv.previous, dict) else None,
        project=node.project,
    )


def step_model(wv: WorkspaceView) -> StepModel | None:
    """The step's evidence, or `None` when the pane is not showing one.

    `panel.step_payload` reads the directory; this only renames its keys into the
    contract. The historical pane is handled by `panel.review_session`, so a step from
    a finished run and the live one are photographed by the same code.
    """
    if wv.pane not in _STEP_PANES:
        return None
    payload = panel.step_payload(panel.review_session(wv))
    rows = [ProbeRowModel(**row) for row in payload.pop("probe_rows", [])]
    return StepModel(**payload, probe_rows=rows)


def run_model(wv: WorkspaceView) -> RunAggregateModel | None:
    """The end-of-run KPIs and the failing cases worth reopening, at `done` only."""
    if wv.pane != "done":
        return None
    extra = panel.done_extra(wv)
    return RunAggregateModel(
        summary=extra.get("summary", {}) or {},
        failed_cases=list(extra.get("failed_cases", []) or []),
        run_path=str(extra.get("run_path", "")),
        unit_key=wv.selected.key,
        unit_label=wv.selected.label,
        unit_kind=wv.selected.kind,
    )


def rollup_model(wv: WorkspaceView) -> RollupRunsModel | None:
    """The per-round table behind a roll-up selection, at the roll-up pane only.

    Gated on the pane rather than on the node's kind because the pane is what decides
    that the client is drawing the roll-up: the one case a roll-up *kind* does not get
    its table is a campaign the parked plan was started from, which is drawn as the
    verdict form instead.
    """
    if wv.pane != "campaign":
        return None
    extra = panel.rollup_extra(wv.selected)
    return RollupRunsModel(
        totals={str(key): int(value) for key, value in extra.get("totals", {}).items()},
        units=[RoundRunRowModel(**row) for row in extra.get("units", [])],
        rounds_total=int(extra.get("rounds_total", 0)),
        rounds_run=int(extra.get("rounds_run", 0)),
    )


def projects_model(
    entries: tuple[ProjectEntry, ...],
    *,
    registry: str,
    open_ids: frozenset[str],
) -> ProjectsModel:
    """The registry as the Server dialog lists it, with what is on screen marked.

    `open_ids` comes from the workspace and not from the registry because the two are
    not the same set: a client launched with `--root` draws a project it may never
    have written to the file, and that project has to read as open or the dialog
    contradicts the tree.
    """
    return ProjectsModel(
        registry=registry,
        projects=[
            ProjectModel(
                id=entry.id,
                name=entry.name,
                root=str(entry.root),
                open=entry.id in open_ids,
            )
            for entry in entries
        ],
    )


def mcp_model(state: McpState) -> McpModel:
    """The MCP server's state, with the snippet only when there is something to reach.

    An `error` state still carries the host and port: they are what the reader has to
    change or free up, and a dialog that hid them would turn a named failure into a
    mystery. The snippet is withheld while the socket is down, though — pasting a URL
    that answers nothing would be advice to fail later.
    """
    return McpModel(
        enabled=state.enabled,
        host=state.host,
        port=state.port,
        path=state.path,
        url=state.url,
        state=state.state,
        error=error_model(state.error),
        config_snippet=state.config_snippet() if state.enabled else "",
    )


def demo_model(state: DemoApiState, *, root: Path, project_id: str) -> DemoModel:
    """The demo's state, named the same way the MCP one is.

    `root` and `project_id` come from the caller and not the state because they are
    facts about the *files*, not the socket: the materialized tree exists whether or
    not the mock is up, and the id is the same one the registry uses, derived from the
    path so a second launch agrees about which project the demo is.
    """
    return DemoModel(
        enabled=state.enabled,
        state=state.state,
        host=state.host,
        port=state.port,
        base_url=state.base_url,
        root=str(root),
        project_id=project_id,
        log_path=state.log_path,
        error=error_model(state.error),
    )


def stream_payload(wv: WorkspaceView) -> dict[str, Any]:
    """The event stream's frame: the engine, plus which screen it belongs to.

    `pane` and `selected` ride along so the client can tell "the status bar moved"
    from "the step changed" without fetching anything. The tree is *not* here: it is
    large, it changes once per unit, and re-reading it on every feed line is exactly
    the cost the stream exists to remove.
    """
    return {
        "engine": engine_model(wv.engine),
        "pane": wv.pane,
        "selected": wv.selected.key,
    }


def bootstrap_model(wv: WorkspaceView) -> BootstrapModel:
    """The whole screen in one read: tree, selection, pane, engine, step, run.

    This is what the client draws on load and re-reads when the event stream says
    something beyond the engine moved. The engine alone is cheap enough to stream; the
    tree is not, which is why the two are separate transports of the same contract.
    """
    fail_counts = panel.fail_counts(wv.tree)
    return BootstrapModel(
        tree=[tree_model(node, fail_counts) for node in wv.tree],
        selected=wv.selected.key,
        pane=wv.pane,
        engine=engine_model(wv.engine),
        session=session_model(wv.session),
        unit=unit_card_model(wv),
        step=step_model(wv),
        run=run_model(wv),
        rollup=rollup_model(wv),
        labels=labels_model(),
        next_unreviewed=wv.next_unreviewed.key if wv.next_unreviewed else None,
        pending_key=wv.pending_key,
        error=error_model(
            wv.error if isinstance(wv.error, dict) else None
        ),
    )
