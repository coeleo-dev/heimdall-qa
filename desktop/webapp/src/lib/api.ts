import type {
  ApiError,
  Bootstrap,
  DemoState,
  McpState,
  ProjectsResponse,
  RunAggregate,
  SourceDocument,
  StepView,
  VerdictStatus,
} from "@/types";
import { apiToken } from "@/lib/token";

/**
 * The client for the harness's own process.
 *
 * Every call is same-origin and relative: the window was pointed at
 * `http://127.0.0.1:<port>/app/`, and the API is that same origin. There is no base
 * URL to configure, which is the point — the port is ephemeral and chosen at start.
 */

export const TOKEN_HEADER = "X-Heimdall-Token";

/** An error the harness refused with, carrying the code the caller has to branch on. */
export class ApiRefusal extends Error {
  readonly status: number;
  readonly payload: ApiError;

  constructor(status: number, payload: ApiError) {
    super(payload.error.message || `HTTP ${status}`);
    this.name = "ApiRefusal";
    this.status = status;
    this.payload = payload;
  }

  get code(): string {
    return this.payload.error.code;
  }

  get commentRequired(): boolean {
    return this.payload.comment_required;
  }
}

function headers(json: boolean): HeadersInit {
  const out: Record<string, string> = {};
  const token = apiToken();
  if (token) out[TOKEN_HEADER] = token;
  if (json) out["Content-Type"] = "application/json";
  return out;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, headers: { ...headers(false), ...init.headers } });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    // The refusal's *shape* is part of the contract: a caller that gets a status code
    // and a string has to guess what to do, and guessing is what the models remove.
    const payload: ApiError = body ?? {
      error: {
        code: "TRANSPORT",
        message: `HTTP ${response.status}`,
        hint: "the harness did not answer in its own shape",
        details: [],
        exit_code: 1,
      },
      comment_required: false,
    };
    throw new ApiRefusal(response.status, payload);
  }
  return body as T;
}

function post<T>(path: string, body: unknown = {}): Promise<T> {
  return call<T>(path, { method: "POST", headers: headers(true), body: JSON.stringify(body) });
}

/** The whole screen in one read. */
export function fetchBootstrap(): Promise<Bootstrap> {
  return call<Bootstrap>("/api/bootstrap");
}

/** The step on screen, re-read on demand rather than carried in every frame. */
export function fetchStep(): Promise<StepView> {
  return call<StepView>("/api/step");
}

/** The end-of-run aggregate. */
export function fetchRun(): Promise<RunAggregate> {
  return call<RunAggregate>("/api/run");
}

export function selectNode(key: string): Promise<Bootstrap> {
  return post<Bootstrap>("/api/select", { key });
}

export function startPlan(scope: string, mode: string, node?: string): Promise<Bootstrap> {
  return post<Bootstrap>("/api/start", { scope, mode, node: node ?? null });
}

export function submitVerdict(
  status: VerdictStatus,
  comment: string,
  continueRound: boolean,
): Promise<Bootstrap> {
  return post<Bootstrap>("/api/verdict", {
    status,
    comment,
    continue_round: continueRound,
  });
}

export function cancelPlan(): Promise<Bootstrap> {
  return post<Bootstrap>("/api/cancel");
}

export function focusStep(index: number): Promise<Bootstrap> {
  return post<Bootstrap>("/api/focus", { index });
}

/**
 * A file the reviewer wrote, for the editor.
 *
 * The read answers with the findings too, so a round that is already broken shows why
 * the moment it opens rather than after a save. That costs a parse of the file plus its
 * includes, and it happens when a person clicks — not on a timer.
 */
export function fetchSource(path: string): Promise<SourceDocument> {
  return call<SourceDocument>(`/api/source?path=${encodeURIComponent(path)}`);
}

/**
 * Save, and get back what `validate` makes of what was saved.
 *
 * A save that leaves an invalid document is a success with findings, not a refusal: the
 * text was written, and the caller has to draw the findings. Throwing here would make
 * the editor revert a change it had already made on disk, which is the one outcome
 * worse than an invalid file.
 */
export function saveSource(path: string, text: string): Promise<SourceDocument> {
  return post<SourceDocument>("/api/source", { path, text });
}

/**
 * A real folder under `campaigns/`, so campaigns can be sorted rather than only
 * listed. The whole bootstrap comes back because the point of the action is visible in
 * the tree — an empty folder has nothing in it to notice, and a client told "created"
 * with an unchanged tree would be right to think it failed.
 *
 * `project` names which project the folder belongs to. With several open,
 * `campaigns/ingest/` is ambiguous, and the node under the pointer is the only honest
 * answer — it is not necessarily the selected one.
 */
export function createFolder(path: string, project: string): Promise<Bootstrap> {
  return post<Bootstrap>("/api/folder", { path, project });
}

/** Move a campaign into another folder. Only campaigns move; the server refuses the rest. */
export function moveCampaign(path: string, directory: string, project: string): Promise<Bootstrap> {
  return post<Bootstrap>("/api/move", { path, directory, project });
}

/** The registry and its entries, with the ones the tree is drawing marked. */
export function fetchProjects(): Promise<ProjectsResponse> {
  return call<ProjectsResponse>("/api/projects");
}

/**
 * Open a directory as a project: registered in the file *and* drawn in the tree.
 *
 * The gate is the server's, and it is the point: a directory that does not look like a
 * project is refused by name before anything is written, so a slip in a file dialog
 * cannot become a walk over a home directory.
 */
export function addProject(root: string): Promise<Bootstrap> {
  return post<Bootstrap>("/api/projects", { root });
}

/** Close a project. No file is deleted; the client forgets a root. */
export function removeProject(id: string): Promise<Bootstrap> {
  return call<Bootstrap>(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function fetchMcp(): Promise<McpState> {
  return call<McpState>("/api/mcp");
}

/**
 * Flip the embedded MCP server.
 *
 * A refused bind is a 200 with `state: "error"` and the reason, not a thrown refusal:
 * the switch was flipped and the port was taken, which is an answer to the request and
 * not a failure of it. The dialog draws the error where the switch is.
 */
export function setMcp(enabled: boolean): Promise<McpState> {
  return post<McpState>("/api/mcp", { enabled });
}

/** Whether the bundled demo project is up, and where its tree was materialized. */
export function fetchDemo(): Promise<DemoState> {
  return call<DemoState>("/api/demo");
}

/**
 * Flip the bundled demo.
 *
 * Turning it on starts the mock and materializes the sample project around the port it
 * bound, so the answer names a `root` and a `project_id` that the tree now holds —
 * the caller refreshes the bootstrap to draw it. Turning it off stops the socket and
 * leaves the files, which is why the answer still carries both.
 */
export function setDemo(enabled: boolean): Promise<DemoState> {
  return post<DemoState>("/api/demo", { enabled });
}
