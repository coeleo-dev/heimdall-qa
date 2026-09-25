import { useCallback, useEffect, useState } from "react";
import { Command as CommandIcon, PanelLeftClose, PanelLeftOpen, Server, Wifi, WifiOff } from "lucide-react";

import { CampaignPane } from "@/components/CampaignPane";
import { CollectionTree } from "@/components/CollectionTree";
import { CommandPalette } from "@/components/CommandPalette";
import { DonePane } from "@/components/DonePane";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LiveMark } from "@/components/LiveMark";
import { ServerDialog } from "@/components/ServerDialog";
import { SourcePane } from "@/components/SourcePane";
import { StatusBar } from "@/components/StatusBar";
import { StepPane } from "@/components/StepPane";
import { UnitCard } from "@/components/UnitCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useHarness } from "@/hooks/useHarness";
import { setDemo } from "@/lib/api";
import { apiToken } from "@/lib/token";
import { cn } from "@/lib/utils";
import type { Bootstrap, VerdictStatus } from "@/types";

const SIDEBAR_MIN = 260;
const SIDEBAR_MAX = 720;
const SIDEBAR_KEY = "heimdall.sidebar.width";

/**
 * The window.
 *
 * A shell, not a page: a sidebar that owns the collection, a pane that shows one thing
 * at a time, and a status bar that is always telling the truth about the engine. The
 * layout is the one a developer already has in their fingers from an editor — which is
 * the point of a desktop client rather than a report.
 */
