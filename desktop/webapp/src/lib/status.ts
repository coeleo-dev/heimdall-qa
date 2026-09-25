import type { BadgeProps } from "@/components/ui/badge";
import type { Labels } from "@/types";

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

/** The harness's `status_pill` classes, mapped onto the client's badge variants.
 *
 * The words themselves come from the server (`labels.status_label`) and are never
 * retyped here: a status the harness adds would then show its raw slug on the screen,
 * which is exactly the kind of drift the labels module exists to prevent. Only the
 * *colour* is a client concern, and `http_5xx` keeps its own colour because "the
 * server answered with a 5xx" and "the pack failed" are different findings.
 */
const PILL_TO_VARIANT: Record<string, BadgeVariant> = {
  "status-success": "pass",
  "status-danger": "fail",
  "status-warning": "warn",
  "status-muted": "muted",
};

export function statusVariant(status: string, labels: Labels): BadgeVariant {
  if (status === "http_5xx") return "http";
  const pill = labels.status_pill[status];
  return pill ? (PILL_TO_VARIANT[pill] ?? "default") : "default";
}

export function statusLabel(status: string, labels: Labels): string {
  return labels.status_label[status] ?? status;
}

/** The pack's outcome as a badge: `pass`/`warn`/`fail`/`skipped`/`waived`. */
export function packVariant(status: string | undefined): BadgeVariant {
  switch (status) {
    case "pass":
      return "pass";
    case "fail":
      return "fail";
    case "warn":
      return "warn";
    case "waived":
      return "outline";
    default:
      return "muted";
  }
}

/** Whether a status is one a reviewer must not scroll past. */
export function isFailing(status: string): boolean {
  return status === "fail" || status === "http_5xx";
}

/** A status as a raw colour, for the bars and dots that cannot wear a badge.
 *
 * The badge path above is the one the words go through; this is for the surfaces that
 * have no room for words — a 4px segment, a dot at the head of a tree row. Both read
 * the same slugs, so a status the harness adds shows the neutral colour here instead
 * of inventing one.
 */
export function statusColor(status: string): string {
  switch (status) {
    case "pass":
      return "var(--status-pass)";
    case "fail":
    case "http_5xx":
    case "missing":
      return "var(--status-fail)";
    case "warn":
    case "not_ready":
      return "var(--status-warn)";
    case "skip":
      return "var(--status-skip)";
    case "not_reviewed":
    case "pending":
      return "color-mix(in oklab, var(--foreground) 16%, transparent)";
    default:
      return "var(--status-http)";
  }
}

/** Whether a status is still waiting on something, rather than decided. */
export function isOpen(status: string): boolean {
  return status === "pending" || status === "not_reviewed" || status === "";
}
