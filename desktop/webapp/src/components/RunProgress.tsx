import { statusColor } from "@/lib/status";
import { cn } from "@/lib/utils";
import type { EngineView, SessionView } from "@/types";

/**
 * How far the run has got, drawn rather than counted.
 *
 * Two readings in one object. The **segments** are the cases this round brought, each
 * one coloured by its verdict the moment the verdict lands — so the bar fills green
 * and red left to right in the order things actually happened, and a failure is a red
 * segment at position three rather than a number that went up by one. The **fill** is
 * `cases_done / case_total`, which is the wait the reviewer is actually sitting
 * through.
 *
 * A run with no session yet — the plan is starting, the first request is on the wire —
 * has no cases to segment, so it sweeps. A sweep is honest about not knowing; a bar
 * parked at 0% for the first four seconds of a campaign is not.
 */
function Track({
  engine,
  session,
  className,
  radius = "rounded-full",
}: {
  engine: EngineView;
  session: SessionView;
  className?: string;
  radius?: string;
}) {
  const total = engine.case_total || engine.step_total;
  const segments = session.queue.length > 0 ? session.queue : null;
  const indeterminate = engine.busy && total === 0;

  return (
    <div
      className={cn(
        "progress-track min-w-16 flex-1",
        radius === "rounded-none" ? "rounded-none" : "rounded-full",
        indeterminate && "progress-sweep",
        className,
      )}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={total || undefined}
      aria-valuenow={total ? engine.cases_done : undefined}
      aria-label="Progresso da execução"
    >
      {indeterminate ? null : segments ? (
        // One segment per case, with a hairline between them. The gap is what makes a
        // decided case and the next one readable as two facts rather than a width.
        //
        // The row the run is on is marked, because the segments are the only place the
        // *order* is visible: a bar filling left to right says how far, not where. The
        // mark is taken off the row — the server ships which of a suite's identical
        // steps is on the wire, and a client matching by case id would mark them all.
        <div className="flex h-full w-full gap-px">
          {segments.map((item, index) => (
            <span
              key={`${item.case_id}-${index}`}
              className={cn(
                "relative h-full min-w-0 flex-1 overflow-hidden rounded-[1px] transition-colors duration-300",
                item.live === "running" && "segment-live",
                item.live === "awaiting" && "ring-1 ring-status-warn/70 ring-inset",
              )}
              style={{ background: statusColor(item.status) }}
              title={
                item.live === "running"
                  ? `${item.case_id} — rodando agora`
                  : item.live === "awaiting"
                    ? `${item.case_id} — aguardando veredito`
                    : item.case_id
              }
            />
          ))}
        </div>
      ) : (
        <div
          className="progress-fill"
          style={{
            width: `${total ? Math.round((engine.cases_done / total) * 100) : 0}%`,
            background: engine.counts.fail
              ? "var(--status-fail)"
              : engine.finished
                ? "var(--status-pass)"
                : "var(--live)",
          }}
        />
      )}
    </div>
  );
}

/** The bar on its own, for the footer's full-width line. The caller sizes it. */
export function RunProgressLine({ engine, session }: { engine: EngineView; session: SessionView }) {
  return <Track engine={engine} session={session} radius="rounded-none" className="h-full min-w-0" />;
}

/** The bar with its count, for a card that has room for the number. */
export function RunProgress({
  engine,
  session,
  className,
}: {
  engine: EngineView;
  session: SessionView;
  className?: string;
}) {
  const total = engine.case_total || engine.step_total;

  return (
    <div className={cn("flex min-w-0 items-center gap-2", className)}>
      <Track engine={engine} session={session} className="h-1.5" />
      {total > 0 && (
        <span className="tabular shrink-0 text-[10px] text-muted-foreground">
          {engine.cases_done}/{total}
        </span>
      )}
    </div>
  );
}
