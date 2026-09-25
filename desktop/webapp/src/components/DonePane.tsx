import { Check, Copy, Play } from "lucide-react";
import { useState } from "react";

import { JsonEditor } from "@/components/JsonEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { statusLabel, statusVariant } from "@/lib/status";
import { cn, prettyJson } from "@/lib/utils";
import type { EngineView, Labels, RunAggregate, SessionView, UnitCard } from "@/types";

/**
 * The end of a run: the numbers as a strip, and the cases worth reopening.
 *
 * The list is the part that matters. A run of 470 cases ending on a wall of identical
 * stat cards answers "how did it go" and leaves "what do I do now" to the reader; the
 * failures are each a link back to their step. When there are none, that is said in
 * words rather than shown as an empty list — the absence is the good news.
 */
export function DonePane({
  run,
  engine,
  session,
  unit,
  labels,
  busy,
  onSelect,
  onStart,
}: {
  run: RunAggregate;
  engine: EngineView;
  session: SessionView;
  unit: UnitCard;
  labels: Labels;
  busy: boolean;
  onSelect: (key: string) => void;
  onStart: (scope: string, mode: string) => void;
}) {
  const summary = run.summary as Record<string, unknown>;
  const counts = (summary.counts ?? {}) as Record<string, number>;
  const packs = (summary.packs ?? {}) as Record<string, number>;
  const latency = (summary.latency_ms ?? {}) as Record<string, number>;

  const kpis: { label: string; value: string; bad?: boolean }[] = [
    { label: "Passou", value: String(engine.counts.pass ?? 0) },
    { label: "Falhou", value: String(engine.counts.fail ?? 0), bad: Boolean(engine.counts.fail) },
    { label: "Pulado", value: String(engine.counts.skip ?? 0) },
  ];
  if (summary.counts !== undefined) {
    kpis.push(
      { label: "HTTP 5xx", value: String(counts.http_5xx ?? 0), bad: Boolean(counts.http_5xx) },
      { label: "Packs fail", value: String(packs.fail ?? 0), bad: Boolean(packs.fail) },
      { label: "Cobertura", value: `${summary.coverage_pct ?? "—"}%` },
      { label: "p50", value: `${latency.p50 ?? "—"} ms` },
      { label: "p95", value: `${latency.p95 ?? "—"} ms` },
      {
        label: "Logs incompletos",
        value: String(summary.logs_incomplete ?? 0),
        bad: Boolean(summary.logs_incomplete),
      },
      { label: "Rejeição humana", value: String(summary.human_reject_rate ?? "—") },
      { label: "Duração", value: `${summary.review_duration_ms ?? "—"} ms` },
    );
  }

  return (
    <div className="pane-column pane-column-wide flex flex-col gap-5 py-4">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="text-base font-semibold">
          Fim do run — <code className="font-mono text-sm">{session.round_id}</code>
        </h1>
        <Badge variant="pass">Concluído</Badge>
      </header>

      <Card className="flex flex-wrap gap-x-6 gap-y-3 p-3">
        {kpis.map((kpi) => (
          <div key={kpi.label} className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {kpi.label}
            </span>
            <span
              className={cn(
                "font-mono text-sm tabular-nums",
                kpi.bad && "text-status-fail",
                !kpi.bad && kpi.label === "Passou" && "text-status-pass",
              )}
            >
              {kpi.value}
            </span>
          </div>
        ))}
      </Card>

      {run.failed_cases.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <h2 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Casos que falharam ({run.failed_cases.length})
          </h2>
          <ul className="flex flex-col gap-px">
            {run.failed_cases.map((item, index) => {
              // The node to reopen comes with the row, from the same walk that painted
              // the queue — a case id cannot name a row of a suite, which lists the
              // same step six times.
              const key = item.key ?? "";
              return (
                <li key={key || `${item.case_id}-${index}`}>
                  <button
                    type="button"
                    disabled={!key}
                    onClick={() => key && onSelect(key)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                      key && "hover:bg-accent",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono">{item.case_id}</span>
                    <Badge variant={statusVariant(item.status, labels)}>
                      {statusLabel(item.status, labels)}
                    </Badge>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ) : (
        <p className="text-xs text-muted-foreground">Nenhum caso falhou neste run.</p>
      )}

      {run.run_path && <RunPath path={run.run_path} />}

      {Object.keys(summary).length > 0 && (
        <details className="rounded-md border border-border/70">
          <summary className="cursor-pointer px-2 py-1.5 text-xs text-muted-foreground outline-none hover:text-foreground">
            summary.json
          </summary>
          <div className="border-t border-border/60 p-2">
            <JsonEditor value={prettyJson(summary)} minLines={10} />
          </div>
        </details>
      )}

      {unit.startable && (
        <div>
          <Button onClick={() => onStart("round", session.mode || "walk")} disabled={busy}>
            <Play className="size-3" />
            Reexecutar
          </Button>
        </div>
      )}
    </div>
  );
}

function RunPath({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <p className="flex items-center gap-2 text-[11px]">
      <span className="text-muted-foreground">Run</span>
      <code className="min-w-0 flex-1 truncate font-mono" title={path}>
        {path}
      </code>
      <Button
        variant="ghost"
        size="sm"
        className="h-5 text-[10px]"
        onClick={() => {
          void navigator.clipboard?.writeText(path).catch(() => undefined);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1_200);
        }}
      >
        {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
        {copied ? "Copiado" : "Copiar"}
      </Button>
    </p>
  );
}
