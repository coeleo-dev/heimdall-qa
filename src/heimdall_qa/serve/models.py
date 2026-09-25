"""The API's contract: the shape of what `/api/*` answers.

This is the boundary `contrib/architecture.md` §9.2 commits to. The Jinja panels read
a loose `dict[str, Any]` shaped like HTML — `body_open`, `case_prefix`,
`scope_labels` — which is fine for a template and useless to a client, and which
nothing could check. These models are that dict given a shape, so that a change to
`panel.py` that the client has not been taught about fails validation here rather
than rendering a blank pane in a window nobody is looking at.

Two rules keep this honest:

- **Nothing here reads anything.** `panel.py` stays the one place that opens a step
  directory; these are filled from its payloads. A second reader would be the second
  source of truth §9.2 exists to prevent.
- **Evidence is `Any`, state is typed.** A `response_body` is whatever the provider
  answered and must not be forced into a shape; a `phase` is one of six words and is.
  Where a field comes off disk it is typed loosely on purpose — coercing provider JSON
  into a `str` would turn a review tool into a validator of things it does not own.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict

from heimdall_qa.keys import NodeKind


class ApiModel(BaseModel):
    """A model that refuses fields it was not taught.

    `extra="forbid"` is the contract's teeth: `panel.py` gaining a key is a change the
    client must be told about, and a model that silently ignored it would let the two
    drift until someone opened the screen and found a panel empty.
    """

    model_config = ConfigDict(extra="forbid")


class HarnessErrorModel(ApiModel):
    """A `HarnessError` as the client needs it: what, why, and what to do."""

    code: str
    message: str
    hint: str = ""
    details: list[str] = []
    exit_code: int = 1


class QueueItemModel(ApiModel):
    """One case's place in the round's queue, and how it went.

    `key` is the collection node this row names, and it is the *reason* the client can
    open a step at all: a suite lists the same label six times, so an id is not a row.
    Empty when the row has no node behind it — a dimension-filtered subset across
    projects, a tail replayed by `from_step` — which the client reads as "nothing to
    open" rather than as "open the first row that shares this name".
    """

    case_id: str
    status: str
    current: bool = False
    reachable: bool = False
    key: str = ""
    #: `running`, `awaiting`, or empty. See `QueueItem.live` for why the mark travels
    #: with the row instead of being matched by the reader.
    live: str = ""


class SessionModel(ApiModel):
    """The live round's own view — the queue and where the review has got to.

    Absent when nothing is running; the engine's phase is the thing that says so, and
    this is empty rather than null so a client never has to branch on both.
    """

    phase: str = "idle"
    queue: list[QueueItemModel] = []
    run_dir: str = ""
    current_step_dir: str = ""
    mode: str = ""
    error: HarnessErrorModel | None = None
    round_id: str = ""
    environment: str = ""
    dimensions: list[str] = []
    progress: list[int] = [0, 0]
    focus_index: int = 0
    pending_index: int = 0
    can_prev: bool = False
    can_next: bool = False
    awaiting_verdict: bool = False


class EngineEventModel(ApiModel):
    """One line of the live feed, in the order it happened."""

    at: str
    kind: str
    text: str
    status: str = "info"


class EngineModel(ApiModel):
    """The status bar's whole world: phase, unit i/n, counts, feed, revision.

    `revision` is the dirty flag the event stream carries. `elapsed_ms` changes on
    every read and is deliberately *not* what revision counts — time passing is not
    an event, and a client that re-rendered for it would never stop.
    """

    phase: str
    scope: str
    plan_label: str
    unit_index: int
    unit_total: int
    unit_label: str
    unit_round: str
    unit_skips: list[str] = []
    case_total: int = 0
    cases_done: int = 0
    step_index: int = 0
    step_total: int = 0
    case_id: str = ""
    run_dir: str = ""
    counts: dict[str, int] = {}
    awaiting: bool = False
    busy: bool = False
    finished: bool = False
    error: HarnessErrorModel | None = None
    events: list[EngineEventModel] = []
    elapsed_ms: int = 0
    revision: int = 0


class TreeNodeModel(ApiModel):
    """One node of the collection, with the badge the tree draws on it.

    `fail_count` is how many descendants are failing. It is not a status a single node
    can carry and it is the number that makes a campaign of forty rounds readable, so
    it is computed where the tree is and shipped with it.
    """

    key: str
    kind: NodeKind
    label: str
    status: str
    children: list[TreeNodeModel] = []
    path: str | None = None
    round_id: str | None = None
    endpoint: str | None = None
    matrix: str | None = None
    case_id: str | None = None
    reason: str | None = None
    startable: bool = False
    expanded: bool = False
    environment: str = ""
    step_kind: str = ""
    fail_count: int = 0
    #: The project this node belongs to. Shipped so the client can offer "remove this
    #: project" on a project node without reading the id back out of the key — keys are
    #: opaque by design, and a client that split one would be a second implementer of a
    #: format this side owns. A project node carries its own id here.
    project: str = ""
    #: `running` or `awaiting` while this node holds the case a run is on, empty
    #: otherwise. Decided where the queue is painted and shipped, because the position
    #: is the only thing that can tell six identically-labelled suite rows apart.
    live: str = ""


class UnitCardModel(ApiModel):
    """The card under the tree: what is selected and what can be done with it.

    The scopes are the five of `plan.scopes_for`, and `scope_labels` is what each
    button says. They ship together because they are decided together: a suite step
    offers `case_forward` and must not call it "deste caso em diante".
    """

    key: str
    kind: NodeKind
    label: str
    status: str
    path: str | None = None
    round_id: str | None = None
    case_id: str | None = None
    endpoint: str | None = None
    environment: str = ""
    step_kind: str = ""
    startable: bool = False
    reason: str | None = None
    scopes: list[str] = []
    scope_labels: dict[str, str] = {}
    step_note: str = ""
    #: The selected round's last whole run, so "run it again" can show what last time
    #: cost. Read on demand, never on the stream: it changes once per run.
    previous: dict[str, Any] | None = None
    #: The project the selection belongs to. The card needs it for the two actions that
    #: are about a project rather than a unit — "Nova pasta" and "Mover para…" — and
    #: needs it as an id rather than as a key, which is opaque by design.
    project: str = ""


class ProbeRowModel(ApiModel):
    """One surface of a probe conference, with the two derived flags the table sorts by.

    `money` marks the surfaces where a wrong number is a wrong amount and not a wrong
    count; `matched` is whether the expectation held. Both are computed in `panel.py`
    because both need the surface's own shape, which the client does not have.
    """

    model_config = ConfigDict(extra="allow")

    id: Any = None
    jsonpath: Any = None
    expect: Any = None
    before: Any = None
    after: Any = None
    esperado: Any = None
    lido: Any = None
    money: bool = False
    matched: bool = False


class StepModel(ApiModel):
    """A step's evidence, read off disk: request, response, packs, logs, probe, verdict.

    This is the pane the reviewer spends the review in, and every field is one of the
    things it draws. The `request_*`/`response_*` fields are `Any` because they are the
    provider's bytes with the harness's framing removed; the rest are the harness's own
    and are typed.
    """

    request_doc: dict[str, Any] = {}
    response_doc: dict[str, Any] = {}
    timing: dict[str, Any] = {}
    packs: list[dict[str, Any]] = []
    pack_alerts: list[dict[str, Any]] = []
    pack_ok: list[dict[str, Any]] = []
    logs_sources: list[dict[str, Any]] = []
    logs_timeline: list[dict[str, Any]] = []
    probe: dict[str, Any] = {}
    probe_rows: list[ProbeRowModel] = []
    is_probe: bool = False
    case_label: str = "passo"
    http_status: Any = None
    elapsed_ms: int | None = None
    request_method: Any = ""
    request_url: Any = ""
    request_headers: Any = None
    request_body: Any = None
    response_headers: Any = None
    response_body: Any = None
    has_http: bool = False
    pause_reason: str = ""
    #: True only when *this* step is the one a verdict applies to. See
    #: `panel._awaiting_verdict` for why the field is not simply the session's flag.
    awaiting_verdict: bool = False
    recorded_verdict: dict[str, Any] = {}
    body_open: bool = False


class RunAggregateModel(ApiModel):
    """The end of a run: the KPIs, and the cases worth reopening."""

    summary: dict[str, Any] = {}
    failed_cases: list[dict[str, Any]] = []
    run_path: str = ""


class SourceFindingModel(ApiModel):
    """One `ValidationFinding`, verbatim, for the editor to show beside the line.

    The four parts are shipped rather than one rendered block because the screen puts
    them in different places — the `code` on a chip, the `message` next to the cursor,
    the `fix` under the list — and a paragraph would have to be parsed back apart by
    whoever wrote the client. `why` rides along so the reading is available without
    `validate --explain`.
    """

    code: str
    where: str
    message: str
    fix: str
    why: str


class SourceModel(ApiModel):
    """A contract, round, campaign or suite, as the editor holds it.

    `findings` and `valid` are computed on the way out, not stored: `source.py` runs the
    same validator the CLI runs, on the file as it now stands on disk. Saving and
    checking being one operation is what makes `valid` a fact rather than an opinion —
    and it is why an invalid save is answered with a 200 and this shape, not with a
    refusal that would have thrown the edit away.
    """

    path: str
    kind: str
    text: str
    editable: bool
    valid: bool
    findings: list[SourceFindingModel] = []


class ApiErrorModel(ApiModel):
    """A refusal, in the shape the client has to act on.

    `comment_required` is the one error the screen must not just display: a reprove
    without a comment has to come back with the box still holding what the reviewer
    wrote, so the client needs to know to keep it rather than clear the form. That
    distinction used to be a substring match on the message in `app.py`; here it is a
    field, which is the whole argument for a declared contract.
    """

    error: HarnessErrorModel
    comment_required: bool = False


class StreamModel(ApiModel):
    """What `GET /api/events` pushes: the engine, and which screen it belongs to.

    `pane` and `selected` ride along because the client needs them to decide whether a
    revision is "the status bar moved" (redraw the strip) or "the step changed"
    (re-read the whole bootstrap). Shipping them costs two strings and saves a fetch
    per feed line.
    """

    engine: EngineModel
    pane: str = ""
    selected: str = ""


class ProjectModel(ApiModel):
    """One project the client knows about, whether it is on screen or only remembered.

    `open` is not the same question as "is it in this list": the root a client was
    launched for is drawn whether or not it was ever registered, and a project closed
    in this window is gone from the tree while its line stays in the file until it is
    removed. Both are shown, because "why is this project not in my tree" deserves an
    answer that is not a shrug.
    """

    id: str
    name: str
    root: str
    open: bool = False


class ProjectsModel(ApiModel):
    """`GET /api/projects`: the registry file, and what it holds.

    The path is shipped because the dialog has to be able to say where the client keeps
    its memory. That file is the **reviewer's** configuration and not the project's —
    two people may open different sets — so "where did it go" wants a path in the
    answer, and this is the only surface that can give it.
    """

    registry: str
    projects: list[ProjectModel] = []


class McpModel(ApiModel):
    """The embedded MCP server, as the Server dialog draws it.

    `state` is the honest three-way answer — `running`, `stopped`, `error` — and
    `enabled` is derived from it rather than stored, so a switch that reads "on" while
    the socket is down cannot be represented. `config_snippet` is the block the reader
    pastes into their MCP client, generated from the host and port this server actually
    bound, so it cannot drift from what is listening.
    """

    enabled: bool
    host: str
    port: int
    path: str
    url: str
    state: str
    error: HarnessErrorModel | None = None
    config_snippet: str = ""


class LabelsModel(ApiModel):
    """The review screen's Portuguese words, shipped once per bootstrap.

    They live in Python and not in the client for the reason `serve/labels.py` gives:
    they are the words the harness speaks, and the CLI, the machine output and the
    screen must not disagree about them.
    """

    status_label: dict[str, str]
    status_pill: dict[str, str]
    kind_label: dict[str, str]
    step_kind_label: dict[str, str]
    phase_label: dict[str, str]
    scope_label: dict[str, str]
    scope_running: dict[str, str]
    step_note: dict[str, str]
    step_scope_label: dict[str, str]


class BootstrapModel(ApiModel):
    """Everything `GET /api/bootstrap` answers: the whole screen in one read.

    The tree, the selection, the pane, the engine and — when one applies — the step or
    the run aggregate. The event stream carries the engine only, and the client fetches
    this again when the revision says something beyond the engine has moved.
    """

    tree: list[TreeNodeModel] = []
    selected: str = ""
    pane: str = "start"
    engine: EngineModel
    session: SessionModel = SessionModel()
    unit: UnitCardModel
    step: StepModel | None = None
    run: RunAggregateModel | None = None
    labels: LabelsModel
    #: The next case the reviewer has not looked at, for the "next" affordance a
    #: campaign ends with. `None` is the honest answer at the end of a campaign.
    next_unreviewed: str | None = None
    #: The tree row a parked plan is waiting on, or `""`. Shipped so a screen that is
    #: not the parked step can offer to go back to it, which is what lets a reviewer
    #: open any other case while a run waits for a verdict.
    pending_key: str = ""
    #: The engine's own error, or the session's if the engine has none. One field, so
    #: the client renders one banner.
    error: HarnessErrorModel | None = None
