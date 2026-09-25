import { useCallback, useEffect, useState } from "react";
import { Copy, FolderOpen, FolderPlus, Plug, RefreshCw, Server, Sparkles, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { ApiRefusal, fetchDemo, fetchMcp, fetchProjects, setDemo, setMcp } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DemoState, McpState, ProjectInfo } from "@/types";

/**
 * What the process behind the window is doing: its own address, the projects it has
 * open, and the two things it can carry — the MCP server and the bundled demo.
 *
 * Four things are deliberate here:
 *
 * - **The API is always on and is not a switch.** It is the window's own transport
 *   over loopback with a per-run token; a toggle for it would be a toggle for the
 *   client working, and the port is ephemeral. It is shown, not offered.
 * - **The MCP switch reports its own failure in place.** A port already taken answers
 *   `state: "error"` with the port in the message, and that is drawn where the switch
 *   is — the one place the reader can act on it. A toast would be a sentence floating
 *   away from the thing that needs fixing.
 * - **The snippet is only offered while the server is up.** A URL pasted into another
 *   tool that answers nothing is advice to fail later.
 * - **The demo switch is one press for three acts.** It starts a mock, materializes a
 *   project around the port that mock bound, and opens it — because a demo nobody can
 *   see is not the feature. Turning it off stops the socket and keeps the files.
 *
 * Every mutation here returns the whole bootstrap, so `onChanged` is how the tree
 * learns that a project appeared or left — the dialog does not hold a copy of the tree
 * and must not start.
 */
