import { useEffect, useState } from "react";
import { Ban, CornerDownRight } from "lucide-react";

import { RunProgressLine } from "@/components/RunProgress";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { EngineView, Labels, SessionView } from "@/types";
/**
 * The clock, counted locally between frames.
 *
 * The server's `elapsed_ms` is authoritative and arrives whenever the engine moves —
 * which, on a thirty-second probe, is once every thirty seconds. A number frozen at
 * `1200 ms` while a request is in flight reads as "it hung", so the time since the
 * last frame is added to the server's total. `revision` is the frame: two frames with
 * the same revision are the same instant, and counting from one of them twice would
 * inflate the clock.
 *
 * The counter restarts at zero on every revision rather than accumulating, so a
 * delayed tick cannot leave the total ahead of the truth.
 */
function useElapsed(engine: EngineView): number {
  const [extra, setExtra] = useState(0);

  useEffect(() => {
    setExtra(0);
    if (!engine.busy) return undefined;
    const frameAt = Date.now();
    const timer = window.setInterval(() => setExtra(Date.now() - frameAt), 1_000);
    return () => window.clearInterval(timer);
  }, [engine.revision, engine.busy]);

  return engine.elapsed_ms + extra;
}

const PHASE_VARIANT: Record<string, "pass" | "fail" | "warn" | "http" | "default"> = {
  idle: "default",
  running: "http",
  awaiting: "warn",
  done: "pass",
  cancelled: "warn",
  error: "fail",
};

/** A count with the colour of what it counts. The three-numeral `12/0/0` this
 *  replaces was a puzzle: it took a legend to read, and the legend was in the docs. */
function Tally({
  label,
  count,
  color,
}: {
  label: string;
  count: number;
  color: string;
}) {
  return (
    <span
      className="tabular flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground"
      title={label}
    >
      <span className="size-1.5 rounded-full" style={{ background: color }} />
      {count}
    </span>
  );
}

