import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiRefusal,
  addProject,
  cancelPlan,
  createFolder,
  fetchBootstrap,
  focusStep,
  moveCampaign,
  removeProject,
  selectNode,
  startPlan,
  submitVerdict,
} from "@/lib/api";
import { openStream } from "@/lib/sse";
import type { Bootstrap, HarnessError, StreamState, VerdictStatus } from "@/types";

export interface Harness {
  bootstrap: Bootstrap | null;
  error: HarnessError | null;
  /** A mutation is in flight; the shell shows it rather than freezing silently. */
  pending: boolean;
  /** The stream is connected. A frozen screen with no signal is how time is wasted. */
  live: boolean;
  refresh: () => Promise<void>;
  /** Re-read the screen *and* reopen the stream, for when the socket has dropped. */
  reconnect: () => void;
  select: (key: string) => Promise<void>;
  start: (scope: string, mode: string, node?: string) => Promise<void>;
  verdict: (status: VerdictStatus, comment: string, continueRound: boolean) => Promise<boolean>;
  cancel: () => Promise<void>;
  focus: (index: number) => Promise<void>;
  /** Make a folder under `campaigns/`; the tree that comes back has it in it. */
  newFolder: (path: string, project: string) => Promise<void>;
  /** Move a campaign into another folder. The key survives, because it is the id. */
  move: (path: string, directory: string, project: string) => Promise<void>;
  openProject: (root: string) => Promise<void>;
  closeProject: (id: string) => Promise<void>;
  clearError: () => void;
}

function asHarnessError(error: unknown): HarnessError {
  if (error instanceof ApiRefusal) return error.payload.error;
  const message = error instanceof Error ? error.message : String(error);
  return {
    code: "TRANSPORT",
    message,
    hint: "the harness's own process may have stopped",
    details: [],
    exit_code: 1,
  };
}

/**
 * One place that owns the screen and the stream.
 *
 * Every frame the server pushes is a state change: the stream blocks on the engine's
 * own revision counter and stays silent when it has not moved, so **a frame means
 * re-read the screen**. That is the whole rule, and it is written here because it was
 * not always the rule.
 *
 * It used to compare the frame's `engine` against the last one and only re-read when
 * some field of it had moved, patching `engine` in place otherwise, on the argument
 * that the status bar was the only thing that could have changed. That argument is
 * wrong: the thing that lands when a case finishes is a *file*, and it shows up in the
 * tree, in the session queue the progress bar segments read and in the step pane —
 * none of which ride in the frame. A frame whose engine happened to read the same then
 * left all of it stale, and only a click that re-read the bootstrap put it right, which
 * is exactly the "not real time" a reviewer reports. The revision is the frame's
 * identity and is monotonic, so it is the only thing worth comparing, and only to skip
 * a repeat of a frame already applied.
 */