export function ServerDialog({
  open,
  onOpenChange,
  onAddProject,
  onRemoveProject,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAddProject: (root: string) => Promise<void>;
  onRemoveProject: (id: string) => Promise<void>;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [mcp, setMcpState] = useState<McpState | null>(null);
  const [demo, setDemoState] = useState<DemoState | null>(null);
  const [registry, setRegistry] = useState("");
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [root, setRoot] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [state, listed, demoState] = await Promise.all([
        fetchMcp(),
        fetchProjects(),
        fetchDemo(),
      ]);
      setMcpState(state);
      setRegistry(listed.registry);
      setProjects(listed.projects);
      setDemoState(demoState);
    } catch (failure) {
      toast.push({
        title: "Não foi possível ler o servidor",
        detail: describe(failure),
        variant: "error",
      });
    }
  }, [toast]);

  // Read on open and never on a timer: the switches change when a person flips one,
  // and the project list changes when a person adds one. Both are here.
  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  const toggleMcp = async () => {
    if (!mcp) return;
    setBusy(true);
    try {
      setMcpState(await setMcp(!mcp.enabled));
    } catch (failure) {
      toast.push({ title: "Não foi possível ligar o MCP", detail: describe(failure), variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  const toggleDemo = async () => {
    if (!demo) return;
    setBusy(true);
    try {
      setDemoState(await setDemo(!demo.enabled));
      await reload();
      // The press adds a project to the tree; the tree is the receipt.
      onChanged();
    } catch (failure) {
      toast.push({ title: "Não foi possível abrir a demonstração", detail: describe(failure), variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  const openDemo = async () => {
    if (!demo?.root) return;
    setBusy(true);
    try {
      await onAddProject(demo.root);
      await reload();
      onChanged();
    } catch (failure) {
      toast.push({ title: "Não foi possível abrir", detail: describe(failure), variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    const trimmed = root.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await onAddProject(trimmed);
      setRoot("");
      await reload();
      onChanged();
    } catch (failure) {
      // The gate's refusal is the interesting one — "this does not look like a
      // project, and here is what one looks like" — so its hint is carried through
      // rather than replaced by a generic sentence.
      toast.push({ title: "Não foi possível abrir", detail: describe(failure), variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (project: ProjectInfo) => {
    setBusy(true);
    try {
      await onRemoveProject(project.id);
      await reload();
      onChanged();
    } catch (failure) {
      toast.push({ title: "Não foi possível remover", detail: describe(failure), variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  const origin = typeof window === "undefined" ? "" : window.location.origin;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <div className="flex flex-col gap-4 p-4">
          <header className="flex flex-col gap-0.5">
            <DialogTitle className="flex items-center gap-1.5">
              <Server className="size-3.5" /> Servidor
            </DialogTitle>
            <DialogDescription>
              A API do processo e os projetos que ele tem abertos. Um plano de cada vez.
            </DialogDescription>
          </header>

          <section className="flex flex-col gap-1.5 rounded border border-border p-2.5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium">API</span>
              <span className="flex items-center gap-1 rounded bg-status-pass/15 px-1.5 py-px text-[10px] text-status-pass">
                <Plug className="size-3" /> no ar
              </span>
            </div>
            <p className="font-mono text-[10px] text-muted-foreground">{origin}</p>
            <p className="text-[11px] text-muted-foreground">
              Loopback, com um token por execução. Não é uma opção desligar: é o que esta
              janela usa para falar com o harness.
            </p>
          </section>

          <section className="flex flex-col gap-2 rounded border border-border p-2.5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium">MCP</span>
              <ToggleSwitch
                enabled={mcp?.enabled ?? false}
                ready={mcp !== null}
                busy={busy}
                label="Servidor MCP embutido"
                onToggle={() => void toggleMcp()}
              />
              {mcp && (
                <span
                  className={cn(
                    "text-[10px]",
                    mcp.state === "running"
                      ? "text-status-pass"
                      : mcp.state === "error"
                        ? "text-status-fail"
                        : "text-muted-foreground",
                  )}
                >
                  {mcp.state === "running"
                    ? `${mcp.host}:${mcp.port}${mcp.path}`
                    : mcp.state === "error"
                      ? "não subiu"
                      : "desligado"}
                </span>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground">
              O mesmo processo serve o MCP em loopback, para um modelo conduzir a revisão.
              Desligado, nada escuta.
            </p>
            {mcp?.error && (
              <div className="rounded border border-status-fail/40 bg-status-fail/10 px-2 py-1.5 text-[11px]">
                <div className="font-medium text-status-fail">{mcp.error.message}</div>
                {mcp.error.hint && (
                  <div className="mt-0.5 text-muted-foreground">{mcp.error.hint}</div>
                )}
              </div>
            )}
            {mcp?.config_snippet && (
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Para o cliente MCP
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-5 gap-1 px-1.5 text-[10px]"
                    onClick={() => void copy(mcp.config_snippet, toast)}
                  >
                    <Copy className="size-3" /> Copiar
                  </Button>
                </div>
                <pre className="overflow-x-auto rounded bg-muted p-2 font-mono text-[10px] leading-relaxed">
                  {mcp.config_snippet}
                </pre>
              </div>
            )}
          </section>

          <section className="flex flex-col gap-2 rounded border border-border p-2.5">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1 text-xs font-medium">
                <Sparkles className="size-3" /> Demonstração
              </span>
              <ToggleSwitch
                enabled={demo?.enabled ?? false}
                ready={demo !== null}
                busy={busy}
                label="Projeto de demonstração"
                onToggle={() => void toggleDemo()}
              />
              {demo && (
                <span
                  className={cn(
                    "text-[10px]",
                    demo.state === "running"
                      ? "text-status-pass"
                      : demo.state === "error"
                        ? "text-status-fail"
                        : "text-muted-foreground",
                  )}
                >
                  {demo.state === "running"
                    ? `${demo.host}:${demo.port}`
                    : demo.state === "error"
                      ? "não subiu"
                      : "desligado"}
                </span>
              )}
              {demo?.root && !demoOpen(demo, projects) && (
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto h-5 shrink-0 gap-1 px-1.5 text-[10px]"
                  onClick={() => void openDemo()}
                  disabled={busy}
                >
                  <FolderOpen className="size-3" /> Abrir
                </Button>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground">
              Um projeto de exemplo com casos que passam e casos que falham de propósito.
              Ligado, sobe uma API de mentira em loopback e materializa o projeto em{" "}
              <code className="font-mono text-[10px]">{demo?.root || "…"}</code>.
            </p>
            {demo?.error && (
              <div className="rounded border border-status-fail/40 bg-status-fail/10 px-2 py-1.5 text-[11px]">
                <div className="font-medium text-status-fail">{demo.error.message}</div>
                {demo.error.hint && (
                  <div className="mt-0.5 text-muted-foreground">{demo.error.hint}</div>
                )}
              </div>
            )}
          </section>

          <section className="flex flex-col gap-2 rounded border border-border p-2.5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium">Projetos</span>
              <span className="text-[10px] text-muted-foreground">
                {projects.filter((project) => project.open).length} abertos de{" "}
                {projects.length}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto h-5 gap-1 px-1.5 text-[10px]"
                onClick={() => void reload()}
                disabled={busy}
              >
                <RefreshCw className="size-3" /> Recarregar
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">
              O registro fica em{" "}
              <code className="font-mono text-[10px]">{registry || "…"}</code> — configuração
              de quem revisa, não do projeto.
            </p>
            <div className="flex items-center gap-1.5">
              <Input
                value={root}
                onChange={(event) => setRoot(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void add();
                }}
                placeholder="/caminho/do/projeto"
                aria-label="Caminho do projeto a abrir"
                autoComplete="off"
                className="h-7 font-mono text-[11px]"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-7 shrink-0 gap-1 text-[11px]"
                onClick={() => void add()}
                disabled={busy || !root.trim()}
              >
                <FolderPlus className="size-3" /> Abrir
              </Button>
            </div>
            {projects.length > 0 && (
              <ul className="flex flex-col gap-px">
                {projects.map((project) => (
                  <li
                    key={project.id}
                    className="flex items-center gap-2 rounded px-1.5 py-1 text-[11px] hover:bg-accent/60"
                  >
                    <span className="min-w-0 flex-1 truncate" title={project.root}>
                      <span className="mr-1.5 font-medium">{project.name}</span>
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {project.root}
                      </span>
                    </span>
                    {!project.open && (
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        não aberto
                      </span>
                    )}
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="shrink-0"
                      aria-label={`Remover ${project.name}`}
                      title="Fechar este projeto (nada é apagado do disco)"
                      onClick={() => void remove(project)}
                      disabled={busy}
                    >
                      <Trash2 className="size-3" />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The switch the dialog's two optional servers share.
 *
 * `role="switch"` rather than a checkbox: it is one binary setting, and a screen reader
 * should say "ligado" or "desligado" rather than read a form control. It reflects the
 * *server's* state, which is why a failed bind leaves it off — the truth is that
 * nothing is listening. `ready` is false until the state has been read, so the switch
 * cannot be flipped into a request the dialog has no answer for.
 */
function ToggleSwitch({
  enabled,
  ready,
  busy,
  label,
  onToggle,
}: {
  enabled: boolean;
  ready: boolean;
  busy: boolean;
  label: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={label}
      onClick={onToggle}
      disabled={busy || !ready}
      className={cn(
        "relative h-4 w-8 shrink-0 rounded-full border outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-50",
        enabled ? "border-primary/60 bg-primary/40" : "border-border bg-muted",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 size-2.5 rounded-full bg-foreground transition-all",
          enabled ? "left-4" : "left-0.5",
        )}
      />
    </button>
  );
}

/** Whether the demo's project is already one the tree is drawing. */
function demoOpen(demo: DemoState, projects: ProjectInfo[]): boolean {
  return projects.some((project) => project.id === demo.project_id && project.open);
}

async function copy(text: string, toast: ReturnType<typeof useToast>): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast.push({ title: "Copiado", variant: "info" });
  } catch {
    // A webview can refuse the clipboard without a gesture it trusts. The snippet is
    // on screen and selectable, so this is a convenience failing and not the feature.
    toast.push({ title: "Selecione e copie o bloco à mão", variant: "error" });
  }
}

function describe(failure: unknown): string {
  if (failure instanceof ApiRefusal) {
    return [failure.payload.error.message, failure.payload.error.hint].filter(Boolean).join(" — ");
  }
  return failure instanceof Error ? failure.message : String(failure);
}
