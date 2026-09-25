from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

import httpx

from heimdall_qa import keys
from heimdall_qa import source
from heimdall_qa.campaign import find_latest_run
from heimdall_qa.collection import TreeNode
from heimdall_qa.collection import expand_to
from heimdall_qa.collection import find_node
from heimdall_qa.collection import find_step_dir
from heimdall_qa.collection import first_startable
from heimdall_qa.collection import index_projects
from heimdall_qa.collection import inspect_round
from heimdall_qa.collection import parent_round
from heimdall_qa.collection import rollup
from heimdall_qa.config import HarnessConfig
from heimdall_qa.engine import EngineView
from heimdall_qa.engine import RunEngine
from heimdall_qa.errors import HarnessError
from heimdall_qa.plan import plan_for
from heimdall_qa.plan import scopes_for
from heimdall_qa.projects import ProjectRef
from heimdall_qa.projects import id_for
from heimdall_qa.schema.load import content_root
from heimdall_qa.session import QueueItem
from heimdall_qa.session import RoundSession
from heimdall_qa.session import SessionView
from heimdall_qa.source import SourceDocument

#: What a run is aimed at. `case_forward` is the only scope that can run a case whose
#: credential an earlier case of the same round mints. `directory` runs every campaign
#: a real folder holds, which is what makes sorting campaigns into folders more than
#: decoration.
UI_SCOPES = frozenset({"campaign", "directory", "folder", "round", "case", "case_forward"})

#: The kinds whose selection gets the roll-up pane rather than the unit card: they hold
#: rounds instead of being one. A project is here because "how many endpoints are wrong
#: across everything I have open" is a real question, even though it gets no run button.
_ROLLUP_KINDS = frozenset({"project", "directory", "campaign", "folder"})


@dataclass(frozen=True)
class WorkspaceView:
    tree: tuple[TreeNode, ...]
    selected: TreeNode
    pane: str
    session: SessionView
    engine: EngineView
    historical_dir: Path | None = None
    next_unreviewed: TreeNode | None = None
    previous: dict[str, object] | None = None
    error: dict[str, object] | None = None
    #: The tree row a parked plan is waiting on, or `""`. Carried on the view and not
    #: derived by the client, because only the overlay's walk knows which tree row a
    #: queue position painted — a suite lists the same step six times. It is what lets
    #: a screen that is *not* showing the parked step offer "go back to it", which is
    #: the whole reason a reviewer can now click away from a parked run without being
    #: stuck looking at it.
    pending_key: str = ""


