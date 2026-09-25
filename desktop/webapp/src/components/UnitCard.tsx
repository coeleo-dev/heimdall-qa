import { useEffect, useState } from "react";
import { Pencil, Play, RotateCcw } from "lucide-react";

import { LiveMark, liveLabel } from "@/components/LiveMark";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { statusLabel, statusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";
import type { EngineView, Labels, SessionView, UnitCard as UnitCardModel } from "@/types";

/** The run modes, and the sentence that explains each. Kept beside each other so a
 *  new mode cannot be added to the radio group without a reason a reviewer can read. */
const MODES = [
  { id: "review", title: "review", detail: "auto-avança quando os packs passam, e para no que falha" },
  { id: "walk", title: "walk", detail: "para em todo passo, para conferir um a um" },
] as const;

/**
 * What is selected, and every way of running it.
 *
 * The buttons are not enumerated here: they are `unit.scopes`, which is
 * `plan.scopes_for` on the server. That is the whole point of the card — a campaign
 * offers one button, a case offers two, a suite step calls its forward button
 * something else — and a client that decided the list itself would drift from the
 * plan the moment a scope was added.
 */
export function UnitCard({
  unit,
  session,
  engine,
  labels,
  busy,
  onStart,
  onSelect,
  onEdit,
}: {
  unit: UnitCardModel;
  session: SessionView;
  engine: EngineView;
  labels: Labels;
  busy: boolean;
  onStart: (scope: string, mode: string) => void;
  onSelect: (key: string) => void;
  onEdit: (path: string) => void;
}) {
  const [mode, setMode] = useState<string>(session.mode === "walk" ? "walk" : "review");

  // A run started elsewhere (the command palette, a re-run from the step pane) decides
  // the mode; the radio follows rather than lying about what would happen next.
  useEffect(() => {
    if (session.mode) setMode(session.mode === "walk" ? "walk" : "review");
  }, [session.mode]);

  const roundId = unit.round_id ?? engine.unit_round;
  const blocked = Boolean(unit.reason) && !unit.startable;
  // `path` is the content-relative file behind the selection, and the kind is decided by
  // its directory on the server. A `case` has none — a case is a fragment of a file — so
  // the button appearing is the server's answer about what is editable, not ours.
  const editable = editablePath(unit);

  return (
    <div className="pane-column flex flex-col gap-4 py-4">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="text-base font-semibold">{unit.label}</h1>
        <Badge variant={statusVariant(unit.status, labels)}>
          {statusLabel(unit.status, labels)}
        </Badge>
        <span className="text-[11px] text-muted-foreground">
          {labels.kind_label[unit.kind] ?? unit.kind}
        </span>
        {editable && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto gap-1"
            onClick={() => onEdit(editable)}
            title={`Editar ${editable}`}
          >
            <Pencil className="size-3" /> Editar
          </Button>
        )}
      </header>

      <Card className="p-3">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
          <Meta label="Tipo">{labels.kind_label[unit.kind] ?? unit.kind}</Meta>
          {unit.endpoint && (
            <Meta label="Endpoint">
              <code className="font-mono">{unit.endpoint}</code>
            </Meta>
          )}
          <Meta label="Round">
            <code className="font-mono">{session.round_id || roundId || "—"}</code>
          </Meta>
          {session.environment && <Meta label="Ambiente">{session.environment}</Meta>}
          {session.dimensions.length > 0 && (
            <Meta label="Dimensões">{session.dimensions.join(", ")}</Meta>
          )}
          <Meta label="Casos na fila">
            <span className="font-mono tabular-nums">{session.queue.length}</span>
          </Meta>
          <Meta label="Estado">{statusLabel(unit.status, labels)}</Meta>
        </dl>
      </Card>

      {blocked ? (
        <p className="rounded-md border border-status-warn/40 bg-status-warn/10 px-3 py-2 text-xs text-status-warn">
          {unit.reason}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {unit.step_note && (
            <p className="text-[11px] leading-relaxed text-muted-foreground">{unit.step_note}</p>
          )}
          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Modo
            </legend>
            {MODES.map((candidate) => (
              <label
                key={candidate.id}
                className={cn(
                  "flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5 text-xs transition-colors",
                  mode === candidate.id
                    ? "border-primary/50 bg-primary/10"
                    : "border-border hover:bg-accent",
                )}
              >
                <input
                  type="radio"
                  name="run-mode"
                  value={candidate.id}
                  checked={mode === candidate.id}
                  onChange={() => setMode(candidate.id)}
                  className="mt-0.5 accent-[var(--primary)]"
                />
                <span>
                  <strong className="font-medium">{candidate.title}</strong>
                  <span className="text-muted-foreground"> — {candidate.detail}</span>
                </span>
              </label>
            ))}
          </fieldset>

          <div className="flex flex-wrap gap-1.5">
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
          </div>
          {busy && (
            <p className="text-[11px] text-muted-foreground">
              Um plano já está rodando. Cancele-o ou espere terminar.
            </p>
          )}
        </div>
      )}

      {session.queue.length > 0 && (
        <section className="flex flex-col gap-1.5">
          <h2 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Casos
          </h2>
          <ul className="flex flex-col gap-px">
            {session.queue.map((item, index) => (
              // Keyed by the row's own node and not by its id: a suite lists the same
              // step six times, and six children with one key is a list React is free
              // to reconcile into rows that stop moving.
              <li key={`${item.key || "row"}-${index}`}>
                <button
                  type="button"
                  disabled={!item.key}
                  onClick={() => item.key && onSelect(item.key)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                    item.current ? "bg-accent" : "hover:bg-accent/60",
                    !item.key && "cursor-default opacity-70",
                  )}
                >
                  <span title={liveLabel(item.live)}>
                    <LiveMark live={item.live} />
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono">{item.case_id}</span>
                  <Badge variant={statusVariant(item.status, labels)}>
                    {statusLabel(item.status, labels)}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {unit.previous && <PreviousRun previous={unit.previous} />}

      <p className="text-[11px] text-muted-foreground">Um plano de cada vez. O agente não opera esta UI.</p>
    </div>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  );
}

/**
 * The file behind a selection, when the server says there is one to edit.
 *
 * Retyped here rather than inferred from `kind`: the server's `path` is the content
 * path, and a client that decided "a round is editable" on its own would offer the
 * button on a node whose path is a case — which is a fragment of a file, not a file.
 */
function editablePath(unit: UnitCardModel): string | null {
  if (!unit.path) return null;
  if (unit.kind === "campaign" || unit.kind === "round") return unit.path;
  return null;
}

/** The selected round's last whole run, so the idle screen answers "where was I". */
function PreviousRun({ previous }: { previous: Record<string, unknown> }) {
  const counts = (previous.counts ?? {}) as Record<string, number>;
  const rows: [string, string][] = [
    ["Passou", String(counts.pass ?? 0)],
    ["Falhou", String(counts.fail ?? 0)],
    ["Pulado", String(counts.skip ?? 0)],
  ];
  if (previous.selection) rows.push(["Recorte", String(previous.selection)]);
  if (previous.coverage_pct !== undefined) rows.push(["Cobertura", `${previous.coverage_pct}%`]);
  if (previous.review_duration_ms !== undefined) {
    rows.push(["Duração", `${previous.review_duration_ms} ms`]);
  }

  return (
    <section className="flex flex-col gap-1.5">
      <h2 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        <RotateCcw className="size-3" />
        Último run
      </h2>
      <Card className="p-2.5">
        <p className="mb-2 truncate font-mono text-[10px] text-muted-foreground" title={String(previous.run_dir ?? "")}>
          {String(previous.run_dir ?? "")}
        </p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          {rows.map(([label, value]) => (
            <Meta key={label} label={label}>
              <span className={cn("font-mono tabular-nums", label === "Falhou" && value !== "0" && "text-status-fail")}>
                {value}
              </span>
            </Meta>
          ))}
        </dl>
      </Card>
    </section>
  );
}