export function App() {
  const harness = useHarness();
  const toast = useToast();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [serverOpen, setServerOpen] = useState(false);
  const [sidebar, setSidebar] = useState(() => readSidebarWidth());
  const [sidebarVisible, setSidebarVisible] = useState(true);
  //: The file the editor is holding, or `null`. Deliberately client state: opening a
  //: file changes nothing about a run, and giving it a server pane would let the client
  //: draw a surface the engine has no word for.
  const [editing, setEditing] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((current) => !current);
      }
      // `Esc` closes the editor, but only when Monaco does not want it first — the
      // editor uses `Esc` to leave a suggestion popup or a multi-cursor, and stealing
      // it there would close the file under a reader who was mid-edit.
      if (event.key === "Escape" && !(event.target as HTMLElement)?.closest?.(".monaco-editor")) {
        setEditing(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const openSource = useCallback((path: string) => setEditing(path), []);

  const start = useCallback(
    async (scope: string, mode: string, node?: string) => {
      try {
        await harness.start(scope, mode, node);
        toast.push({ title: "Plano iniciado", variant: "info" });
      } catch (failure) {
        const error = failure as { message?: string; hint?: string };
        toast.push({
          title: "Não foi possível iniciar",
          detail: [error.message, error.hint].filter(Boolean).join(" — "),
          variant: "error",
        });
      }
    },
    [harness, toast],
  );

  const verdict = useCallback(
    async (status: VerdictStatus, comment: string, continueRound: boolean) => {
      const ok = await harness.verdict(status, comment, continueRound);
      if (!ok) {
        toast.push({
          title: "Veredito recusado",
          detail: harness.error ? `${harness.error.message} ${harness.error.hint}` : "",
          variant: "error",
        });
      }
      return ok;
    },
    [harness, toast],
  );

  const bootstrap = harness.bootstrap;

  /**
   * A collection write, reported the same way everywhere.
   *
   * The four of these — new folder, move, open project, close project — answer with
   * the whole bootstrap, so a success is silent and the tree is the receipt. A refusal
   * is not: it is either the gate saying what a project looks like or the filesystem
   * saying no, and both need reading.
   */
  const collectionAction = useCallback(
    async (label: string, run: () => Promise<void>) => {
      try {
        await run();
      } catch (failure) {
        const error = failure as { message?: string; hint?: string };
        toast.push({
          title: label,
          detail: [error.message, error.hint].filter(Boolean).join(" — "),
          variant: "error",
        });
      }
    },
    [toast],
  );

  /**
   * The command palette's door into the demo.
   *
   * One press for what the Server dialog's switch does, so a reviewer who never opens
   * the dialog can still reach it. It is the same call, not a second path: the server
   * starts the mock, materializes around the port it bound and opens the project.
   */
  const enableDemo = useCallback(
    () =>
      collectionAction("Não foi possível abrir a demonstração", async () => {
        await setDemo(true);
        await harness.refresh();
      }),
    [collectionAction, harness],
  );

  if (!bootstrap) {
    return <BootScreen error={harness.error} hasToken={Boolean(apiToken())} />;
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar
        bootstrap={bootstrap}
        live={harness.live}
        pending={harness.pending}
        sidebarVisible={sidebarVisible}
        onToggleSidebar={() => setSidebarVisible((current) => !current)}
        onOpenPalette={() => setPaletteOpen(true)}
        onOpenServer={() => setServerOpen(true)}
        onReconnect={harness.reconnect}
      />

      {harness.error && (
        <ErrorBanner error={harness.error} onDismiss={harness.clearError} />
      )}

      <div className="flex min-h-0 flex-1">
        {sidebarVisible && (
          <>
            <aside
              className="shrink-0 border-r border-border bg-sidebar"
              style={{ width: `${sidebar}px` }}
            >
              <CollectionTree
                tree={bootstrap.tree}
                selected={bootstrap.selected}
                labels={bootstrap.labels}
                onSelect={(key) => void harness.select(key)}
                onCreateFolder={(path, project) =>
                  collectionAction("Não foi possível criar a pasta", () =>
                    harness.newFolder(path, project),
                  )
                }
                onMoveCampaign={(path, directory, project) =>
                  collectionAction("Não foi possível mover", () =>
                    harness.move(path, directory, project),
                  )
                }
                onRemoveProject={(id) =>
                  collectionAction("Não foi possível remover o projeto", () =>
                    harness.closeProject(id),
                  )
                }
              />
            </aside>
            <Resizer width={sidebar} onWidth={setSidebar} />
          </>
        )}

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
            {editing ? (
              <SourcePane
                key={editing}
                path={editing}
                onClose={() => setEditing(null)}
                onSaved={() => void harness.refresh()}
              />
            ) : (
              // Keyed by the pane and not by the step: a new pane arriving should
              // feel like the app answering, and a step advancing under the reader's
              // cursor should not — remounting here would throw away the comment box
              // mid-verdict.
              <div key={bootstrap.pane} className="pane-in h-full">
                <Pane
                  bootstrap={bootstrap}
                  busy={bootstrap.engine.busy}
                  onSelect={(key) => void harness.select(key)}
                  onStart={start}
                  onVerdict={verdict}
                  onFocus={(index) => void harness.focus(index)}
                  onEdit={openSource}
                />
              </div>
            )}
          </div>

          <StatusBar
            engine={bootstrap.engine}
            session={bootstrap.session}
            labels={bootstrap.labels}
            environment={bootstrap.session.environment}
            selected={bootstrap.selected}
            pendingKey={bootstrap.pending_key}
            onSelect={(key) => void harness.select(key)}
            cancelDisabled={harness.pending}
            onCancel={() => void harness.cancel()}
          />
        </main>
      </div>

      <CommandPalette
        bootstrap={bootstrap}
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onSelect={(key) => void harness.select(key)}
        onStart={start}
        onCancel={() => void harness.cancel()}
        onRefresh={() => void harness.refresh()}
        onEdit={openSource}
        onDemo={() => void enableDemo()}
      />

      <ServerDialog
        open={serverOpen}
        onOpenChange={setServerOpen}
        onAddProject={async (root) => {
          // The refusal is not swallowed here: the gate's message is the whole point of
          // the dialog, and `useHarness.openProject` lets it through for the caller.
          await harness.openProject(root);
        }}
        onRemoveProject={async (id) => {
          await harness.closeProject(id);
        }}
        onChanged={() => void harness.refresh()}
      />
    </div>
  );
}

function Pane({
  bootstrap,
  busy,
  onSelect,
  onStart,
  onVerdict,
  onFocus,
  onEdit,
}: {
  bootstrap: Bootstrap;
  busy: boolean;
  onSelect: (key: string) => void;
  onStart: (scope: string, mode: string, node?: string) => Promise<void>;
  onVerdict: (status: VerdictStatus, comment: string, continueRound: boolean) => Promise<boolean>;
  onFocus: (index: number) => void;
  onEdit: (path: string) => void;
}) {
  const { pane, step, run, unit, engine, session, tree, labels, rollup } = bootstrap;

  if (pane === "campaign") {
    return (
      <CampaignPane
        unit={unit}
        engine={engine}
        session={session}
        tree={tree}
        labels={labels}
        rollup={rollup}
        busy={busy}
        nextUnreviewed={bootstrap.next_unreviewed}
        onStart={onStart}
        onSelect={onSelect}
      />
    );
  }

  if (pane === "done" && run) {
    return (
      <DonePane
        run={run}
        engine={engine}
        session={session}
        unit={unit}
        labels={labels}
        busy={busy}
        onSelect={onSelect}
        onStart={onStart}
      />
    );
  }

  if ((pane === "review" || pane === "historical") && step) {
    return (
      <StepPane
        step={step}
        session={session}
        unit={unit}
        labels={labels}
        pane={pane}
        busy={busy}
        onVerdict={onVerdict}
        onFocus={onFocus}
        onStart={onStart}
      />
    );
  }

  return (
    <div className="h-full overflow-y-auto scroll-thin">
      <UnitCard
        unit={unit}
        session={session}
        engine={engine}
        labels={labels}
        busy={busy}
        onStart={onStart}
        onSelect={onSelect}
        onEdit={onEdit}
      />
    </div>
  );
}

function TopBar({
  bootstrap,
  live,
  pending,
  sidebarVisible,
  onToggleSidebar,
  onOpenPalette,
  onOpenServer,
  onReconnect,
}: {
  bootstrap: Bootstrap;
  live: boolean;
  pending: boolean;
  sidebarVisible: boolean;
  onToggleSidebar: () => void;
  onOpenPalette: () => void;
  onOpenServer: () => void;
  onReconnect: () => void;
}) {
  const { engine, labels, selected, unit } = bootstrap;

  return (
    <header className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-card/70 px-2 backdrop-blur">
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={onToggleSidebar}
        aria-label={sidebarVisible ? "Esconder a coleção" : "Mostrar a coleção"}
        title={sidebarVisible ? "Esconder a coleção" : "Mostrar a coleção"}
        className="shrink-0"
      >
        {sidebarVisible ? (
          <PanelLeftClose className="size-3.5" />
        ) : (
          <PanelLeftOpen className="size-3.5" />
        )}
      </Button>

      {/* Where the selection is, in the window's own title. The breadcrumb is the
          collection path and not just a label, because "which campaign is this case
          in" is the question a one-tree window makes easy to lose. It gives way
          before the run does: on a narrow window the dot, the phase and the case are
          the three things that cannot be missing, and this is the one that can. */}
      <nav aria-label="Seleção" className="hidden min-w-0 items-center gap-1 sm:flex">
        <span className="truncate text-xs font-medium" title={selected}>
          {unit.label}
        </span>
        {unit.kind && (
          <span className="hidden shrink-0 text-[10px] text-muted-foreground lg:inline">
            {unit.step_kind
              ? (labels.step_kind_label[unit.step_kind] ?? unit.kind)
              : (labels.kind_label[unit.kind] ?? unit.kind)}
          </span>
        )}
      </nav>

      {/* What the run is doing, where the window's own title is. The status bar says
          the same thing at the bottom; putting it here too is what makes it visible
          without moving the eye away from the tree. */}
      {(engine.busy || engine.awaiting) && (
        <span
          className={cn(
            "flex min-w-0 shrink items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px]",
            engine.awaiting
              ? "border-status-warn/40 bg-status-warn/10"
              : "border-live/30 bg-live/10",
          )}
        >
          <LiveMark live={engine.awaiting ? "awaiting" : "running"} />
          <span
            className={cn(
              "shrink-0 font-medium",
              engine.awaiting ? "text-status-warn" : "text-live",
            )}
          >
            {labels.phase_label[engine.phase] ?? engine.phase}
          </span>
          {engine.unit_label && (
            <span className="max-w-32 truncate font-mono text-muted-foreground">
              {engine.unit_label}
            </span>
          )}
          {engine.case_id && (
            <code className="max-w-40 truncate font-mono text-foreground/80">{engine.case_id}</code>
          )}
        </span>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        {pending && (
          <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <span className="size-1.5 animate-pulse rounded-full bg-muted-foreground" aria-hidden />
            enviando…
          </span>
        )}
        {/* The connection, and the way back from a dropped one. A dead stream is the
            one failure the window can fix itself, so the fix is on the chip rather
            than in a menu the reader has to find while the screen is frozen. */}
        {live ? (
          <span
            className="flex items-center gap-1 rounded-full border border-status-pass/30 bg-status-pass/10 px-1.5 py-0.5 text-[10px] text-status-pass"
            title="ligado ao processo do harness; cada mudança chega sozinha"
          >
            <Wifi className="size-3" />
            ao vivo
          </span>
        ) : (
          <span className="flex items-center gap-1 rounded-full border border-status-fail/40 bg-status-fail/10 py-0.5 pl-1.5 text-[10px] text-status-fail">
            <WifiOff className="size-3" />
            sem stream
            <button
              type="button"
              onClick={onReconnect}
              className="rounded-full bg-status-fail/20 px-1.5 py-px font-medium outline-none transition-colors hover:bg-status-fail/30 focus-visible:ring-2 focus-visible:ring-ring/60"
              title="Reabrir o stream e reler a tela"
            >
              Reconectar
            </button>
          </span>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={onOpenServer}
          className="h-6 gap-1.5 text-[10px]"
          title="Servidor: a API, o MCP e os projetos abertos"
        >
          <Server className="size-3" /> Servidor
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onOpenPalette}
          className="h-6 gap-1.5 text-[10px] text-muted-foreground"
          title="Paleta de comandos (Ctrl/Cmd+K)"
        >
          <CommandIcon className="size-3" />K
        </Button>
      </div>
    </header>
  );
}

/** The sidebar drag handle. A pointer drag rather than a CSS resize, because the
 *  handle has to stay a real focusable separator for the keyboard to move it too. */
function Resizer({
  width,
  onWidth,
}: {
  width: number;
  onWidth: (width: number) => void;
}) {
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (event: PointerEvent) => {
      const next = clamp(event.clientX, SIDEBAR_MIN, SIDEBAR_MAX);
      onWidth(next);
      storeSidebarWidth(next);
    };
    const stop = () => setDragging(false);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", stop);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", stop);
    };
  }, [dragging, onWidth]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Redimensionar coleção"
      aria-valuemin={SIDEBAR_MIN}
      aria-valuemax={SIDEBAR_MAX}
      aria-valuenow={width}
      tabIndex={0}
      onPointerDown={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 48 : 16;
        if (event.key === "ArrowLeft") onWidth(clamp(width - step, SIDEBAR_MIN, SIDEBAR_MAX));
        else if (event.key === "ArrowRight") onWidth(clamp(width + step, SIDEBAR_MIN, SIDEBAR_MAX));
        else if (event.key === "Home") onWidth(SIDEBAR_MIN);
        else if (event.key === "End") onWidth(SIDEBAR_MAX);
        else return;
        event.preventDefault();
        storeSidebarWidth(width);
      }}
      className={cn(
        "w-1 shrink-0 cursor-col-resize outline-none transition-colors hover:bg-primary/40 focus-visible:bg-primary/60",
        dragging && "bg-primary/60",
      )}
    />
  );
}