export function StatusBar({
  engine,
  session,
  labels,
  environment,
  selected,
  pendingKey,
  onSelect,
  onCancel,
  cancelDisabled,
}: {
  engine: EngineView;
  session: SessionView;
  labels: Labels;
  environment: string;
  /** What the tree has selected right now, so the jump is only offered when it would
   *  actually move the screen. */
  selected: string;
  /** The row a parked plan waits on, or `""`. */
  pendingKey: string;
  onSelect: (key: string) => void;
  onCancel: () => void;
  cancelDisabled: boolean;
}) {
  const elapsed = useElapsed(engine);
  const started =
    Boolean(engine.unit_round) ||
    engine.busy ||
    ["done", "cancelled", "error"].includes(engine.phase);

  const decided = engine.cases_done + (engine.counts.skip ?? 0);
  const total = engine.case_total || engine.step_total;
  const current = engine.case_id;

  return (
    <footer className="shrink-0 border-t border-border bg-statusbar">
      {/* The run as one unbroken line across the whole window. It is the only thing on
          screen that answers "is it moving" without being read. */}
      {started && total > 0 && (
        <div className="h-0.5 w-full">
          <RunProgressLine engine={engine} session={session} />
        </div>
      )}

      <div className="flex h-7 items-center gap-2 overflow-hidden px-2 text-[11px]">
        {started ? (
          <>
            {engine.busy ? (
              <span className="live-dot shrink-0" aria-hidden />
            ) : (
              <span
                className="size-1.5 shrink-0 rounded-full"
                style={{
                  background:
                    engine.phase === "done"
                      ? "var(--status-pass)"
                      : engine.phase === "error"
                        ? "var(--status-fail)"
                        : "var(--status-skip)",
                }}
                aria-hidden
              />
            )}
            <Badge variant={PHASE_VARIANT[engine.phase] ?? "default"} className="shrink-0">
              {labels.phase_label[engine.phase] ?? engine.phase}
            </Badge>

            {engine.plan_label && (
              <span className="min-w-0 shrink truncate">
                {engine.scope && (
                  <span className="text-muted-foreground">
                    {labels.scope_running[engine.scope] ?? engine.scope}{" "}
                  </span>
                )}
                <span className="font-medium">{engine.plan_label}</span>
              </span>
            )}

            {engine.unit_total > 1 && (
              <span className="tabular shrink-0 text-muted-foreground">
                {engine.unit_index}/{engine.unit_total}
              </span>
            )}
            {engine.unit_label && (
              <span className="max-w-40 shrink truncate font-mono" title={engine.unit_label}>
                {engine.unit_label}
              </span>
            )}
            {engine.step_total > 0 && (
              <span className="tabular shrink-0 text-muted-foreground">
                passo {engine.step_index}/{engine.step_total}
              </span>
            )}

            {current && (
              <code
                className={cn(
                  "shrink-0 rounded border px-1 py-px font-mono text-[10px]",
                  engine.awaiting
                    ? "border-status-warn/50 bg-status-warn/10 text-status-warn"
                    : "border-live/40 bg-live/10 text-live",
                )}
                title={engine.awaiting ? "aguardando veredito" : "rodando agora"}
              >
                {current}
              </code>
            )}

            {/* The way back to a parked step from anywhere in the window.
                A run waiting on a verdict used to hold the screen: it was the only pane
                on offer, so opening a case to review it was not possible until the
                verdict was written. Now any case opens, and the parked step is reachable
                from the one strip that is always on screen — which is what makes leaving
                it safe rather than a trap. */}
            {engine.awaiting && pendingKey && pendingKey !== selected && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onSelect(pendingKey)}
                className="h-5 shrink-0 gap-1 border-status-warn/50 px-1.5 text-[10px] text-status-warn hover:bg-status-warn/15 hover:text-status-warn"
                title="Voltar ao passo que espera veredito"
              >
                <CornerDownRight className="size-3" />
                Ir ao passo em espera
              </Button>
            )}

            {decided > 0 && (
              <span className="flex shrink-0 items-center gap-2">
                <Tally label="passaram" count={engine.counts.pass ?? 0} color="var(--status-pass)" />
                <Tally
                  label="falharam"
                  count={engine.counts.fail ?? 0}
                  color={(engine.counts.fail ?? 0) > 0 ? "var(--status-fail)" : "var(--status-skip)"}
                />
                {(engine.counts.skip ?? 0) > 0 && (
                  <Tally label="pulados" count={engine.counts.skip ?? 0} color="var(--status-skip)" />
                )}
              </span>
            )}

            <span className="tabular ml-auto shrink-0 text-muted-foreground">
              {(elapsed / 1000).toFixed(1)}s
            </span>

            {engine.busy && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onCancel}
                disabled={cancelDisabled}
                className="h-5 shrink-0 px-1.5 text-[10px] text-status-fail hover:bg-status-fail/15 hover:text-status-fail"
              >
                <Ban className="size-3" />
                Cancelar
              </Button>
            )}
          </>
        ) : (
          <span className="text-muted-foreground">
            Ocioso — escolha o que rodar na coleção
          </span>
        )}

        <div className={cn("flex shrink-0 items-center gap-2", !started && "ml-auto")}>
          {engine.unit_skips.length > 0 && (
            <span className="text-status-warn" title={engine.unit_skips.join("\n")}>
              {engine.unit_skips.length} pulada(s)
            </span>
          )}
          {environment && (
            <Badge variant={environment === "production" ? "fail" : "warn"}>{environment}</Badge>
          )}
        </div>
      </div>

      {engine.events.length > 0 && (
        <div className="flex h-6 items-center gap-2 overflow-hidden border-t border-border/60 px-2">
          {/* The newest line, marked. A run's feed read right to left is four grey
              strings; read with the last one lit it is "this is happening now". */}
          <span className="h-3 w-px shrink-0 bg-live/70" aria-hidden />
          <span className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
            {engine.events.slice(-3).map((event, index, shown) => {
              // Older lines fall away on a narrow window before the newest one does:
              // the feed's job is "what just happened", and three of them is a luxury a
              // 900px window cannot pay for. Counted from the newest, so the rule is
              // about the age of a line and not about where it happens to sit.
              const age = shown.length - 1 - index;
              return (
                <span
                  key={`${event.at}-${index}-${event.text}`}
                  className={cn(
                    "flex min-w-0 shrink items-center gap-1.5 font-mono text-[10px]",
                    age === 1 && "hidden opacity-45 sm:flex",
                    age === 2 && "hidden opacity-45 lg:flex",
                    event.status === "fail" && "text-status-fail",
                    event.status === "pass" && "text-status-pass",
                    event.status === "skip" && "text-status-skip",
                    event.status === "info" && "text-muted-foreground",
                  )}
                >
                  <span className="text-muted-foreground/70">{event.at}</span>
                  <span className="truncate">{event.text}</span>
                </span>
              );
            })}
          </span>
        </div>
      )}
    </footer>
  );
}
