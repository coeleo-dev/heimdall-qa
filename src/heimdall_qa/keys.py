"""The names of the nodes in the collection tree, built in one place.

A tree key is the handle every layer passes around. The client selects one, the plan
resolves one, the engine compares one to the round it is running, and the workspace
asks whether a case belongs to the round on screen. Until the client could show more
than one project at a time, a key could be spelled inline — `f"round:{rel}"` — for the
simple reason that there was one root and a relative path was unique inside it.

With several projects in one tree that stops being true. Two checkouts both have
`rounds/smoke.yaml`, so a key naming only the path names two nodes and `find_node`
returns whichever the walk reached first. Every key therefore carries the project it
belongs to, and the project is the *second* segment so the kind stays the first thing
a reader sees in a log line or a JSON payload.

**This module does not parse, deliberately.** A key is opaque once built, and a
consumer that needs the parts — `plan._campaign_of` wants the campaign behind a flow —
reads them off the `TreeNode`, which already carries them. Splitting the string back
apart would have to guess whether a colon belongs to a path, a matrix or a case id,
and a suite step's id is a whole sentence (`loop chain ×2`). Not parsing is what keeps
the separator a free choice instead of a contract, and it is why the campaign key can
be built from the campaign's own `id:` rather than from its path: moving the file into
a folder is then a move, and the selection survives it.

The builders are named `for_<kind>` and never bare `round`: the callers of this module
are full of local variables called `round`, `case` and `folder`, and a bare `round`
here would be shadowed at the one call site that mattered.
"""

from __future__ import annotations

from typing import Literal

#: Every kind of node the tree can hold. Defined here and imported by the collection
#: and the wire models, so one list is the source of all three.
#:
#: `directory` is a real folder under the project's content root, and it is a new kind
#: rather than a reuse of `folder` for a reason that would otherwise be a silent bug:
#: `folder` is already taken, and it means a campaign's grouping by matrix — "Fluxo" in
#: the UI. A folder you can drag a campaign into and a matrix the manifest declares are
#: different things that happen to share a word, so they get different kinds.
NodeKind = Literal["project", "directory", "campaign", "folder", "round", "case"]

#: The kind segments, named rather than written as literals at every call site, so the
#: wire format and the `NodeKind` above cannot drift apart.
PROJECT = "project"
DIRECTORY = "directory"
CAMPAIGN = "campaign"
FOLDER = "folder"
ROUND = "round"
CASE = "case"

_SEPARATOR = ":"


def for_project(project_id: str) -> str:
    """The root node of one registered project."""
    return _key(PROJECT, project_id)


def for_directory(project_id: str, rel: str) -> str:
    """A real folder under the project's content root."""
    return _key(DIRECTORY, project_id, rel)


def for_campaign(project_id: str, campaign_id: str) -> str:
    """A campaign manifest, keyed by its own `id:` and not by its path.

    The distinction is the whole reason a campaign can be moved. A key built from the
    path would change the moment the file went into a folder, so a move would read as
    "the old node vanished and a new one appeared" — the selection would be lost, and
    any open screen would be looking at a node that no longer exists.
    """
    return _key(CAMPAIGN, project_id, campaign_id)


def for_folder(project_id: str, campaign_id: str, matrix: str) -> str:
    """A campaign's grouping by matrix. Not a directory — see `NodeKind`."""
    return _key(FOLDER, project_id, campaign_id, matrix)


def for_round(project_id: str, rel: str) -> str:
    """A round file, content-relative. A round has no id of its own to key on."""
    return _key(ROUND, project_id, rel)


def for_case(project_id: str, rel: str, case_id: str) -> str:
    """One case of a round, or one visible step of a suite round.

    The two share a kind because the tree draws them the same way and the scopes
    table is what tells them apart — `step_kind` is set for a step and empty for a
    case file.
    """
    return _key(CASE, project_id, rel, case_id)


def for_step(project_id: str, rel: str, index: int) -> str:
    """One visible step of a suite round, keyed by its position.

    Positional because a step's label is not an identifier. It is built from the case
    the step runs, and a suite is free to run one case twice — two loops that read
    alike would then be two nodes with one key, which `find_node` resolves to the
    first, React resolves to whichever it reconciles, and the tree paints as one row
    wearing two statuses. The position is the only part of a step that is unique by
    construction, and it is the same part the queue is walked by.
    """
    return _key(CASE, project_id, rel, f"#step-{index}")


def _key(kind: str, *parts: str) -> str:
    return _SEPARATOR.join((kind, *parts))