function BootScreen({
  error,
  hasToken,
}: {
  error: { code: string; message: string; hint: string } | null;
  hasToken: boolean;
}) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="flex w-full max-w-md flex-col gap-3">
        <h1 className="text-sm font-semibold">Heimdall QA</h1>
        {!hasToken ? (
          // The one failure that has a fix the reader can act on, and the one that is
          // most confusing if it is reported as "401": the window was opened by hand
          // instead of by the shell, so it has no token.
          <p className="text-xs text-muted-foreground">
            Esta janela não recebeu o token da execução. Abra o cliente pelo comando{" "}
            <code className="rounded bg-muted px-1 font-mono">heimdall-qa</code> (ou{" "}
            <code className="rounded bg-muted px-1 font-mono">heimdall-qa desktop</code>),
            que passa o token na URL.
          </p>
        ) : error ? (
          <div className="rounded-md border border-status-fail/40 bg-status-fail/10 p-3 text-xs">
            <div className="font-medium text-status-fail">{error.message}</div>
            {error.hint && <div className="mt-1 text-muted-foreground">{error.hint}</div>}
          </div>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">Lendo a coleção…</p>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-6 w-2/3" />
          </>
        )}
      </div>
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  // A third of the window, not 70%: at 70% the collection and the thing it opened are
  // both cramped, and the tree is a navigation column — a sidebar that takes most of
  // the screen is a pane, and the reader has one of those already.
  const limit = Math.max(min, Math.floor(window.innerWidth * 0.34));
  return Math.min(Math.min(max, Math.max(min, limit)), Math.max(min, value));
}

function readSidebarWidth(): number {
  try {
    const stored = Number(window.localStorage.getItem(SIDEBAR_KEY));
    if (Number.isFinite(stored) && stored >= SIDEBAR_MIN) return clamp(stored, SIDEBAR_MIN, SIDEBAR_MAX);
  } catch {
    /* a webview with storage disabled gets the default */
  }
  return 300;
}

function storeSidebarWidth(width: number): void {
  try {
    window.localStorage.setItem(SIDEBAR_KEY, String(width));
  } catch {
    /* not worth reporting */
  }
}
