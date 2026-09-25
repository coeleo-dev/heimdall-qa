import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ArrowRight, Boxes, CheckCircle2, Layers, Play, SkipForward, XCircle } from "lucide-react";

import { RunProgress } from "@/components/RunProgress";
import { LiveMark } from "@/components/LiveMark";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { isOpen, statusColor, statusLabel, statusVariant } from "@/lib/status";
import { flatten } from "@/lib/tree";
import { cn } from "@/lib/utils";
import type { EngineView, Labels, SessionView, TreeNode, UnitCard } from "@/types";

/** A roll-up of a campaign or flow: how many endpoints sit in each state.
 *
 * Only rounds are counted. A campaign's folders are a grouping, and counting them
 * would make "3 falhando" mean something different depending on how deep the tree is
 * — the number a reviewer wants is "how many endpoints are wrong". */
export function CampaignPane({
  unit,
  engine,
  session,
  tree,
  labels,
  busy,
  nextUnreviewed,
  onStart,
  onSelect,
}: {
  unit: UnitCard;
  engine: EngineView;
  session: SessionView;
  tree: TreeNode[];
  labels: Labels;
  busy: boolean;
  nextUnreviewed: string | null;
  onStart: (scope: string, mode: string) => void;
  onSelect: (key: string) => void;
}) {
  const [mode, setMode] = useState<string>(session.mode === "walk" ? "walk" : "review");

  useEffect(() => {
    if (session.mode) setMode(session.mode === "walk" ? "walk" : "review");
  }, [session.mode]);

  const node = flatten(tree).find((candidate) => candidate.key === unit.key);
  const rounds = flatten(node?.children ?? []).filter((child) => child.kind === "round");

  const tally = new Map<string, number>();
  for (const round of rounds) {
    tally.set(round.status, (tally.get(round.status) ?? 0) + 1);
  }
  // Worst first: the reason someone opened this card is to see what is wrong.
  const order = ["fail", "http_5xx", "missing", "not_ready", "not_reviewed", "skip", "warn", "pending", "pass"];
  const ordered = [...tally.entries()].sort(
    (a, b) => order.indexOf(a[0]) - order.indexOf(b[0]),
  );
  const failing = node?.fail_count ?? 0;
  const passing = tally.get("pass") ?? 0;
  const open = rounds.filter((round) => isOpen(round.status)).length;

  /**
   * Whether the plan in flight is this item's to report.
   *
   * Read off the tree's live mark and not off `plan_label`, because the two disagree
   * exactly where it matters: a run started on a *round* — "Rodar este endpoint" — has
   * that round's label as its plan label, while the campaign holding it is the node the
   * reviewer is looking at. Asked by label the roll-up offered "Como rodar" and a
   * second Start button while a plan was already running inside it.
   *
   * The mark bubbles from the row on the wire to every ancestor, and the server puts it
   * there, so this is the same answer the tree draws one panel to the left.
   */
  const liveRounds = rounds.filter((round) => round.live !== "");
  // `finished` and not only the mark: `plan_label` outlives the plan — the engine keeps
  // the last one so the strip can name it — so a campaign that just ran matched its own
  // label and drew the live card, with the Start buttons behind "a plan is already
  // running here", until the server restarted. The fallback is for the frames between
  // `start` and the first mark on the wire, and a finished plan is not that.
  const runningHere =
    liveRounds.length > 0 || (!engine.finished && engine.plan_label === unit.label);
  /** The tree row the parked row stands for, so the verdict form is one click away.
   *  Taken from the row and not matched by case id: a suite names the same step six
   *  times, and the row's own key is the only thing that says which one it means. */
  const liveRowKey = session.queue.find((item) => item.live !== "")?.key;
  const nextNode = nextUnreviewed
    ? flatten(tree).find((candidate) => candidate.key === nextUnreviewed)
    : null;

  return (
    <div className="pane-column flex flex-col gap-4 py-5">
      <header className="flex flex-wrap items-center gap-2">
        <span className="flex size-7 items-center justify-center rounded-md border border-border bg-card">
          {unit.scopes.length > 0 ? (
            <Boxes className="size-3.5 text-muted-foreground" />
          ) : (
            <Layers className="size-3.5 text-muted-foreground" />
          )}
        </span>
        <h1 className="text-base font-semibold tracking-tight">{unit.label}</h1>
        <Badge variant={statusVariant(unit.status, labels)}>{statusLabel(unit.status, labels)}</Badge>
        <span className="text-[11px] text-muted-foreground">
          {labels.kind_label[unit.kind] ?? unit.kind}
        </span>
      </header>

      {/* The three numbers that decide "do I run this now", as tiles rather than as
          rows of a definition list: a dl makes the reader parse, and this is the one
          screen where the answer has to be immediate. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat icon={<Layers className="size-3" />} label="Endpoints" value={rounds.length} />
        <Stat
          icon={<CheckCircle2 className="size-3" />}
          label="Passaram"
          value={passing}
          tone="pass"
        />
        <Stat
          icon={<XCircle className="size-3" />}
          label="Falhando"
          value={failing}
          tone={failing > 0 ? "fail" : "muted"}
        />
        <Stat
          icon={<SkipForward className="size-3" />}
          label="Não reviewados"
          value={open}
          tone={open > 0 ? "warn" : "muted"}
        />
      </div>

      {ordered.length > 0 && (
        <div className="flex flex-col gap-2">
          {/* The distribution as one bar before it is a list of badges: at a glance
              "mostly green, one red" is a length, and reading four numbers is not. */}
          <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted">
            {ordered.map(([status, count]) => (
              <span
                key={status}
                style={{
                  width: `${(count / Math.max(rounds.length, 1)) * 100}%`,
                  background: statusColor(status),
                }}
                title={`${statusLabel(status, labels)}: ${count}`}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {ordered.map(([status, count]) => (
              <Badge key={status} variant={statusVariant(status, labels)}>
                {statusLabel(status, labels)} {count}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {runningHere ? (
        <Card className="flex flex-col gap-3 border-live/30 p-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <LiveMark live={engine.awaiting ? "awaiting" : "running"} />
            <Badge variant={engine.awaiting ? "warn" : "http"}>
              {labels.phase_label[engine.phase] ?? engine.phase}
            </Badge>
            {engine.unit_total > 0 && (
              <span className="tabular text-muted-foreground">
                unidade {engine.unit_index}/{engine.unit_total}
              </span>
            )}
            {engine.unit_label && <strong className="font-medium">{engine.unit_label}</strong>}
            {engine.case_id && (
              <code
                className={cn(
                  "rounded border px-1 font-mono text-[10px]",
                  engine.awaiting
                    ? "border-status-warn/50 bg-status-warn/10 text-status-warn"
                    : "border-live/40 bg-live/10 text-live",
                )}
              >
                {engine.case_id}
              </code>
            )}
          </div>
          <RunProgress engine={engine} session={session} />
          {/* The roll-up is where a campaign is watched, so the one action a parked
              run needs has to be reachable from here. Without this the reviewer sees
              "aguardando veredito" and has to find the pulsing row in the tree before
              the form appears. */}
          {engine.awaiting && liveRowKey ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => onSelect(liveRowKey)} className="h-6 text-[11px]">
                Responder este veredito
              </Button>
              <span className="text-[11px] text-muted-foreground">
                {liveRounds.length > 1
                  ? `${liveRounds.length} endpoints em andamento aqui.`
                  : "O plano para neste caso até você decidir."}
              </span>
            </div>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              {liveRounds.length > 1
                ? `${liveRounds.length} endpoints em andamento neste item. `
                : ""}
              O plano em andamento é deste item. Cancele-o para começar outro.
            </p>
          )}
        </Card>
      ) : (
        <Card className="flex flex-col gap-3 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Como rodar
            </span>
            {/* A segmented control rather than two radio labels: it is one choice with
                two answers, and it should not look like a form. */}
            <div className="flex rounded-md border border-border p-0.5">
              {(
                [
                  ["review", "Auto", "avança sozinho nos packs que passam"],
                  ["walk", "Passo a passo", "para em todo caso para você decidir"],
                ] as const
              ).map(([id, name, detail]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setMode(id)}
                  title={detail}
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

          <div className="flex flex-wrap gap-1.5">
            {unit.scopes.map((scope, index) => (
              <Button
                key={scope}
                variant={index === 0 ? "default" : "outline"}
                onClick={() => onStart(scope, mode)}
                disabled={busy}
                title={index === 0 ? "O escopo mais estreito que este item aceita" : undefined}
              >
                <Play className="size-3" />
                {unit.scope_labels[scope] ?? labels.scope_label[scope] ?? scope}
              </Button>
            ))}
          </div>
          <p className="text-[11px] text-muted-foreground">
            {mode === "review"
              ? "Auto: cada caso é julgado pelos packs e o run só para no que falha."
              : "Passo a passo: cada caso espera o seu veredito antes do próximo."}
          </p>
        </Card>
      )}

      {nextNode && (
        <Button variant="outline" size="sm" className="self-start" onClick={() => onSelect(nextNode.key)}>
          Próximo não reviewado: {nextNode.label}
          <ArrowRight className="size-3" />
        </Button>
      )}

      <p className="text-[11px] text-muted-foreground">
        Um plano de cada vez. O agente não opera esta UI.
      </p>
    </div>
  );
}

/** One number, with the colour of what it counts. */
function Stat({
  icon,
  label,
  value,
  tone = "muted",
}: {
  icon: ReactNode;
  label: string;
  value: number;
  tone?: "pass" | "fail" | "warn" | "muted";
}) {
  const toneClass = {
    pass: "text-status-pass",
    fail: "text-status-fail",
    warn: "text-status-warn",
    muted: "text-muted-foreground",
  }[tone];

  return (
    <Card className="flex flex-col gap-1 p-2.5">
      <span
        className={cn("flex items-center gap-1.5 text-[10px] uppercase tracking-wider", toneClass)}
      >
        {icon}
        {label}
      </span>
      <span className={cn("tabular text-lg font-semibold leading-none", toneClass)}>{value}</span>
    </Card>
  );
}