class WorkspaceSession:
    """The collection, the selection, and the engine that runs what is selected.

    The engine owns execution; this owns *what* is selected and *what the screen
    should show for it*. The split is what makes granularity possible at all: a
    campaign, a flow, a folder, a round and a case are all just differently-scoped
    plans, and the workspace no longer has to be able to start exactly one of them.

    It holds a **tuple of projects**, not a root. Every key in the tree carries the
    project it belongs to, so this is the object that can answer "whose root, whose
    run directory, whose secrets" for any node — and the object the engine asks when
    it needs to build a session for a plan that came from one project or another.

    The single-project constructor is kept because it is the honest shape of a
    one-project workspace, and because a test that hands in its own transport and run
    directory should not have to assemble a `ProjectRef` to do it.
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        projects: Sequence[ProjectRef] | None = None,
        root: Path | None = None,
        config: HarnessConfig | None = None,
        runs_dir: Path | None = None,
        secrets: dict[str, str] | None = None,
        focus: Path | None = None,
        engine: RunEngine | None = None,
    ) -> None:
        if projects is None:
            if root is None or config is None or runs_dir is None:
                raise TypeError(
                    "WorkspaceSession needs `projects`, or the `root`, `config` and"
                    " `runs_dir` of the one project it is for"
                )
            projects = (
                ProjectRef(
                    id=id_for(root),
                    name=root.name,
                    root=root,
                    runs_dir=runs_dir,
                    config=config,
                    secrets=dict(secrets or {}),
                ),
            )
        self._projects = tuple(projects)
        self._client = client
        self._engine = engine or RunEngine(
            root=self._projects[0].root,
            config=self._projects[0].config,
            client=client,
            runs_dir=self._projects[0].runs_dir,
            secrets=dict(self._projects[0].secrets),
        )
        #: The project whose plan is in flight, set at `start`. The engine knows the
        #: round it is running as a content path, and a path means nothing without the
        #: project it is relative to — this is what turns it back into a tree key, so
        #: the live round can be painted onto the stored tree and a click on one of its
        #: cases can be recognised as a click on the run.
        self._running: ProjectRef | None = None
        self._historical_dir: Path | None = None
        self._error: dict[str, object] | None = None
        self._tree: tuple[TreeNode, ...] = ()
        self._selected_key = ""
        #: The node the current plan was started from, for `_pane`'s benefit: it is the
        #: one roll-up that answers a parked plan with the verdict form rather than with
        #: its own card. Empty until something runs, and never cleared — it is inert
        #: whenever nothing is parked.
        self._started_key = ""
        #: What the tree was last indexed for: the plan has moved, or ended, and the
        #: collection now has something new to say about it. Indexing is a walk plus a
        #: round-file read each, so it happens on that marker and not on every poll.
        self._indexed_at: tuple[int, bool, Path | None] | None = None
        self.refresh()
        if focus is not None:
            self._select_focus_path(focus)
        elif self._tree:
            start = first_startable(self._tree) or self._tree[0]
            self._selected_key = start.key

    @property
    def projects(self) -> tuple[ProjectRef, ...]:
        """Every project this session draws, in the registry's order."""
        return self._projects

    @classmethod
    def wrap(cls, session: RoundSession) -> WorkspaceSession:
        """A workspace around an already-built session's wiring.

        Kept because `create_app(session=...)` is how a test hands in its own
        transport and run directory; the session object itself is not adopted, since
        execution now belongs to the engine and a live round is one unit of a plan.
        """
        return cls(
            root=session._root,
            config=session._config,
            client=session._client,
            runs_dir=session._runs_dir,
            secrets=session._secrets,
            focus=session._round_path,
        )

    # -- collection --------------------------------------------------------

    def refresh(self) -> None:
        self._tree = index_projects(self._projects)

    def add_project(self, project: ProjectRef) -> WorkspaceView:
        """Open one more project, keeping the selection where it was.

        Re-indexing is the whole point: there is one tree over every project, so a
        project that is open but absent from the tree is a project nobody can click.
        The selection survives because every key names its project, so the node that
        was under the cursor is still the node under the cursor after the tree grows.

        Adding a project that is already open is not an error — two windows, or a
        double click on a menu item, should land on the same workspace and not on a
        duplicate subtree whose rounds appear twice.
        """
        if any(candidate.id == project.id for candidate in self._projects):
            return self.view()
        self._projects = (*self._projects, project)
        self.refresh()
        return self.view()

    def remove_project(self, project_id: str) -> WorkspaceView:
        """Close a project: its subtree leaves the tree, and nothing else moves.

        No file is deleted and no run is touched — this is the client forgetting a
        root, not a verdict on what is under it. If the selection lived inside the
        project it falls back the way it does when any node disappears, and if the
        project was the one a plan was started for the live round is simply no longer
        part of the tree.

        The last project cannot be closed. A client with no project has no tree, no
        editor and no round to run, so the honest answer is to refuse rather than to
        hand back a screen that looks broken.
        """
        remaining = tuple(item for item in self._projects if item.id != project_id)
        if len(remaining) == len(self._projects):
            raise HarnessError(
                code="PROJECT_UNKNOWN",
                message=f"no project '{project_id}' is open",
                hint="reload; the project may have been removed in another window",
            )
        if not remaining:
            raise HarnessError(
                code="PROJECT_EMPTY",
                message="the last project cannot be closed",
                hint="open another project first, then close this one",
            )
        self._projects = remaining
        self.refresh()
        return self.view()

    def owner(self, node: TreeNode | None) -> ProjectRef:
        """The project a node belongs to.

        A node carries its own project id, so this is a lookup and not a search. The
        fallback to the first project covers the one node that belongs to none — the
        placeholder a workspace with an empty tree selects — and keeps the card, the
        editor and the run buttons working before anything has been registered.
        """
        if node is not None and node.project:
            for project in self._projects:
                if project.id == node.project:
                    return project
        if not self._projects:
            raise HarnessError(
                code="PROJECT_EMPTY",
                message="no project is open",
                hint="add one from the server dialog, or start with --root",
            )
        return self._projects[0]

    def project(self, project_id: str | None = None) -> ProjectRef:
        """The named project, or the selected node's when no id is given.

        Both cases are real: the editor and the run buttons act on the selection, and a
        context action on a tree node names the project it belongs to outright rather
        than depending on what happened to be selected when the menu opened.
        """
        if not project_id:
            return self._selected_owner()
        for candidate in self._projects:
            if candidate.id == project_id:
                return candidate
        raise HarnessError(
            code="PROJECT_UNKNOWN",
            message=f"no project '{project_id}' is open",
            hint="reload; the project may have been removed in another window",
        )

    def _selected_owner(self) -> ProjectRef:
        return self.owner(find_node(self._tree, self._selected_key))

    def read_source(self, path: str) -> SourceDocument:
        """A file the reviewer wrote, with the findings `validate` gives it now.

        On the workspace rather than in the route because the root and the project view
        are its to hold: a route that assembled them itself would be a second place
        that knows where content lives, and the desktop client is not going to be the
        only caller of this for long.

        The project is the selected node's. That is the only answer that is right when
        several projects are open — `rounds/smoke.yaml` exists in more than one of
        them — and it is the one the client means, because it opened the file from the
        node it had selected.
        """
        project = self._selected_owner()
        return source.read_source(project.root, project.config.project, path)

    def write_source(self, path: str, text: str) -> SourceDocument:
        """Save, then re-index: a round that just became valid has a new status.

        Re-indexing here and not in the route is the point of routing this through the
        workspace at all. A save that turns a broken round into a working one changes
        what the tree draws, and a client told "saved" while the tree still says
        `invalid` would be right to distrust the screen.
        """
        project = self._selected_owner()
        document = source.write_source(project.root, project.config.project, path, text)
        self.refresh()
        return document

    def create_folder(self, path: str, project_id: str = "") -> WorkspaceView:
        """Make a folder under `campaigns/`, then re-index so it appears.

        The re-index is not a nicety: an empty folder has nothing in it to notice, so
        without this the tree would not change at all and the folder the reviewer just
        created would look like it failed. `collection` keeps empty folders for exactly
        this reason — the tree has to be able to show the destination before it holds
        anything.

        The project can be named outright because a context action targets the node
        under the pointer, which is not necessarily the selected one.
        """
        project = self.project(project_id or None)
        source.create_folder(project.root, project.config.project, path)
        self.refresh()
        return self.view()

    def move_campaign(
        self,
        path: str,
        directory: str,
        project_id: str = "",
    ) -> WorkspaceView:
        """Move a campaign file into another folder, then re-index.

        The campaign's key is built from its `id:` and not from its path, so the
        selection survives the move. That is what makes "Mover para…" a move on screen
        instead of the old node vanishing and a new one appearing under the cursor.
        """
        project = self.project(project_id or None)
        source.move_campaign(project.root, project.config.project, path, directory)
        self.refresh()
        return self.view()

    def select(self, key: str) -> WorkspaceView:
        node = find_node(self._tree, key)
        if node is None:
            raise HarnessError(
                code="ROUND_INVALID",
                message=f"unknown tree node: {key}",
                hint="reload the collection from the left tree",
            )
        self._selected_key = key
        self._error = None
        if self._is_live(node):
            # The live round is on screen; a case click is a step to focus, not a
            # different run to render.
            self._historical_dir = None
            if node.kind == "case" and node.case_id:
                self._focus_live_case(node)
            return self.view()
        self._historical_dir = self._historical_for(node)
        return self.view()

    # -- execution ---------------------------------------------------------

    def start(
        self,
        scope: str,
        mode: str,
        node_key: str | None = None,
    ) -> WorkspaceView:
        """Turn the selection into a plan and hand it to the engine.

        The scope defaults to the narrowest one the node supports, which is what
        keeps `POST /start` with a round selected meaning what it always meant.
        """
        if node_key:
            self.select(node_key)
        node = find_node(self._tree, self._selected_key)
        if node is None:
            raise HarnessError(
                code="ROUND_INVALID",
                message="nothing is selected to run",
                hint="pick a campaign, a flow, a round or a case from the tree",
            )
        if not scope:
            scope = _default_scope(node)
        project = self.owner(node)
        plan = plan_for(
            self._tree,
            node.key,
            scope,
            root=project.root,
            project=project.config.project,
        )
        if not plan.units:
            raise HarnessError(
                code="ROUND_INVALID",
                message=f"{plan.label} has nothing to run",
                hint="pick another node from the collection",
            )
        # A plan with no runnable unit is a plan that would do nothing but write a
        # run directory. Refusing it keeps the answer to "start this round" honest
        # when the round is not ready, and still lets a campaign skip the rounds it
        # lists that are. The reason is the collection's own, not a generic one.
        if not plan.runnable:
            unit = plan.skipped[0]
            raise HarnessError(
                code="ROUND_INVALID",
                message=f"{unit.label}: {unit.skip}",
                hint="fix the round, or run something else from the collection",
            )
        self._running = project
        self._engine.start(plan, mode, project)
        self._historical_dir = None
        #: The node the current plan was started from. It is what lets a campaign walk
        #: land on the first case that needs a verdict instead of on the campaign's own
        #: card, while every *other* node stays openable while the plan waits — see
        #: `_pane`. Inert once nothing is parked, so it is never cleared.
        self._started_key = node.key
        return self.view()

    def apply_verdict(
        self,
        status: str,
        comment: str,
        continue_round: bool,
    ) -> WorkspaceView:
        self._engine.submit_verdict(status, comment, continue_round)
        return self.view()

    def cancel(self) -> WorkspaceView:
        self._engine.cancel()
        return self.view()

    def focus(self, index: int) -> WorkspaceView:
        self._engine.focus(index)
        return self.view()

    def wait_settled(self, timeout: float | None = None) -> WorkspaceView:
        """Block until the engine is awaiting a verdict or finished.

        The seam the suite uses: a request starts or advances a run, and the test
        then waits for the engine instead of sleeping and hoping.
        """
        self._engine.wait_settled(timeout=timeout)
        return self.view()

    def wait_change(self, after: int, timeout: float) -> int:
        """Block until the engine's revision moves past `after`, or time out.

        The seam `GET /api/events` uses: the stream has nothing to send until the
        engine says something, and blocking on the engine's own condition variable is
        what makes the stream push instead of poll. A timeout is returned as the same
        revision it was given, which the caller reads as "send a keep-alive".
        """
        return self._engine.wait_for_change(after, timeout=timeout)

    def engine_revision(self) -> int:
        """The dirty flag, without building a view around it."""
        return self._engine.revision()

    # -- rendering ---------------------------------------------------------

    def view(self) -> WorkspaceView:
        engine = self._engine.snapshot()
        self._reindex_after_a_unit(engine)
        selected = find_node(self._tree, self._selected_key)
        if selected is None:
            selected = self._tree[0] if self._tree else _empty_node()
            self._selected_key = selected.key
        tree, row_keys = self._overlay_live(self._tree)
        tree = expand_to(tree, self._selected_key)
        selected = find_node(tree, self._selected_key) or selected
        session = self._session_view(selected, engine, row_keys)
        pane = self._pane(selected, session, engine)
        return WorkspaceView(
            tree=tree,
            selected=selected,
            pane=pane,
            session=session,
            engine=engine,
            historical_dir=self._historical_dir,
            next_unreviewed=_next_unreviewed(selected),
            # Read here and not in the template: the card wants the numbers the last
            # run ended with, and a template cannot open a file. Only asked for when
            # the card is actually drawn, which is only when nothing is running.
            previous=self._previous_run(selected) if pane == "start" else None,
            error=self._error or session.error,
            pending_key=self._pending_key(engine, row_keys),
        )

    def _previous_run(self, selected: TreeNode) -> dict[str, object] | None:
        """The selected round's last run, for the unit card's "last time" line.

        Read off the tree's index rather than globbing `runs/` again: `inspect_round`
        already found the latest run and read its summary to decide the badge, and two
        sweeps of the same directory is two answers that can disagree. A case selection
        answers with its round's run, which is what its card has always meant.
        """
        node = parent_round(self._tree, selected.key)
        if node is None or node.run is None or not node.run.summary:
            return None
        return {**node.run.summary, "run_dir": str(node.run.path.resolve())}

    def _pending_key(self, engine: EngineView, row_keys: dict[int, str]) -> str:
        """The tree row a parked plan is waiting on, or `""`.

        Read from the live session's own position, so it is the row the run is on and
        not the first row that shares its label. Empty while nothing is parked, and
        empty for a parked step the overlay never painted — a row that is not in the
        collection cannot be jumped to, and naming one would be a broken link.
        """
        if not engine.awaiting:
            return ""
        live = self._engine.session_view()
        if live is None:
            return ""
        return row_keys.get(live.pending_index, "")

    def _reindex_after_a_unit(self, engine: EngineView) -> None:
        """Re-read the collection when the run has something new to say about it.

        The tree is built from disk once, at startup, and a round that just finished is
        only on disk by then — without this, a campaign's roll-up would still read
        "not reviewed" for every round it had already walked. Re-indexing on each poll
        would glob `runs/` and re-read every round file twice a second; re-indexing when
        the plan moves to another unit costs that once per round, and a terminal phase
        has no next unit to wait for, so it re-indexes too.
        """
        marker = (engine.unit_index, engine.finished, engine.run_dir)
        if marker == self._indexed_at or engine.run_dir is None:
            return
        self._indexed_at = marker
        self.refresh()

    def _pane(self, selected: TreeNode, session: SessionView, engine: EngineView) -> str:
        """Which panel the selection deserves, in priority order.

        A **roll-up** comes first, and that is a correction rather than a preference. A
        parked plan used to outrank everything, so clicking any node while a run waited
        for a verdict redrew the parked step: the selection moved, the tree highlighted
        the new node, and the pane showed something else. That is indistinguishable from
        "the app will not let me open this case", which is what a reviewer reported. The
        roll-up now answers for the campaign the plan belongs to — it reports the run and
        offers the one click to the parked row — so parking is answered without taking
        the screen hostage.

        A parked plan still outranks a *step* selection while the selection is the live
        round or one of its cases, because that is precisely the screen the verdict form
        belongs on. So does the node the plan was started from: starting a campaign in
        walk mode has to land on the case that needs the first verdict, and it is the
        one selection where the roll-up is not the answer the reviewer asked for.
        Anything else is read from disk, which is what makes every other case in the
        collection openable mid-run.
        """
        if selected.kind in _ROLLUP_KINDS and not (
            engine.awaiting and selected.key == self._started_key
        ):
            return "campaign"
        if engine.awaiting and (self._is_live(selected) or selected.key == self._started_key):
            return "review"
        if self._is_live(selected):
            if engine.busy or session.phase in {"step", "running"}:
                return "review"
            # Read-only review: the engine is past the step and the case is on
            # screen, which is how a failing case is reopened from the end-of-run list.
            if selected.kind == "case" and session.current_step_dir is not None:
                return "review"
        # A round with a finished run on disk opens on its KPIs, whether or not the
        # engine still remembers running it. That last clause is the whole fix: the
        # branch below used to sit inside `self._is_live(selected)` and ask the session
        # for `phase == "done"`, so of a campaign's forty rounds exactly one — the last
        # unit, and only until another plan started — could show what its run said. The
        # answer is on disk and already indexed on the node, so every round gets it.
        if selected.kind == "round" and _has_summary(selected):
            return "done"
        if selected.kind == "case" and self._historical_dir is not None:
            return "historical"
        return "start"

    def _session_view(
        self,
        selected: TreeNode,
        engine: EngineView,
        row_keys: dict[int, str],
    ) -> SessionView:
        live = self._engine.session_view()
        if live is not None and self._holds_live(selected):
            # The live round is what the screen must show, whether the selection is one
            # of its cases, the round itself, or a campaign the plan belongs to: a
            # parked plan has a step that needs a verdict, and only the session knows
            # which one. The row keys come from the overlay, which is the only walk that
            # knows which tree row each queue position painted.
            #
            # Containment and not identity, because "a campaign the plan belongs to" is
            # the half that used to be missing, and it is the selection a reviewer runs a
            # campaign from. A campaign, a folder and a project got the *stored* session
            # — `phase="start"`, an empty queue, progress 0/0 — while a plan was running
            # inside them, so the roll-up's progress bar drew nothing and its badges were
            # whatever the last re-index read. The screen only told the truth once the
            # reviewer clicked into a case and back, which is the "it does not update in
            # real time" this replaces.
            #
            # And containment is *walked*, never approximated: the tree is one tree over
            # every project, so a rule that read "this project is live because it holds
            # the key somewhere" without descending would paint every sibling of the run
            # with the live round's queue.
            return replace(
                live,
                row_keys=tuple(
                    row_keys.get(index, "") for index in range(len(live.queue))
                ),
            )
        round_node = parent_round(self._tree, selected.key) or selected
        children = round_node.children if round_node.kind == "round" else ()
        return SessionView(
            phase="start",
            queue=tuple(
                QueueItem(child.case_id or child.label, child.status)
                for child in children
            ),
            # A round that is not the live one is not painted by the overlay, so its
            # rows are its own children and each one names itself.
            row_keys=tuple(child.key for child in children),
            current_step_dir=self._historical_dir,
            run_dir=None,
            mode="",
            error=None,
            round_id=round_node.round_id or round_node.label,
            environment=round_node.environment or "",
            progress=(0, len(children)),
        )

    # -- live round --------------------------------------------------------

    def _is_live(self, node: TreeNode) -> bool:
        key = self._live_round_key()
        if key is None:
            return False
        if node.key == key:
            return True
        # The case's own project, not the running one: a case from another project
        # with the same path is a different node and must not be painted as live.
        return node.kind == "case" and keys.for_round(node.project, node.path or "") == key

    def _holds_live(self, node: TreeNode) -> bool:
        """Whether the round on the wire is this node or something under it.

        The difference from `_is_live` is the roll-up: a campaign, a folder and a
        project *contain* the live round without being it, and they are exactly the
        selections a plan is started from and read on. Asked here rather than in
        `_session_view` so the tree paint and the session agree on one answer.
        """
        key = self._live_round_key()
        if key is None:
            return False
        if self._is_live(node):
            return True
        return find_node(node.children, key) is not None

    def _live_round_key(self) -> str | None:
        """The tree key of the round on the wire, or `None` when nothing is running.

        A round is named by a content path, and a content path is only meaningful
        inside one project — so the key needs the project the plan was started for. It
        is remembered at `start`, which is the only moment it is unambiguous.
        """
        engine = self._engine.snapshot()
        if not engine.unit_round or self._running is None:
            return None
        return keys.for_round(self._running.id, engine.unit_round)

    def _focus_live_case(self, node: TreeNode) -> None:
        """Point the live round's pane at the row a tree node stands for.

        By node key and not by case id: the overlay's own walk is the only thing that
        knows which queue position a row painted — a suite lists the same step six
        times — and reading it here is what makes clicking the third loop open the
        third loop instead of the first.

        The id is the fallback and not the rule. It is right for everything that is not
        a suite (a round's cases are unique), and for a row the overlay never painted
        — a step a `from_step` run did not replay is not in the queue at all, and
        jumping to the first row that shares its label is the old behaviour this
        replaces only where it can be replaced.
        """
        live = self._engine.session_view()
        if live is None:
            return
        for index, row in enumerate(live.row_keys):
            if row == node.key:
                self._engine.focus(index)
                return
        for index, item in enumerate(live.queue):
            if item.case_id == (node.case_id or node.label):
                self._engine.focus(index)
                return

    def _overlay_live(self, tree: tuple[TreeNode, ...]) -> tuple[tuple[TreeNode, ...], dict[int, str]]:
        """Paint the live round's per-case verdicts onto the stored ones.

        The tree is built from disk, so a case decided thirty seconds ago still reads
        as `not_reviewed` until the round ends and its summary is written. This is
        what makes the collection answer "where is it now" and not "where did it
        finish".

        `start` is the one phase that has nothing to paint — the run directory exists
        and no case has gone out yet — so it is the only one that returns the tree
        unchanged. `running` is the phase this exists for: it used to be that the
        session never left `start` until it parked or finished, and a tree that only
        updated at the end of a round is a tree a reviewer has to click around to
        refresh.

        The second half of the answer is which tree row each queue position paints.
        The client needs it to open the step a row names, and a case id cannot supply
        it: a suite lists the same step six times. It is returned rather than
        recomputed because this walk already knows, and a second walk would be a second
        answer.
        """
        live = self._engine.session_view()
        if live is None or live.phase == "start":
            return tree, {}
        return _overlay_queue(tree, self._live_round_key(), live.queue)

    # -- history -----------------------------------------------------------

    def _historical_for(self, node: TreeNode) -> Path | None:
        """The step directory of a case in a run that is not the live one.

        Only reached when the selection is not what the engine is showing, which is
        how a previous run is reopened from the tree.
        """
        if node.kind != "case" or not node.case_id or not node.path:
            return None
        round_node = parent_round(self._tree, node.key)
        if round_node is None or not round_node.round_id:
            return None
        run_dir = find_latest_run(self.owner(node).runs_dir, round_node.round_id)
        if run_dir is None:
            return None
        return find_step_dir(run_dir, node.case_id)

    def _select_focus_path(self, path: Path) -> None:
        """Select a round by the absolute path a caller was handed.

        Only ever reached for the workspace's first project — a `--root`/target on the
        command line names one project's round — so the content root and the project id
        are that project's.
        """
        project = self._projects[0]
        content = content_root(project.root, project.config.project)
        rel = _posix_rel(path, content)
        key = keys.for_round(project.id, rel)
        if find_node(self._tree, key) is None:
            self._tree = self._tree + (
                inspect_round(
                    rel,
                    project.root,
                    project.runs_dir,
                    project.config.project,
                    project_id=project.id,
                    endpoint=None,
                    matrix=None,
                ),
            )
        self._selected_key = key


