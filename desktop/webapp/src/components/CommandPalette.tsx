import { useMemo } from "react";
import { Ban, FileCode2, Play, RefreshCw, Search, Sparkles, Terminal } from "lucide-react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { statusLabel } from "@/lib/status";
import { flatten } from "@/lib/tree";
import { cn } from "@/lib/utils";
import type { Bootstrap } from "@/types";

/**
 * ⌘K.
 *
 * This is the detail that makes a window read as an application rather than a page,
 * and it is also genuinely the fastest path to the thing a reviewer wants: a campaign
 * tree of seventy rounds is a lot to click through, and "note-omit" finding the case
 * in one keystroke is the difference between the tree being a directory and being a
 * search index.
 *
 * The run actions are not enumerated here — they are `unit.scopes`, the same table the
 * card's buttons come from. A palette with its own list of scopes would be the second
 * answer to "what can this node run".
 */
export function CommandPalette({
  bootstrap,
  open,
  onOpenChange,
  onSelect,
  onStart,
  onCancel,
  onRefresh,
  onEdit,
  onDemo,
}: {
  bootstrap: Bootstrap;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (key: string) => void;
  onStart: (scope: string, mode: string) => void;
  onCancel: () => void;
  onRefresh: () => void;
  onEdit: (path: string) => void;
  /** Open the bundled demo project, the same act the Server dialog's switch performs. */
  onDemo: () => void;
}) {
  const nodes = useMemo(() => flatten(bootstrap.tree), [bootstrap.tree]);
  const failing = useMemo(
    () => nodes.filter((node) => node.status === "fail" || node.status === "http_5xx"),
    [nodes],
  );
  // Every file the collection knows about that the server would let us write: rounds and
  // campaigns. This is the second way into the editor, and for a tree of seventy rounds
  // it is the only one worth using — typing four letters beats clicking a node, waiting
  // for the card, and then finding the button.
  const files = useMemo(() => editableFiles(bootstrap.tree), [bootstrap.tree]);

  const run = (action: () => void) => {
    onOpenChange(false);
    action();
  };

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Ir para um caso, rodar, cancelar…" />
      <CommandList>
        <CommandEmpty>Nada bate com a busca.</CommandEmpty>

        <CommandGroup heading="Ações">
          {bootstrap.unit.scopes.map((scope) => (
            <CommandItem
              key={scope}
              value={`rodar ${bootstrap.unit.scope_labels[scope] ?? scope} ${bootstrap.unit.label}`}
              onSelect={() => run(() => onStart(scope, bootstrap.session.mode || "review"))}
            >
              <Play className="size-3" />
              {bootstrap.unit.scope_labels[scope] ?? scope}
              <CommandShortcut>{bootstrap.unit.label}</CommandShortcut>
            </CommandItem>
          ))}
          {bootstrap.engine.busy && (
            <CommandItem value="cancelar plano" onSelect={() => run(onCancel)}>
              <Ban className="size-3 text-status-fail" />
              Cancelar o plano em andamento
            </CommandItem>
          )}
          <CommandItem value="recarregar colecao" onSelect={() => run(onRefresh)}>
            <RefreshCw className="size-3" />
            Recarregar a coleção
            <CommandShortcut>R</CommandShortcut>
          </CommandItem>
          <CommandItem value="abrir projeto de demonstracao demo" onSelect={() => run(onDemo)}>
            <Sparkles className="size-3 text-muted-foreground" />
            Abrir o projeto de demonstração
          </CommandItem>
        </CommandGroup>

        {failing.length > 0 && (
          <CommandGroup heading={`Falhando (${failing.length})`}>
            {failing.slice(0, 40).map((node) => (
              <CommandItem
                key={node.key}
                value={`${node.label} ${node.kind} ${node.path ?? ""} ${node.case_id ?? ""}`}
                onSelect={() => run(() => onSelect(node.key))}
              >
                <span
                  className={cn(
                    "size-1.5 shrink-0 rounded-full",
                    node.status === "http_5xx" ? "bg-status-http" : "bg-status-fail",
                  )}
                />
                <span className="min-w-0 flex-1 truncate">{node.label}</span>
                <CommandShortcut>{statusLabel(node.status, bootstrap.labels)}</CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        <CommandGroup heading="Coleção">
          {nodes
            .filter((node) => node.kind === "round")
            .slice(0, 200)
            .map((node) => (
              <CommandItem
                key={node.key}
                value={`${node.label} ${node.endpoint ?? ""} ${node.path ?? ""}`}
                onSelect={() => run(() => onSelect(node.key))}
              >
                <Search className="size-3 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{node.label}</span>
                {node.endpoint && (
                  <span className="truncate font-mono text-[10px] text-muted-foreground">
                    {node.endpoint}
                  </span>
                )}
              </CommandItem>
            ))}
        </CommandGroup>

        {files.length > 0 && (
          <CommandGroup heading="Editar um arquivo">
            {files.slice(0, 100).map((file) => (
              <CommandItem
                key={file.path}
                value={`editar ${file.path} ${file.label}`}
                onSelect={() => run(() => onEdit(file.path))}
              >
                <FileCode2 className="size-3 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{file.path}</span>
                <CommandShortcut>{file.label}</CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {bootstrap.session.run_dir && (
          <CommandGroup heading="Sessão">
            <CommandItem
              value="copiar caminho do run"
              onSelect={() =>
                run(() => {
                  void navigator.clipboard?.writeText(bootstrap.session.run_dir).catch(() => undefined);
                })
              }
            >
              <Terminal className="size-3" />
              Copiar o caminho do run
              <CommandShortcut className="truncate font-mono normal-case tracking-normal">
                {bootstrap.session.run_dir.split("/").slice(-1)[0]}
              </CommandShortcut>
            </CommandItem>
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
}

/**
 * The files the palette offers to edit, de-duplicated by path.
 *
 * A round under two campaigns appears twice in the tree and is one file; offering it
 * twice would make the palette's list look longer than the project is. The kind is taken
 * from the node rather than from the path so the label is the harness's own word.
 */
function editableFiles(tree: Bootstrap["tree"]): { path: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const node of flatten(tree)) {
    if (!node.path) continue;
    if (node.kind !== "round" && node.kind !== "campaign") continue;
    if (!seen.has(node.path)) seen.set(node.path, node.kind);
  }
  return [...seen].map(([path, label]) => ({ path, label }));
}
