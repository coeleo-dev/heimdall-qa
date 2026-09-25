import { Check, Copy, Play } from "lucide-react";
import { useState } from "react";

import { JsonEditor } from "@/components/JsonEditor";
import { KpiStrip } from "@/components/KpiStrip";
import type { Kpi } from "@/components/KpiStrip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
 *
 * Everything read here comes from `run.summary`, not from `engine.counts`. That is not
 * a preference: a campaign's last round used to draw the *campaign's* totals under the
 * round's own name, and a round opened from history has no engine counters at all. The
 * engine's numbers are the fallback for the one case they are right for — a run that
 * was cancelled before it could write a summary.
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
  onStart: (scope: string, mode: string, node?: string) => void;
}) {
  const summary = run.summary as Record<string, unknown>;
  const counts = (summary.counts ?? {}) as Record<string, number>;
  const packs = (summary.packs ?? {}) as Record<string, number>;
  const latency = (summary.latency_ms ?? {}) as Record<string, number>;
  const hasSummary = summary.counts !== undefined;
  const counted = (name: string): number =>
    hasSummary ? (counts[name] ?? 0) : (engine.counts[name] ?? 0);
  // The way the run was run, so "run again" repeats it rather than guessing. A round
  // opened from disk has no session mode to inherit — its session is the stored one
  // with an empty mode — and this is the only place that fact survived the run.
  const [mode, setMode] = useState<string>(
    String(summary.mode ?? "") === "walk" ? "walk" : "review",
  );

  const kpis: Kpi[] = [
    { label: "Passou", value: String(counted("pass")), tone: counted("pass") ? "pass" : "muted" },
    { label: "Falhou", value: String(counted("fail")), tone: counted("fail") ? "fail" : "muted" },
    { label: "Pulado", value: String(counted("skip")), tone: counted("skip") ? "warn" : "muted" },
  ];
  if (hasSummary) {
    kpis.push(
      {
        label: "HTTP 5xx",
        value: String(counts.http_5xx ?? 0),
        tone: counts.http_5xx ? "fail" : "muted",
      },
      {
        label: "Instrumento",
        value: String(counts.instrument ?? 0),
        tone: counts.instrument ? "warn" : "muted",
      },
      { label: "Packs fail", value: String(packs.fail ?? 0), tone: packs.fail ? "fail" : "muted" },
      { label: "Packs warn", value: String(packs.warn ?? 0), tone: packs.warn ? "warn" : "muted" },
      { label: "Cobertura", value: `${summary.coverage_pct ?? "—"}%` },
      { label: "p50", value: `${latency.p50 ?? "—"} ms` },
      { label: "p95", value: `${latency.p95 ?? "—"} ms` },
      {
        label: "Logs incompletos",
        value: String(summary.logs_incomplete ?? 0),
        tone: summary.logs_incomplete ? "warn" : "muted",
      },
      { label: "Rejeição humana", value: String(summary.human_reject_rate ?? "—") },
      { label: "Duração", value: `${Math.round(Number(summary.review_duration_ms ?? 0))} ms` },
    );
  }

  return (
    <div className="pane-column pane-column-wide flex flex-col gap-5 py-4">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="text-base font-semibold">
          Fim do run —{" "}
          <code className="font-mono text-sm">{run.unit_label || session.round_id}</code>
        </h1>
        {run.unit_kind && (
          <span className="text-[10px] text-muted-foreground">
            {labels.kind_label[run.unit_kind] ?? run.unit_kind}
          </span>
        )}
        <Badge variant="pass">Concluído</Badge>
      </header>

      <KpiStrip items={kpis} />

      {run.failed_cases.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <h2 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Casos que falharam ({run.failed_cases.length})
          </h2>
          <ul className="flex flex-col gap-px">
            {run.failed_cases.map((item, index) => {
              // The node to reopen comes with the row, resolved on the server against
              // the round's own cases — a case id cannot name a row of a suite, which
              // lists the same step six times.
              const key = item.key ?? "";
              return (
                <li key={key || `${item.case_id}-${index}`}>
                  <button
                    type="button"
                    disabled={!key}
                    onClick={() => key && onSelect(key)}
                    title={item.step_dir || undefined}
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                      key && "hover:bg-accent",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono">{item.case_id}</span>
                    {item.reason && (
                      <span className="hidden max-w-48 truncate text-[10px] text-muted-foreground md:inline">
                        {item.reason}
                      </span>
                    )}
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

      {/* The buttons come from the unit's own scope table and not from a hardcoded
          "round": a round opened from history is still a round the engine can be asked
          to run again, and the button has to say what the server would do. The mode is
          the run's own, so "run it again" repeats what was read rather than silently
          switching a walk into an auto run. */}
      {unit.startable && unit.scopes.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {unit.scopes.map((scope, index) => (
            <Button
              key={scope}
              variant={index === 0 ? "default" : "outline"}
              onClick={() => onStart(scope, mode)}
              disabled={busy}
            >
              <Play className="size-3" />
              {unit.scope_labels[scope] ?? labels.scope_label[scope] ?? scope}
            </Button>
          ))}
          <div className="flex rounded-md border border-border p-0.5">
            {(
              [
                ["review", "Auto"],
                ["walk", "Passo a passo"],
              ] as const
            ).map(([id, name]) => (
              <button
                key={id}
                type="button"
                onClick={() => setMode(id)}
                className={cn(
                  "rounded px-2 py-0.5 text-[11px] transition-colors",
                  mode === id
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                {name}
              </button>
            ))}
          </div>
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