def _default_scope(node: TreeNode) -> str:
    scopes = scopes_for(node)
    if not scopes:
        raise HarnessError(
            code="ROUND_INVALID",
            message=f"{node.label} cannot be run",
            hint="pick a campaign, a flow, a round or a case from the tree",
        )
    return scopes[0]


def _has_summary(node: TreeNode) -> bool:
    """Whether this round has a finished run whose KPIs the pane can draw.

    A run directory exists from the moment a round starts, so "is there a run" is not
    the question the pane asks — a run with no `summary.json` is a run still in flight,
    and opening its KPIs would draw a card of zeros over the step a reviewer is
    watching go out. The summary is what the pane reads, so the summary is the gate.
    """
    return node.run is not None and bool(node.run.summary)


def _overlay_queue(
    nodes: tuple[TreeNode, ...],
    live_key: str | None,
    queue: tuple[QueueItem, ...],
) -> tuple[tuple[TreeNode, ...], dict[int, str]]:
    """The live round's rows, painted, and which row each queue position painted.

    Matched by id, forward only, with the position remembered. Both halves of that
    matter. By id, because a round's rows and its queue are not always the same
    length: a dimension-filtered round runs a subset of what the tree lists, and a
    `from_step` run replays a tail of a suite. Forward only, because a suite's rows
    can share a label — two loops over the same case are two rows called `loop x3` —
    and a lookup keyed by id would paint every one of them with the last status
    written. Walking the two lists together paints the third row with the third
    case's verdict, and leaves a row whose case did not run untouched.

    The live round's own badge, and every badge above it, is re-derived from the
    children instead of being left at whatever the last re-index read. Without that a
    campaign of rounds turning green still said "não reviewado" at the top, which is
    the tree a reviewer clicks around in to make it tell the truth. A node with no
    live row under it is returned untouched, badge included.
    """
    if live_key is None:
        return nodes, {}
    rows: dict[int, str] = {}
    painted, found = _paint_queue(nodes, live_key, queue, rows)
    if not found:
        # Nothing to paint — a `--root` on a round that is not in the collection. Not an
        # error: the overlay is a paint job, and a key it cannot find leaves the tree as
        # it was built.
        return nodes, {}
    return painted, rows


