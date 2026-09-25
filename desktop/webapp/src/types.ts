/**
 * The API's contract, in TypeScript.
 *
 * This mirrors `src/heimdall_qa/serve/models.py` field for field. It is a hand-written
 * mirror on purpose — generating it would mean running a Python step during `npm ci`
 * and the bundle has to build with nothing but Node. The mirror is kept honest from
 * the other side: the Python models are `extra="forbid"`, so a field that exists here
 * and not there fails validation before it can render a blank pane.
 */

export type NodeKind = "project" | "directory" | "campaign" | "folder" | "round" | "case";

export type Pane = "start" | "review" | "historical" | "done" | "campaign";

/**
 * The editor's pane. It is *not* in `Pane` on purpose: `pane` is the server's word for
 * what the screen is showing, and opening a file changes nothing about a run. The
 * client draws the editor over the server's pane instead, so a client-side surface can
 * never be mistaken for state the engine has.
 */
export type EditableKind = "contract" | "round" | "campaign" | "suite";

export interface HarnessError {
  code: string;
  message: string;
  hint: string;
  details: string[];
  exit_code: number;
}

export interface ApiError {
  error: HarnessError;
  /** The one refusal the form must not clear: a reprove owes a comment. */
  comment_required: boolean;
}

export interface QueueItem {
  case_id: string;
  status: string;
  current: boolean;
  reachable: boolean;
  /**
   * The collection node this row names. The server decides it, because a row is not an
   * id: a suite lists the same step six times, so "the node for this case id" is a
   * question only the queue's own position can answer. Empty means there is nothing to
   * open, and the row is drawn inert rather than pointing at a neighbour.
   */
  key: string;
  /** `running`, `awaiting`, or empty. Shipped with the row for the same reason. */
  live: string;
}

export interface SessionView {
  phase: string;
  queue: QueueItem[];
  run_dir: string;
  current_step_dir: string;
  mode: string;
  error: HarnessError | null;
  round_id: string;
  environment: string;
  dimensions: string[];
  /** `[index, total]` — a two-element tuple in Python, an array here. */
  progress: number[];
  focus_index: number;
  pending_index: number;
  can_prev: boolean;
  can_next: boolean;
  awaiting_verdict: boolean;
}

export interface EngineEvent {
  at: string;
  kind: string;
  text: string;
  status: string;
}

export interface EngineView {
  phase: string;
  scope: string;
  plan_label: string;
  unit_index: number;
  unit_total: number;
  unit_label: string;
  unit_round: string;
  unit_skips: string[];
  case_total: number;
  cases_done: number;
  step_index: number;
  step_total: number;
  case_id: string;
  run_dir: string;
  counts: Record<string, number>;
  awaiting: boolean;
  busy: boolean;
  finished: boolean;
  error: HarnessError | null;
  events: EngineEvent[];
  elapsed_ms: number;
  /** The dirty flag the event stream carries; equal values mean "nothing happened". */
  revision: number;
}

export interface TreeNode {
  key: string;
  kind: NodeKind;
  label: string;
  status: string;
  children: TreeNode[];
  path: string | null;
  round_id: string | null;
  endpoint: string | null;
  matrix: string | null;
  case_id: string | null;
  reason: string | null;
  startable: boolean;
  expanded: boolean;
  environment: string;
  step_kind: string;
  fail_count: number;
  /**
   * The project the node belongs to. Shipped so an action on a node can name its
   * project without reading the id back out of the key — the key is opaque here by
   * design, and a client that split one would be a second implementer of its format.
   */
  project: string;
  /**
   * `running` or `awaiting` while this node holds the case a run is on, empty
   * otherwise. Decided on the server, where the queue's position is known: matching a
   * case id against the rows lights up every one of a suite's identically-labelled
   * steps and so says nothing about which one is on the wire.
   */
  live: string;
}

export interface UnitCard {
  key: string;
  kind: NodeKind;
  label: string;
  status: string;
  path: string | null;
  round_id: string | null;
  case_id: string | null;
  endpoint: string | null;
  environment: string;
  step_kind: string;
  startable: boolean;
  reason: string | null;
  scopes: string[];
  scope_labels: Record<string, string>;
  step_note: string;
  previous: Record<string, unknown> | null;
  project: string;
}

/** A probe surface. `extra="allow"` on the Python side, so anything may be here. */
export interface ProbeRow {
  id?: unknown;
  jsonpath?: unknown;
  expect?: unknown;
  before?: unknown;
  after?: unknown;
  esperado?: unknown;
  lido?: unknown;
  money: boolean;
  matched: boolean;
  [key: string]: unknown;
}

export interface PackResult {
  pack_id?: string;
  status?: string;
  [key: string]: unknown;
}

export interface LogSource {
  id?: string;
  text?: string;
  [key: string]: unknown;
}

export interface StepView {
  request_doc: Record<string, unknown>;
  response_doc: Record<string, unknown>;
  timing: Record<string, unknown>;
  packs: PackResult[];
  pack_alerts: PackResult[];
  pack_ok: PackResult[];
  logs_sources: LogSource[];
  logs_timeline: Record<string, unknown>[];
  probe: Record<string, unknown>;
  probe_rows: ProbeRow[];
  is_probe: boolean;
  case_label: string;
  http_status: unknown;
  /** Milliseconds, or `null` when the step recorded no timing. A number, not "12 ms". */
  elapsed_ms: number | null;
  request_method: unknown;
  request_url: unknown;
  request_headers: unknown;
  request_body: unknown;
  response_headers: unknown;
  response_body: unknown;
  has_http: boolean;
  pause_reason: string;
  awaiting_verdict: boolean;
  recorded_verdict: Record<string, unknown>;
  body_open: boolean;
}

