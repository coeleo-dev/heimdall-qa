import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * A run's numbers as one wrapping strip of labelled figures.
 *
 * Shared by the end-of-run pane and a campaign's roll-up on purpose: they are the same
 * question asked of different scopes — "what did these runs decide" — and two copies of
 * the markup would drift into two different-looking answers to it. The value is a
 * string because half of these are not counts: a coverage is a percentage and a latency
 * is milliseconds, and formatting belongs where the digits are drawn.
 *
 * The tone is what the eye is meant to catch. `muted` is deliberately not "green": a
 * zero that would be bad if it were not zero should not shout, and a zero that is good
 * news does not need a colour to say so.
 */
export interface Kpi {
  label: string;
  value: string;
  tone?: "pass" | "fail" | "warn" | "muted";
}

const TONE: Record<NonNullable<Kpi["tone"]>, string> = {
  pass: "text-status-pass",
  fail: "text-status-fail",
  warn: "text-status-warn",
  muted: "",
};

export function KpiStrip({ items, className }: { items: Kpi[]; className?: string }) {
  return (
    <Card data-testid="kpi-strip" className={cn("flex flex-wrap gap-x-6 gap-y-3 p-3", className)}>
      {items.map((item) => (
        <div key={item.label} className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {item.label}
          </span>
          <span
            className={cn("font-mono text-sm tabular-nums", TONE[item.tone ?? "muted"])}
          >
            {item.value}
          </span>
        </div>
      ))}
    </Card>
  );
}