export function useHarness(): Harness {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [error, setError] = useState<HarnessError | null>(null);
  const [pending, setPending] = useState(false);
  const [live, setLive] = useState(false);
  /**
   * Bumped to tear the stream down and open a new one.
   *
   * A dropped stream was previously unrecoverable from the window: the "Recarregar"
   * button re-read the bootstrap and nothing else, so the screen could be made current
   * by hand while the connection that keeps it current stayed dead, and every later
   * change was invisible again. The socket is part of what "reload" means.
   */
  const [streamKey, setStreamKey] = useState(0);

  const latest = useRef<StreamState | null>(null);
  const refreshing = useRef(false);
  /**
   * A refresh was asked for while one was already in flight.
   *
   * Frames arrive faster than a bootstrap can be read — a campaign landing cases emits
   * them in a burst — and dropping the ones that overlap would leave the screen on the
   * first frame of the burst for good, because the next frame only ever asks about a
   * *later* revision. Coalescing instead means the last request of a burst is always
   * served.
   */
  const refreshAgain = useRef(false);
  /**
   * Which answer wins when two are in flight.
   *
   * Every write answers with the whole bootstrap, and so does every read — a stream
   * frame is the server saying its revision moved, not a payload. During a run the
   * stream emits in bursts, so a click and a frame are routinely out at the same time
   * and their two answers race. When the frame's read is the last to land, the screen
   * goes back to the selection that was current when that read *started*: the tree
   * highlights the case that was clicked and the pane shows the one before it, and the
   * next frame does not repair it because all it says is that a revision moved. That is
   * the whole of "sometimes it will not let me open a case to review".
   *
   * Two counters, because there are two ways to lose the race:
   *
   * - `mutationEpoch` is bumped when a write answers. A read that was in flight while
   *   that happened is stale by definition — it described the screen as it was before
   *   the write — and is dropped. This is the one that makes a click beat a frame.
   * - `issued`/`applied` orders reads against each other, so two reads landing out of
   *   order cannot put the older screen on top.
   */
  const issued = useRef(0);
  const applied = useRef(0);
  const mutationEpoch = useRef(0);
  const refresh = useCallback(async () => {
    if (refreshing.current) {
      refreshAgain.current = true;
      return;
    }
    refreshing.current = true;
    try {
      do {
        refreshAgain.current = false;
        const epoch = mutationEpoch.current;
        const mine = ++issued.current;
        try {
          const next = await fetchBootstrap();
          if (epoch === mutationEpoch.current && mine >= applied.current) {
            applied.current = mine;
            setBootstrap(next);
          }
          if (next.error) setError(next.error);
        } catch (failure) {
          setError(asHarnessError(failure));
        }
      } while (refreshAgain.current);
    } finally {
      refreshing.current = false;
      setLive(true);
    }
  }, []);

  const act = useCallback(
    async (run: () => Promise<Bootstrap>, optimisticClear = true) => {
      setPending(true);
      if (optimisticClear) setError(null);
      try {
        const next = await run();
        // A write's answer is authored, not observed: it is what the reviewer asked
        // for, so it is applied and everything already in flight is invalidated.
        mutationEpoch.current += 1;
        applied.current = ++issued.current;
        setBootstrap(next);
      } catch (failure) {
        const harnessError = asHarnessError(failure);
        setError(harnessError);
        // A refusal is not always a dead end — a reprove without a comment is the
        // form's problem, and the caller decides what to do with it — so the error is
        // reported and the promise resolves rather than rejects.
        throw harnessError;
      } finally {
        setPending(false);
      }
    },
    [],
  );

  const select = useCallback(
    async (key: string) => {
      try {
        await act(() => selectNode(key));
      } catch {
        /* reported through `error` */
      }
    },
    [act],
  );

  const start = useCallback(
    async (scope: string, mode: string, node?: string) => {
      try {
        await act(() => startPlan(scope, mode, node));
      } catch {
        /* reported through `error` */
      }
    },
    [act],
  );

  const cancel = useCallback(async () => {
    try {
      await act(() => cancelPlan());
    } catch {
      /* reported through `error` */
    }
  }, [act]);

  const focus = useCallback(
    async (index: number) => {
      try {
        await act(() => focusStep(index));
      } catch {
        /* reported through `error` */
      }
    },
    [act],
  );

  /** `true` when the verdict landed; `false` leaves the form holding the comment. */
  const verdict = useCallback(
    async (status: VerdictStatus, comment: string, continueRound: boolean) => {
      try {
        await act(() => submitVerdict(status, comment, continueRound));
        return true;
      } catch {
        return false;
      }
    },
    [act],
  );

  // The four collection mutations all answer with the whole bootstrap, so they go
  // through `act` like every other one: the tree the server hands back *is* the update,
  // and a client that re-indexed locally would be a second implementation of it.
  const newFolder = useCallback(
    async (path: string, project: string) => {
      try {
        await act(() => createFolder(path, project));
      } catch {
        /* reported through `error`, and the dialog keeps the reader's text */
      }
    },
    [act],
  );

  const move = useCallback(
    async (path: string, directory: string, project: string) => {
      try {
        await act(() => moveCampaign(path, directory, project));
      } catch {
        /* reported through `error` */
      }
    },
    [act],
  );

  const openProject = useCallback(
    async (root: string) => {
      // Deliberately *not* swallowing the refusal here: `REGISTRY_NOT_A_PROJECT` is the
      // gate telling the reader what a project looks like, and the dialog is the only
      // place that sentence can be read.
      await act(() => addProject(root));
    },
    [act],
  );

  const closeProject = useCallback(
    async (id: string) => {
      await act(() => removeProject(id));
    },
    [act],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const close = openStream({
      onState: (state) => {
        setLive(true);
        const previous = latest.current;
        latest.current = state;
        if (!previous) {
          // The mount refresh is already in flight; this frame is the same state it
          // will come back with.
          void refresh();
          return;
        }
        if (state.engine.revision === previous.engine.revision) {
          // The stream coalesces revisions, so the same one can arrive twice. It is
          // the only frame that is not news, and it costs nothing to skip.
          return;
        }
        void refresh();
      },
      // The socket was down, so the frames sent during the gap are gone. Re-reading
      // is the only way to find out what moved while the client was not listening.
      onResync: () => void refresh(),
      onLost: () => setLive(false),
    });
    return close;
  }, [refresh, streamKey]);

  const reconnect = useCallback(() => {
    setStreamKey((current) => current + 1);
    setError(null);
    void refresh();
  }, [refresh]);

  return {
    bootstrap,
    error,
    pending,
    live,
    refresh,
    reconnect,
    select,
    start,
    verdict,
    cancel,
    focus,
    newFolder,
    move,
    openProject,
    closeProject,
    clearError: () => setError(null),
  };
}
