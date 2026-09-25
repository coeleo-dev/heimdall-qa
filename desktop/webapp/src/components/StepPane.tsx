import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Copy, Check } from "lucide-react";

import { JsonEditor } from "@/components/JsonEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { packVariant, statusLabel } from "@/lib/status";
import { cn, prettyJson, text } from "@/lib/utils";
import type {
  Labels,
  Pane,
  SessionView,
  StepView,
  UnitCard as UnitCardModel,
  VerdictStatus,
} from "@/types";

/**
 * One step's evidence, ordered by what a reviewer asks.
 *
 * The order is inherited and deliberate: what happened (header), did it break the
 * contract (packs), what the numbers were (probe), and only then the raw bodies. The
 * bodies open themselves on a failed step and stay shut on a passing one, because on
 * a run of hundreds of steps the only answer worth opening unasked is the one that
 * went wrong.
 */
export function StepPane({
  step,
  session,
  unit,
  labels,
  pane,
  busy,
  onVerdict,
  onFocus,
  onStart,
}: {
  step: StepView;
  session: SessionView;
  unit: UnitCardModel;
  labels: Labels;
  pane: Pane;
  busy: boolean;
  onVerdict: (status: VerdictStatus, comment: string, continueRound: boolean) => Promise<boolean>;
  onFocus: (index: number) => void;
  onStart: (scope: string, mode: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [sending, setSending] = useState(false);
  const commentRef = useRef<HTMLTextAreaElement | null>(null);
  const formRef = useRef<HTMLDivElement | null>(null);

  const awaiting = step.awaiting_verdict;
  const [prev, total] = session.progress;

  // A new step under the cursor is a new question: carrying the previous answer's
  // comment into it would post one case's reasoning against another.
  useEffect(() => {
    setComment("");
    setInvalid(false);
  }, [session.current_step_dir, session.pending_index]);

  const send = async (status: VerdictStatus) => {
    if (sending) return;
    if (status !== "pass" && comment.trim() === "") {
      setInvalid(true);
      commentRef.current?.focus();
      return;
    }
    setSending(true);
    const ok = await onVerdict(status, comment, true);
    setSending(false);
    if (!ok) setInvalid(true);
  };

  // The shortcuts click the same handlers the buttons do, so there is one definition
  // of what "reprovar e parar" means. Focus goes to the form rather than the textarea
  // — a focused textarea would turn every `A` into the letter A.
  useEffect(() => {
    if (!awaiting) return;
    const onKey = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName?.toLowerCase() ?? "";
      if (tag === "textarea" && event.key !== "Escape") return;
      if (tag === "input") return;
      const key = event.key.toLowerCase();
      if (key === "/") {
        event.preventDefault();
        commentRef.current?.focus();
        return;
      }
      if (key === "j" || key === "k") {
        if (!session.can_next && !session.can_prev) return;
        event.preventDefault();
        if (key === "j" && session.can_next) onFocus(session.focus_index + 1);
        if (key === "k" && session.can_prev) onFocus(session.focus_index - 1);
        return;
      }
      if (key === "a") {
        event.preventDefault();
        void send("pass");
      } else if (key === "r") {
        event.preventDefault();
        void send(event.shiftKey ? "fail_stop" : "fail");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // `send` closes over `comment`, which is the point: the shortcut posts what is in
    // the box right now, exactly as the button would.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaiting, comment, sending, session.can_next, session.can_prev, session.focus_index]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <StepHeader
        step={step}
        session={session}
        pane={pane}
        prev={prev}
        total={total}
        onFocus={onFocus}
      />

      <div className="@container min-h-0 flex-1 overflow-y-auto scroll-thin">
        <div className="pane-column pane-column-wide flex flex-col gap-5 py-4">
          <Packs step={step} />

          {step.probe_rows.length > 0 && <ProbeTable rows={step.probe_rows} />}

          {step.is_probe && !step.has_http && (
            <p className="text-[11px] text-muted-foreground">
              Este passo é conferência, não um POST.
            </p>
          )}

          {step.has_http && (
            <section className="flex flex-col gap-2">
              <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                HTTP
              </h3>
              {/* Request beside response once the *pane* is wide enough for both. This
                  is the one comparison the screen exists for, and stacking them is what
                  made the reviewer scroll between two halves of a single exchange — the
                  scroll is where the diff gets missed, not where it gets found.
                  Measured against the pane and not the window, because the sidebar is
                  draggable: on a 1600px window with a wide collection open, a viewport
                  breakpoint would still call this roomy and split it into two 250px
                  columns of JSON. Below that it stays stacked, because two cramped
                  panes are worse than one. */}
              <div className="grid grid-cols-1 items-start gap-3 @4xl:grid-cols-2">
                <div className="flex min-w-0 flex-col gap-2">
                  <Collapsible title="Body da request" defaultOpen={step.body_open}>
                    <BodyWithCopy value={step.request_body} />
                    <p className="mt-1.5 text-[10px] text-muted-foreground">
                      password, token e api_key gravados aparecem como [REDACTED]. O HTTP enviou
                      o valor real.
                    </p>
                  </Collapsible>
                  <Collapsible title="Headers da request" defaultOpen={false}>
                    <JsonEditor value={prettyJson(step.request_headers)} minLines={4} />
                  </Collapsible>
                </div>
                <div className="flex min-w-0 flex-col gap-2">
                  <Collapsible title="Body da response" defaultOpen={step.body_open}>
                    <BodyWithCopy value={step.response_body} />
                  </Collapsible>
                </div>
              </div>
            </section>
          )}

          <Logs step={step} />
        </div>
      </div>

      {awaiting ? (
        <div
          ref={formRef}
          tabIndex={-1}
          className="shrink-0 border-t border-border bg-card/95 backdrop-blur outline-none"
        >
          <div className="pane-column pane-column-wide flex flex-col gap-2 py-3">
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted-foreground">
                Comentário <span className="opacity-70">(obrigatório ao reprovar)</span>
              </span>
              <Textarea
                ref={commentRef}
                id="verdict-comment"
                rows={2}
                value={comment}
                onChange={(event) => {
                  setComment(event.target.value);
                  setInvalid(false);
                }}
                aria-invalid={invalid}
                className={cn(invalid && "border-status-fail focus-visible:border-status-fail")}
              />
            </label>
            {invalid && <p className="text-[11px] text-status-fail">Reprovar exige comentário.</p>}
            <div className="flex flex-wrap gap-1.5">
              <Button onClick={() => void send("pass")} disabled={sending}>
                Aprovar <Kbd>A</Kbd>
              </Button>
              <Button variant="destructive" onClick={() => void send("fail")} disabled={sending}>
                Reprovar e seguir <Kbd>R</Kbd>
              </Button>
              <Button
                variant="outline"
                onClick={() => void send("fail_stop")}
                disabled={sending}
                className="text-status-fail"
              >
                Reprovar e parar <Kbd>⇧R</Kbd>
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div className="shrink-0 border-t border-border bg-card/95 py-2 backdrop-blur">
          <div className="pane-column pane-column-wide flex flex-col gap-2">
            {Object.keys(step.recorded_verdict).length > 0 && (
              <p className="text-xs">
                <span className="text-muted-foreground">Veredito: </span>
                {statusLabel(String(step.recorded_verdict.status ?? ""), labels)}
                {step.recorded_verdict.comment ? (
                  <span className="text-muted-foreground"> — {String(step.recorded_verdict.comment)}</span>
                ) : null}
              </p>
            )}
            {session.phase !== "done" && session.focus_index !== session.pending_index && (
              <Button
                variant="link"
                size="sm"
                className="self-start p-0"
                onClick={() => onFocus(session.pending_index)}
              >
                Ir ao passo atual
              </Button>
            )}
            {unit.kind === "case" && unit.scopes.length > 0 && !busy && (
              <div className="flex flex-col gap-1.5">
                {unit.step_note && (
                  <p className="text-[11px] text-muted-foreground">{unit.step_note}</p>
                )}
                <div className="flex flex-wrap gap-1.5">
                  {unit.scopes.map((scope) => (
                    <Button key={scope} variant="outline" size="sm" onClick={() => onStart(scope, "review")}>
                      {unit.scope_labels[scope] ?? labels.scope_label[scope] ?? scope}
                    </Button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function StepHeader({
  step,
  session,
  pane,
  prev,
  total,
  onFocus,
}: {
  step: StepView;
  session: SessionView;
  pane: Pane;
  prev: number;
  total: number;
  onFocus: (index: number) => void;
}) {
  const status = Number(step.http_status);
  const hasStatus = step.http_status !== null && step.http_status !== "" && !Number.isNaN(status);
  const statusVariantName = status >= 500 ? "fail" : status >= 400 ? "warn" : "pass";
  // The nav walks the steps that already ran, so it belongs to a run in progress as
  // much as to one parked at a verdict: reading case two while case nine is on the
  // wire is the whole point of following being a choice.
  const navigable = session.phase === "step" || session.phase === "running" || session.phase === "done";

  return (
    <header className="shrink-0 border-b border-border bg-card/60 py-2 backdrop-blur">
      <div className="pane-column pane-column-wide flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">{step.case_label}</h2>
        {step.has_http && (
          <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            {text(step.request_method)} {text(step.request_url)}
          </span>
        )}
        {hasStatus && (
          <Badge variant={statusVariantName} className="font-mono">
            HTTP {status}
          </Badge>
        )}
        {step.elapsed_ms !== null && (
          <span className="font-mono text-[10px] text-muted-foreground">{step.elapsed_ms} ms</span>
        )}
        {step.pause_reason && (
          <span className="text-[10px] text-status-warn">{step.pause_reason}</span>
        )}
        {pane === "historical" && <Badge variant="muted">run anterior</Badge>}

        {navigable && (
          <nav className="ml-auto flex items-center gap-1.5" aria-label="Passos do run">
            <Button
              variant="ghost"
              size="sm"
              disabled={!session.can_prev}
              onClick={() => onFocus(session.focus_index - 1)}
            >
              <ChevronLeft className="size-3" />
              Anterior <Kbd>K</Kbd>
            </Button>
            <span className="font-mono text-[10px] text-muted-foreground">
              {prev}/{total}
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={!session.can_next}
              onClick={() => onFocus(session.focus_index + 1)}
            >
              Próximo <Kbd>J</Kbd>
              <ChevronRight className="size-3" />
            </Button>
          </nav>
        )}
      </div>
    </header>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-border/70 bg-background/60 px-1 text-[9px] font-normal opacity-70">
      {children}
    </kbd>
  );
}

function Packs({ step }: { step: StepView }) {
  const alerts = step.pack_alerts;
  const ok = step.pack_ok;

  if (step.packs.length === 0) {
    return (
      <section className="flex flex-col gap-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Packs
        </h3>
        <p className="text-[11px] text-muted-foreground">Nenhum pack neste passo.</p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Packs
      </h3>
      {alerts.length > 0 && (
        <ul className="flex flex-col gap-1">
          {alerts.map((pack, index) => (
            <li
              key={`${pack.pack_id}-${index}`}
              className={cn(
                "flex items-start gap-2 rounded border px-2 py-1.5 text-xs",
                pack.status === "fail"
                  ? "border-status-fail/40 bg-status-fail/10"
                  : "border-status-warn/40 bg-status-warn/10",
              )}
            >
              <strong className="font-medium">{pack.pack_id}</strong>
              <Badge variant={packVariant(pack.status)}>{pack.status}</Badge>
              {pack.detail ? (
                <span className="min-w-0 flex-1 break-words text-muted-foreground">
                  {text(pack.detail)}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {ok.length > 0 && (
        <Collapsible title={`Packs que passaram (${ok.length})`} defaultOpen={alerts.length === 0}>
          <ul className="flex flex-col gap-0.5 font-mono text-[11px] text-muted-foreground">
            {ok.map((pack, index) => (
              <li key={`${pack.pack_id}-${index}`}>
                {pack.pack_id} — {pack.status}
                {pack.detail ? ` (${text(pack.detail)})` : ""}
              </li>
            ))}
          </ul>
        </Collapsible>
      )}
    </section>
  );
}

function ProbeTable({ rows }: { rows: StepView["probe_rows"] }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Esperado vs lido
      </h3>
      <div className="overflow-x-auto scroll-thin rounded-md border border-border">
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr className="bg-muted/50 text-left text-muted-foreground">
              <th className="px-2 py-1 font-medium">Superfície</th>
              <th className="px-2 py-1 font-medium">Antes</th>
              <th className="px-2 py-1 font-medium">Depois</th>
              <th className="px-2 py-1 font-medium">Delta</th>
              <th className="px-2 py-1 font-medium">Esperado</th>
              <th className="px-2 py-1 font-medium">Lido</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr
                key={`${text(row.id)}-${index}`}
                className={cn(
                  "border-t border-border/60",
                  row.matched ? "bg-status-pass/5" : "bg-status-fail/10",
                )}
              >
                <td className="px-2 py-1">{text(row.id)}</td>
                <td className="px-2 py-1 font-mono">{text(row.before)}</td>
                <td className="px-2 py-1 font-mono">{text(row.after)}</td>
                <td className="px-2 py-1 font-mono text-muted-foreground">{text(row.delta)}</td>
                <td className="px-2 py-1 font-mono">{row.money ? `R$ ${text(row.esperado)}` : text(row.esperado)}</td>
                <td className={cn("px-2 py-1 font-mono", !row.matched && "text-status-fail")}>
                  {row.money ? `R$ ${text(row.lido)}` : text(row.lido)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Logs({ step }: { step: StepView }) {
  const timeline = useMemo(() => {
    return step.logs_timeline
      .map((entry) => `${text(entry.at) || "sem carimbo"} [${text(entry.source)}] ${text(entry.text)}`)
      .join("\n");
  }, [step.logs_timeline]);

  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Logs
      </h3>
      {step.logs_sources.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          Nenhuma fonte de log declarada — a evidência é só o HTTP.
        </p>
      ) : (
        <>
          <Collapsible
            title={`Logs — ${step.logs_sources.length} fonte(s) declarada(s)`}
            defaultOpen={step.body_open}
          >
            <div className="flex flex-col gap-3">
              {step.logs_sources.map((source, index) => {
                const hasText = Boolean(source.text);
                return (
                  <div key={`${source.id}-${index}`}>
                    <p className="mb-1 text-[11px]">
                      <strong className="font-medium">{source.id}</strong>{" "}
                      <span className="text-muted-foreground">
                        {text(source.role)}
                        {source.propagate === false ? " · propagate: false" : ""} ·{" "}
                        {text(source.reason)}
                      </span>
                    </p>
                    {hasText ? (
                      <pre className="max-h-72 overflow-auto scroll-thin rounded border border-border/70 bg-black/20 p-2 font-mono text-[10px] leading-relaxed">
                        {source.text}
                      </pre>
                    ) : (
                      <p className="text-[11px] text-muted-foreground">
                        {source.propagate === false
                          ? "o projeto declarou que o trace não chega aqui"
                          : "nenhuma linha para este trace"}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </Collapsible>
          {step.logs_timeline.length > 1 && (
            <Collapsible
              title={`Timeline do trace (${step.logs_timeline.length} entradas, ordenadas pelo carimbo declarado)`}
              defaultOpen={false}
            >
              <pre className="max-h-72 overflow-auto scroll-thin rounded border border-border/70 bg-black/20 p-2 font-mono text-[10px] leading-relaxed">
                {timeline}
              </pre>
            </Collapsible>
          )}
        </>
      )}
    </section>
  );
}

function BodyWithCopy({ value }: { value: unknown }) {
  const body = prettyJson(value);
  return (
    <>
      <div className="mb-1.5 flex justify-end">
        <CopyButton value={body} />
      </div>
      <JsonEditor value={body} language="json" minLines={6} />
    </>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-5 text-[10px]"
      onClick={() => {
        void navigator.clipboard?.writeText(value).catch(() => undefined);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1_200);
      }}
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
      {copied ? "Copiado" : "Copiar"}
    </Button>
  );
}

/** A disclosure that keeps its own state, so opening a body never re-renders Monaco
 *  until it is actually looked at. */
function Collapsible({
  title,
  defaultOpen,
  children,
}: {
  title: string;
  defaultOpen: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-border/70">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 rounded-t-md px-2 py-1.5 text-left text-xs outline-none hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <ChevronDown className={cn("size-3 shrink-0 transition-transform", !open && "-rotate-90")} />
        <span className="min-w-0 flex-1 truncate">{title}</span>
      </button>
      {open && <div className="border-t border-border/60 p-2">{children}</div>}
    </div>
  );
}
