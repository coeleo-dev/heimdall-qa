import { AlertTriangle, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { HarnessError } from "@/types";

/** A refusal, in the terms the reviewer has to act on: what, plus what to do.
 *
 * The `hint` is not decoration. Every `HarnessError` in the harness is raised with one
 * precisely so the screen can say "fix the round, or run something else" instead of
 * showing a code — and a banner that drops the hint turns a five-second fix into a
 * search through the source.
 */
export function ErrorBanner({
  error,
  onDismiss,
}: {
  error: HarnessError;
  onDismiss: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 border-b border-status-fail/35 bg-status-fail/10 px-3 py-2 text-xs"
    >
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-status-fail" />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-status-fail">
          <span className="font-mono text-[10px] opacity-80">{error.code}</span>{" "}
          {error.message}
        </div>
        {error.hint && <div className="mt-0.5 text-muted-foreground">{error.hint}</div>}
        {error.details.length > 0 && (
          <ul className="mt-1 list-disc pl-4 text-[11px] text-muted-foreground">
            {error.details.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
        )}
      </div>
      <Button variant="ghost" size="icon-sm" onClick={onDismiss} aria-label="Dispensar erro">
        <X className="size-3" />
      </Button>
    </div>
  );
}