def _paint_queue(
    nodes: tuple[TreeNode, ...],
    live_key: str,
    queue: tuple[QueueItem, ...],
    rows: dict[int, str],
) -> tuple[tuple[TreeNode, ...], bool]:
    """One level of the walk: paint, and say whether the live round was under it."""
    updated: list[TreeNode] = []
    found = False
    for node in nodes:
        if node.key == live_key:
            updated.append(_relive(node, _paint_rows(node.children, queue, rows)))
            found = True
            continue
        children, under = _paint_queue(node.children, live_key, queue, rows)
        if not under:
            updated.append(node)
            continue
        updated.append(_relive(node, children))
        found = True
    return tuple(updated), found


def _paint_rows(
    children: tuple[TreeNode, ...],
    queue: tuple[QueueItem, ...],
    rows: dict[int, str],
) -> tuple[TreeNode, ...]:
    """One round's rows: the verdict of their queue position, and where the run is."""
    painted: list[TreeNode] = []
    cursor = 0
    for child in children:
        wanted = child.case_id or child.label
        match = None
        for index in range(cursor, len(queue)):
            if queue[index].case_id == wanted:
                match = (index, queue[index])
                break
        if match is None:
            painted.append(child)
            continue
        cursor = match[0] + 1
        rows[match[0]] = child.key
        painted.append(replace(child, status=match[1].status, live=match[1].live))
    return tuple(painted)


