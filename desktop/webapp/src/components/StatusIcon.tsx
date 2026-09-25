import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDot,
  CircleHelp,
  HelpCircle,
  ServerCrash,
  SkipForward,
  Wrench,
} from "lucide-react";

import { statusLabel } from "@/lib/status";
import { cn } from "@/lib/utils";
import type { Labels } from "@/types";

/**
 * A status as an icon, for the column of rows the eye runs down.
 *
 * The tree spelled every status out in a badge on every row, which is a wall of
 * two-word chips: at seventy rounds the labels were the widest thing in the sidebar and
 * the eye had nothing to run down. An icon is the same information in 12px, and the word
 * is not lost — it moves to the tooltip (`TipLayer`, driven off `data-tip`) and to
 * `aria-label`, which is where a reader who needs it looks anyway.
 *
 * The *word* still comes from the server (`labels.status_label`): a status the harness
 * adds tomorrow gets its own slug as the tooltip instead of a blank one. Only the glyph
 * and its colour are decided here, and an unknown status gets a neutral question mark
 * rather than a guess that would read as a verdict.
 *
 * `data-tip` rather than a per-row tooltip component, and `aria-label` rather than
 * `title`, so the browser's own tooltip does not appear as a second one — the row still
 * gets a real tooltip through the shared layer, including from the keyboard.
 */
const GLYPHS: Record<string, typeof CheckCircle2> = {
  pass: CheckCircle2,
  fail: AlertTriangle,
  http_5xx: ServerCrash,
  missing: Ban,
  not_ready: Wrench,
  not_reviewed: CircleDot,
  pending: CircleHelp,
  skip: SkipForward,
  warn: AlertTriangle,
};

/** The colour, kept next to the glyph so the two cannot drift apart. */
const TONES: Record<string, string> = {
  pass: "text-status-pass",
  fail: "text-status-fail",
  http_5xx: "text-status-http",
  missing: "text-status-fail",
  not_ready: "text-status-warn",
  not_reviewed: "text-muted-foreground/70",
  pending: "text-status-skip",
  skip: "text-status-skip",
  warn: "text-status-warn",
};

export function StatusIcon({
  status,
  labels,
  className,
  /** The reason a node cannot run, appended to the tooltip when it has one. */
  detail,
}: {
  status: string;
  labels: Labels;
  className?: string;
  detail?: string | null;
}) {
  const Glyph = GLYPHS[status] ?? HelpCircle;
  const word = statusLabel(status, labels);
  const text = detail ? `${word} — ${detail}` : word;

  return (
    <span
      role="img"
      tabIndex={0}
      aria-label={text}
      data-tip={text}
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded outline-none",
        "focus-visible:ring-1 focus-visible:ring-ring",
        TONES[status] ?? "text-muted-foreground",
        className,
      )}
    >
      <Glyph className="size-3.5" strokeWidth={2.2} />
    </span>
  );
}
