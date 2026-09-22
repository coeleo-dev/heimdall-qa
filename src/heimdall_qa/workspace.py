from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

import httpx

from heimdall_qa.collection import TreeNode
from heimdall_qa.collection import expand_to
from heimdall_qa.collection import find_node
from heimdall_qa.collection import find_step_dir
from heimdall_qa.collection import first_startable
from heimdall_qa.collection import index_workspace
from heimdall_qa.collection import inspect_round
from heimdall_qa.collection import parent_round
from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError
from heimdall_qa.session import QueueItem
from heimdall_qa.session import RoundSession
from heimdall_qa.session import SessionView
from heimdall_qa.validate import resolve_path


@dataclass(frozen=True)
class WorkspaceView:
    tree: tuple[TreeNode, ...]
    selected: TreeNode
    pane: str
    session: SessionView
    historical_dir: Path | None = None
    next_unreviewed: TreeNode | None = None
    error: dict[str, object] | None = None


class WorkspaceSession:
    def __init__(
        self,
        *,
        root: Path,
        config: HarnessConfig,
        client: httpx.Client,
        runs_dir: Path,
        secrets: dict[str, str] | None = None,
        focus: Path | None = None,
    ) -> None:
        self._root = root
        self._config = config
        self._client = client
        self._runs_dir = runs_dir
        self._secrets = secrets or {}
        self._active: RoundSession | None = None
        self._historical_dir: Path | None = None
        self._error: dict[str, object] | None = None
        self._tree: tuple[TreeNode, ...] = ()
        self._selected_key = ""
        self.refresh()
        if focus is not None:
            self._select_focus_path(focus)
        elif self._tree:
            start = first_startable(self._tree) or self._tree[0]
            self._selected_key = start.key

    @classmethod
    def wrap(cls, session: RoundSession) -> "WorkspaceSession":
        workspace = cls(
            root=session._root,
            config=session._config,
            client=session._client,
            runs_dir=session._runs_dir,
            secrets=session._secrets,
            focus=session._round_path,
        )
        workspace._active = session
        return workspace

    def refresh(self) -> None:
        self._tree = index_workspace(self._root, self._runs_dir)

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
        if self._busy():
            live = self._live_round_key()
            if node.kind == "case" and live == f"round:{node.path}":
                self._focus_live_case(node.case_id or "")
            self._historical_dir = None
            return self.view()
        self._historical_dir = self._historical_for(node)
        if node.kind == "case" and node.case_id and self._same_live_round(node):
            self._focus_live_case(node.case_id)
        return self.view()

    def start(self, mode: str, node_key: str | None = None) -> WorkspaceView:
        if node_key:
            self.select(node_key)
        if self._busy():
            raise HarnessError(
                code="ROUND_BUSY",
                message="a round is waiting for a verdict",
                hint="finish or stop the current round before starting another",
            )
        round_node = self._require_startable()
        round_path = resolve_path(self._root, round_node.path or "")
        if self._active is not None and self._active.view().phase == "start":
            if self._active._round_path.resolve() == round_path.resolve():
                self._active.start(mode)
                return self.view()
        self._active = RoundSession(
            round_path,
            root=self._root,
            config=self._config,
            client=self._client,
            runs_dir=self._runs_dir,
            secrets=self._secrets,
        )
        self._active.start(mode)
        self._historical_dir = None
        self._selected_key = round_node.key
        return self.view()

    def apply_verdict(
        self,
        status: str,
        comment: str,
        continue_round: bool,
    ) -> WorkspaceView:
        active = self._require_active()
        active.apply_verdict(status, comment, continue_round)
        if active.view().phase == "done":
            self.refresh()
        return self.view()

    def focus(self, index: int) -> WorkspaceView:
        active = self._require_active()
        active.focus(index)
        return self.view()

    def view(self) -> WorkspaceView:
        selected = find_node(self._tree, self._selected_key)
        if selected is None:
            selected = self._tree[0] if self._tree else _empty_node()
            self._selected_key = selected.key
        tree = expand_to(self._overlay_live(self._tree), self._selected_key)
        selected = find_node(tree, self._selected_key) or selected
        session = self._session_view(selected)
        return WorkspaceView(
            tree=tree,
            selected=selected,
            pane=self._pane(selected, session),
            session=session,
            historical_dir=self._historical_dir,
            next_unreviewed=_next_unreviewed(selected),
            error=self._error or session.error,
        )

    def _pane(self, selected: TreeNode, session: SessionView) -> str:
        reviewing_live = self._selected_is_live(selected) and (
            self._busy() or session.phase == "step"
        )
        if reviewing_live:
            return "review"
        if selected.kind == "case" and self._historical_dir is not None:
            return "historical"
        if session.phase == "done" and self._selected_is_live(selected):
            return "done"
        if selected.kind in {"campaign", "folder"}:
            return "campaign"
        return "start"

    def _session_view(self, selected: TreeNode) -> SessionView:
        if self._active is not None and self._selected_is_live(selected):
            view = self._active.view()
            if (
                selected.kind == "case"
                and self._historical_dir is not None
                and not self._busy()
            ):
                return replace(
                    view,
                    current_step_dir=self._historical_dir,
                    awaiting_verdict=False,
                    mode="",
                )
            return view
        round_node = parent_round(self._tree, selected.key) or selected
        total = len(round_node.children) if round_node.kind == "round" else 0
        return SessionView(
            phase="start",
            queue=tuple(
                QueueItem(child.case_id or child.label, child.status)
                for child in round_node.children
            ),
            current_step_dir=self._historical_dir,
            run_dir=None,
            mode="",
            error=None,
            round_id=round_node.round_id or round_node.label,
            environment=round_node.environment or "",
            progress=(0, total),
        )

    def _require_startable(self) -> TreeNode:
        node = find_node(self._tree, self._selected_key)
        round_node = parent_round(self._tree, self._selected_key) if node else None
        if round_node is None or not round_node.startable or not round_node.path:
            reason = (round_node.reason if round_node else None) or "round is not startable"
            raise HarnessError(
                code="ROUND_INVALID",
                message=reason,
                hint="fix the round with heimdall-qa validate, or pick a startable endpoint",
            )
        return round_node

    def _require_active(self) -> RoundSession:
        if self._active is None:
            raise HarnessError(
                code="VERDICT_INVALID",
                message="no step is waiting for a verdict",
                hint="start a round from the collection tree",
            )
        return self._active

    def _busy(self) -> bool:
        if self._active is None:
            return False
        return self._active.view().phase == "step"

    def _live_round_key(self) -> str | None:
        if self._active is None:
            return None
        rel = _posix_rel(self._active._round_path, self._root)
        return f"round:{rel}"

    def _selected_is_live(self, selected: TreeNode) -> bool:
        live = self._live_round_key()
        if live is None:
            return False
        if selected.key == live:
            return True
        return selected.kind == "case" and f"round:{selected.path}" == live

    def _same_live_round(self, node: TreeNode) -> bool:
        if self._active is None or self._active.view().phase == "start":
            return False
        return self._selected_is_live(node)

    def _focus_live_case(self, case_id: str) -> None:
        if self._active is None:
            return
        for index, item in enumerate(self._active.view().queue):
            if item.case_id == case_id:
                self._active.focus(index)
                return

    def _historical_for(self, node: TreeNode) -> Path | None:
        if node.kind != "case" or not node.case_id or not node.path:
            return None
        round_node = parent_round(self._tree, node.key)
        if round_node is None or not round_node.round_id:
            return None
        from heimdall_qa.campaign import find_latest_run

        run_dir = find_latest_run(self._runs_dir, round_node.round_id)
        if run_dir is None:
            return None
        return find_step_dir(run_dir, node.case_id)

    def _select_focus_path(self, path: Path) -> None:
        rel = _posix_rel(path, self._root)
        key = f"round:{rel}"
        if find_node(self._tree, key) is None:
            self._tree = self._tree + (
                inspect_round(
                    rel,
                    self._root,
                    self._runs_dir,
                    endpoint=None,
                    matrix=None,
                ),
            )
        self._selected_key = key

    def _overlay_live(self, tree: tuple[TreeNode, ...]) -> tuple[TreeNode, ...]:
        if self._active is None:
            return tree
        view = self._active.view()
        if view.phase == "start":
            return tree
        live = self._live_round_key()
        by_id = {item.case_id: item for item in view.queue}
        return _overlay_queue(tree, live, by_id)


def _overlay_queue(
    nodes: tuple[TreeNode, ...],
    live_key: str | None,
    by_id: dict[str, QueueItem],
) -> tuple[TreeNode, ...]:
    if live_key is None:
        return nodes
    updated: list[TreeNode] = []
    for node in nodes:
        children = _overlay_queue(node.children, live_key, by_id)
        if node.key == live_key:
            kids = []
            for child in children:
                item = by_id.get(child.case_id or child.label)
                if item is None:
                    kids.append(child)
                    continue
                kids.append(replace(child, status=item.status))
            children = tuple(kids)
        updated.append(replace(node, children=children))
    return tuple(updated)


def _next_unreviewed(selected: TreeNode) -> TreeNode | None:
    roots = selected.children if selected.kind in {"campaign", "folder"} else (selected,)
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
