import { cn } from "@/lib/utils";

/**
 * The dot that says where a run is, on whatever row it is drawn next to.
 *
 * One component because it appears in three lists — the collection, the "Casos" list of
 * the unit card, and the end-of-run table — and three hand-written copies of a pulsing
 * dot is how two of them end up with a different pulse rate. What it never does is
 * decide *whether* a row is live: that word is shipped with the row, because only the
 * server knows which of six identically-labelled suite steps is the one on the wire.
 *
 * A row with no live state gets an empty spacer rather than nothing, so the labels
 * beside it stay in one column whether or not a run is going.
 */
export function LiveMark({ live, className }: { live: string; className?: string }) {
  if (live === "running") {
    return <span className={cn("live-dot shrink-0", className)} aria-hidden />;
  }
  if (live === "awaiting") {
    return (
      <span
        className={cn(
          "size-1.5 shrink-0 animate-pulse rounded-full bg-status-warn",
          className,
        )}
        aria-hidden
      />
    );
  }
  return <span className={cn("size-1.5 shrink-0", className)} aria-hidden />;
}

/** The word for the state, for a title or a screen reader. */
export function liveLabel(live: string): string {
  if (live === "running") return "rodando agora";
  if (live === "awaiting") return "aguardando veredito";
  return "";
}