export interface RunAggregate {
  summary: Record<string, unknown>;
  /** Each failure as a row: the id to read, the verdict, and the node to reopen. */
  failed_cases: FailedCase[];
  run_path: string;
  /**
   * The row this aggregate belongs to. A finished round is no longer necessarily the
   * one the engine ran last — a campaign roll-up opens any of its rounds — so the
   * header reads its name from here and not from `session.round_id`, which is
   * whatever the engine still remembers.
   */
  unit_key: string;
  unit_label: string;
  unit_kind: string;
}

/**
 * One case a finished run decided against.
 *
 * `key` is the row to reopen, resolved on the server by walking the round's own cases
 * forward — a case id cannot name a row of a suite, which lists the same step six
 * times. `reason` names the packs that decided against it and `step_dir` is the
 * directory the evidence is in, which is what the row falls back to when the round has
 * changed shape since the run.
 */
export interface FailedCase {
  case_id: string;
  status: string;
  key?: string;
  reason?: string;
  step_dir?: string;
}

/** One round's latest run, as a row of a roll-up table. */
export interface RoundRunRow {
  key: string;
  label: string;
  round_id: string;
  endpoint: string;
  run_path: string;
  /** A completed run exists. A run still in flight has a path and empty counts. */
  found: boolean;
  status: string;
  counts: Record<string, number>;
  packs: Record<string, number>;
  coverage_pct: number;
  latency_ms: Record<string, number>;
  mode: string;
  /** The run directory's name: `<stamp>-<round id>`. */
  stamp: string;
  logs_incomplete: number;
  failed: number;
}

/**
 * A campaign, folder, directory or project's rounds, and their additive totals.
 *
 * `totals` holds only what can honestly be summed — case counts, pack alerts, runs
 * that never happened. Coverage and latency ride on each row and are never summed: a
 * p95 over a campaign is not the average of forty p95s, so the table shows each and
 * claims none.
 */
export interface RollupRuns {
  totals: Record<string, number>;
  units: RoundRunRow[];
  rounds_total: number;
  rounds_run: number;
}

/**
 * One `ValidationFinding`, field for field.
 *
 * The four parts arrive whole rather than as one rendered block because the screen puts
 * them in different places — the code on a chip, the message next to the list, the fix
 * under it — and a paragraph would have to be parsed back apart here.
 */
export interface SourceFinding {
  code: string;
  where: string;
  message: string;
  fix: string;
  why: string;
}

/** A file the editor holds, with what `validate` made of it as it stands on disk. */
export interface SourceDocument {
  path: string;
  kind: string;
  text: string;
  editable: boolean;
  /** False means `findings` is non-empty. Both are sent; they are one fact. */
  valid: boolean;
  findings: SourceFinding[];
}

/** The Portuguese words, shipped once per bootstrap rather than baked into the client. */
export interface Labels {
  status_label: Record<string, string>;
  status_pill: Record<string, string>;
  kind_label: Record<string, string>;
  step_kind_label: Record<string, string>;
  phase_label: Record<string, string>;
  scope_label: Record<string, string>;
  scope_running: Record<string, string>;
  step_note: Record<string, string>;
  step_scope_label: Record<string, string>;
}

export interface Bootstrap {
  tree: TreeNode[];
  selected: string;
  pane: Pane;
  engine: EngineView;
  session: SessionView;
  unit: UnitCard;
  step: StepView | null;
  run: RunAggregate | null;
  /** The per-round table a roll-up selection carries, or `null` for any other pane. */
  rollup: RollupRuns | null;
  labels: Labels;
  next_unreviewed: string | null;
  /** The tree row a parked plan is waiting on, or `""`. See the server model. */
  pending_key: string;
  error: HarnessError | null;
}

/** What `GET /api/events` pushes. */
export interface StreamState {
  engine: EngineView;
  pane: Pane;
  selected: string;
}

/** One project the client knows about, whether it is on screen or only remembered. */
export interface ProjectInfo {
  id: string;
  name: string;
  /** Absolute, because the registry stores a path and the dialog has to show it. */
  root: string;
  open: boolean;
}

export interface ProjectsResponse {
  /** The registry file itself: user configuration, not project state. */
  registry: string;
  projects: ProjectInfo[];
}

/**
 * The embedded MCP server, as the Server dialog draws it.
 *
 * `state` is the three-way truth — `running`, `stopped`, `error` — and `enabled` is
 * derived from it on the server, so a switch that reads "on" while the socket is down
 * cannot be represented. `config_snippet` is empty while it is down, on purpose:
 * handing over a URL that answers nothing is advice to fail later.
 */
export interface McpState {
  enabled: boolean;
  host: string;
  port: number;
  path: string;
  url: string;
  state: string;
  error: HarnessError | null;
  config_snippet: string;
}

/**
 * The bundled demo project, as the Server dialog draws it.
 *
 * The same three-way `state` the MCP switch carries, because it is the same kind of
 * thing, plus where the materialized tree landed and the id it is registered under.
 * `root` does not exist until the switch has been on once, so the dialog reads
 * `state` before promising a path.
 */
export interface DemoState {
  enabled: boolean;
  state: string;
  host: string;
  port: number;
  base_url: string;
  root: string;
  project_id: string;
  log_path: string;
  error: HarnessError | null;
}

export type VerdictStatus = "pass" | "fail" | "fail_stop";