def _relive(node: TreeNode, children: tuple[TreeNode, ...]) -> TreeNode:
    """A node whose children just moved: its badge and its live mark follow them."""
    return replace(
        node,
        children=children,
        status=rollup(children),
        live=_live_rollup(children),
    )


def _live_rollup(children: tuple[TreeNode, ...]) -> str:
    """`running` while a row under this node is on the wire, else `awaiting`.

    One plan runs at a time, so the two cannot both be under one node; the order is
    here only so that a mark seeing both would name the more active one rather than
    the alphabetical one.
    """
    marks = {child.live for child in children}
    if "running" in marks:
        return "running"
    return "awaiting" if "awaiting" in marks else ""


def _next_unreviewed(selected: TreeNode) -> TreeNode | None:
    roots = selected.children if selected.kind in _ROLLUP_KINDS else (selected,)
    return _first_status(roots, "not_reviewed")


def _first_status(nodes: tuple[TreeNode, ...], status: str) -> TreeNode | None:
    for node in nodes:
        if node.kind == "round" and node.status == status and node.startable:
            return node
        found = _first_status(node.children, status)
        if found is not None:
            return found
    return None


def _empty_node() -> TreeNode:
    return TreeNode(
        key="workspace",
        kind="campaign",
        label="(vazia)",
        status="not_reviewed",
    )


def _posix_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
