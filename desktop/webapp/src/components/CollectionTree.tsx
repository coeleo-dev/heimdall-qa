import { useMemo, useState } from "react";
import { ChevronRight, FolderPlus, ListTree, MoveRight, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { StatusIcon } from "@/components/StatusIcon";
import { statusLabel } from "@/lib/status";
import { cn } from "@/lib/utils";
import type { Labels, TreeNode } from "@/types";

/** The statuses the chips filter on, in the order the page has always offered them.
 *
 * The set is not "every status": `pass` is the norm and filtering to it is not a
 * question anyone asks, while `fail` and `http_5xx` are two different findings and
 * must not be merged into one chip. */
const CHIPS = ["fail", "http_5xx", "not_reviewed", "not_ready"] as const;

/** Where a campaign can be moved, relative to the project's content root.
 *
 * The root is always a destination — moving something back out of a folder has to be
 * as easy as moving it in, or sorting becomes one-way. */
export const CAMPAIGNS_ROOT = "campaigns";

/** The kinds a "Nova pasta" can be made inside. A round's folder is not one of them:
 *  a round is a file, and the loaders only ever read `campaigns/`. */
const FOLDER_PARENTS = new Set(["project", "directory", "campaign", "folder"]);

/**
 * The collection, as a tree a reviewer can actually read at seventy rounds.
 *
 * Two things are load-bearing and both are inherited from the Jinja tree rather than
 * invented here:
 *
 * - **A node shows if it or anything under it matches.** A matching case whose campaign
 *   is hidden is a result nobody can reach, so the filter walks bottom-up.
 * - **The server decides the default layout; the reviewer only overrides it.** The
 *   open path is the path to the selection, and a client that kept its own expansion
 *   state would fight the server on every stream update.
 *
 * One thing is deliberately better than the page: while a filter is active, branches
 * that contain a match open themselves. The page left them closed, which meant a
 * search could report "1 result" inside a collapsed campaign and show nothing.
 *
 * The third thing this file has that the page never did is the row action: creating a
 * folder and moving a campaign into it are the two writes a reviewer makes to the
 * *collection* rather than to a file, and they belong where the collection is.
 */
export function CollectionTree({
  tree,
  selected,
  labels,
  onSelect,
  onCreateFolder,
  onMoveCampaign,
  onRemoveProject,
}: {
  tree: TreeNode[];
  selected: string;
  labels: Labels;
  onSelect: (key: string) => void;
  onCreateFolder: (path: string, project: string) => Promise<void>;
  onMoveCampaign: (path: string, directory: string, project: string) => Promise<void>;
  onRemoveProject: (id: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Map<string, boolean>>(new Map());

  const needle = query.trim().toLowerCase();
  const filtering = needle.length > 0 || statuses.size > 0;

  const visible = useMemo(() => {
    const matched = new Map<string, boolean>();
    const walk = (node: TreeNode): boolean => {
      const own =
        (!needle || node.label.toLowerCase().includes(needle)) &&
        (statuses.size === 0 || statuses.has(node.status));
      // `map(...).some(...)` and not `some(walk)`: `some` stops at the first child
      // that returns true, and with no filter active *every* child returns true, so
      // the siblings after the first were never recorded in `matched` and were then
      // filtered out of the render. That is a campaign rendering one flow, and a
      // project rendering one campaign. Every child has to be visited for its own
      // entry to exist, whether or not its neighbour already matched.
      const child = node.children.map(walk).some(Boolean);
      const show = own || child;
      matched.set(node.key, show);
      return show;
    };
    tree.forEach(walk);
    return matched;
  }, [tree, needle, statuses]);

  const anyVisible = useMemo(
    () => tree.some((node) => visible.get(node.key)),
    [tree, visible],
  );

  const toggleStatus = (status: string) => {
    setStatuses((current) => {
      const next = new Set(current);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  };

  //: Where the run is, as a mark on every node that contains it. Marking only the
  //: case would put the dot under a collapsed round, which is the state the tree is
  //: in most of the time — a campaign is forty rows and nobody expands all of them.
  //: So the case is marked and so is every ancestor, and "where did it stop" survives
  //: the tree being closed.
  //:
  //: The mark itself comes off the node and is never matched here: a node is told it is
  //: running by the walk that painted the queue, which is the only thing that knows
  //: which of a suite's six identically-labelled rows is on the wire. All this adds is
  //: the hop up the tree, so a collapsed campaign still shows the dot.
  const live = useMemo(() => {
    const marks = new Map<string, "running" | "awaiting">();
    const walk = (node: TreeNode): "running" | "awaiting" | null => {
      let found: "running" | "awaiting" | null =
        node.live === "running" ? "running" : node.live === "awaiting" ? "awaiting" : null;
      for (const child of node.children) {
        const under = walk(child);
        if (under && !found) found = under;
      }
      if (found) marks.set(node.key, found);
      return found;
    };
    tree.forEach(walk);
    return marks;
  }, [tree]);

  const collapsible = useMemo(() => {    const keys: string[] = [];
    const walk = (nodes: TreeNode[]) => {
      for (const node of nodes) {
        if (node.children.length > 0) keys.push(node.key);
        walk(node.children);
      }
    };
    walk(tree);
    return keys;
  }, [tree]);

  /**
   * Every folder in the tree, as a move destination, grouped by project.
   *
   * Read off the tree rather than fetched: the folders *are* the tree nodes, and a
   * separate request would be a second answer to "which folders exist" that can
   * disagree with the one on screen. The root of `campaigns/` is always included,
   * because moving a campaign back out of a folder has to be as easy as moving it in.
   */
  const destinations = useMemo(() => {
    const byProject = new Map<string, { path: string; label: string }[]>();
    for (const project of tree) {
      if (project.kind !== "project") continue;
      const found: { path: string; label: string }[] = [
        { path: CAMPAIGNS_ROOT, label: CAMPAIGNS_ROOT },
      ];
      const walk = (nodes: TreeNode[]) => {
        for (const node of nodes) {
          if (node.kind === "directory" && node.path) {
            found.push({ path: node.path, label: node.path });
          }
          walk(node.children);
        }
      };
      walk(project.children);
      byProject.set(project.project, found);
    }
    return byProject;
  }, [tree]);

  const isOpen = (node: TreeNode): boolean => {
    const override = collapsed.get(node.key);
    if (override !== undefined) return !override;
    // While filtering, a branch that contains a match opens: see the note above.
    if (filtering) return true;
    return node.expanded || node.key === selected;
  };

  const allCollapsed = collapsible.length > 0 && collapsible.every((key) => collapsed.get(key) === true);

  /**
   * The one thing the client decides alone, and it still does not lose the server's
   * layout: `collapsible.length` is asked of the tree, so "expandir tudo" is offered
   * whenever there is something shut, not only when the client shut it.
   */
  const toggleAll = () => {
    const next = new Map(collapsed);
    for (const key of collapsible) next.set(key, !allCollapsed);
    setCollapsed(next);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-2 pb-2 pt-2">
        <div className="mb-1.5 flex items-center gap-1.5 px-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          <ListTree className="size-3" />
          Coleção
        </div>
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filtrar por nome…"
          aria-label="Filtrar a coleção por nome"
          autoComplete="off"
          className="mb-1.5"
        />
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filtrar por estado">
          {CHIPS.map((status) => (
            <FilterChip
              key={status}
              label={statusLabel(status, labels)}
              active={statuses.has(status)}
              danger={status === "fail" || status === "http_5xx"}
              onClick={() => toggleStatus(status)}
            />
          ))}
          <Button variant="ghost" size="sm" onClick={toggleAll} className="h-5 px-1.5 text-[10px]">
            {allCollapsed ? "Expandir tudo" : "Recolher tudo"}
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin px-1 py-1">
        {anyVisible ? (
          <ul className="flex flex-col">
            {tree
              .filter((node) => visible.get(node.key))
              .map((node) => (
                <TreeBranch
                  key={node.key}
                  node={node}
                  depth={0}
                  selected={selected}
                  live={live}
                  labels={labels}
                  visible={visible}
                  isOpen={isOpen}
                  destinations={destinations}
                  onCreateFolder={onCreateFolder}
                  onMoveCampaign={onMoveCampaign}
                  onRemoveProject={onRemoveProject}
                  onToggle={(key, next) => {
                    setCollapsed((current) => new Map(current).set(key, next));
                  }}
                  onSelect={onSelect}
                />
              ))}
          </ul>
        ) : (
          <p className="px-2 py-4 text-center text-[11px] text-muted-foreground">
            Nada na coleção bate com o filtro.
          </p>
        )}
      </div>
    </div>
  );
}

function FilterChip({
  label,
  active,
  danger,
  onClick,
}: {
  label: string;
  active: boolean;
  danger: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded border px-1.5 py-px text-[10px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60",
        active
          ? danger
            ? "border-status-fail/50 bg-status-fail/20 text-status-fail"
            : "border-primary/50 bg-primary/20 text-foreground"
          : "border-border text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

function TreeBranch({
  node,
  depth,
  selected,
  live,
  labels,
  visible,
  isOpen,
  destinations,
  onCreateFolder,
  onMoveCampaign,
  onRemoveProject,
  onToggle,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  selected: string;
  live: Map<string, "running" | "awaiting">;
  labels: Labels;
  visible: Map<string, boolean>;
  isOpen: (node: TreeNode) => boolean;
  destinations: Map<string, { path: string; label: string }[]>;
  onCreateFolder: (path: string, project: string) => Promise<void>;
  onMoveCampaign: (path: string, directory: string, project: string) => Promise<void>;
  onRemoveProject: (id: string) => Promise<void>;
  onToggle: (key: string, collapsed: boolean) => void;
  onSelect: (key: string) => void;
}) {
  const open = isOpen(node);
  const hasChildren = node.children.length > 0;
  const shown = node.children.filter((child) => visible.get(child.key));
  const kind = node.step_kind
    ? (labels.step_kind_label[node.step_kind] ?? node.kind)
    : (labels.kind_label[node.kind] ?? node.kind);
  const isCurrent = node.key === selected;
  const mark = live.get(node.key);
  const isAwaiting = mark === "awaiting";
  const isRunning = mark === "running";
  const folder = node.kind === "directory" ? (node.path ?? CAMPAIGNS_ROOT) : CAMPAIGNS_ROOT;
  const here = destinations
    .get(node.project)
    ?.filter((candidate) => candidate.path !== parentOf(node.path ?? ""))
    .filter((candidate) => candidate.path !== node.path) ?? [];

  return (
    <li>
      <div
        className={cn(
          "group relative flex items-center gap-0.5 rounded pr-0.5",
          isCurrent && "bg-accent",
          isAwaiting && !isCurrent && "bg-status-warn/10",
          isRunning && !isCurrent && "bg-live/10",
        )}
        style={{ paddingLeft: `${depth * 10}px` }}
      >
        {/* What the tree is doing, on the row that is doing it. The status bar says it
            too, but a campaign is forty rows and the answer to "where is it now" has
            to be findable without reading. */}
        {(isRunning || isAwaiting) && (
          <span
            className={cn(
              "absolute inset-y-0 left-0 w-0.5",
              isAwaiting ? "bg-status-warn" : "bg-live",
            )}
            aria-hidden
          />
        )}
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggle(node.key, open)}
            aria-expanded={open}
            aria-label={`${open ? "Recolher" : "Expandir"} ${node.label}`}
            className="flex size-4 shrink-0 items-center justify-center rounded text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <ChevronRight
              className={cn("size-3 transition-transform", open && "rotate-90")}
            />
          </button>
        ) : (
          <span className="size-4 shrink-0" />
        )}
        <button
          type="button"
          onClick={() => onSelect(node.key)}
          data-tip={node.reason ?? node.path ?? node.key}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded py-1 pl-0.5 pr-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          {/* Where the run is, in a slot that is always the same width: a mark that
              came and went would slide every label left and right mid-run. */}
          <span className="flex size-4 shrink-0 items-center justify-center">
            {isRunning && <span className="live-dot" aria-hidden />}
            {isAwaiting && (
              <span className="size-1.5 animate-pulse rounded-full bg-status-warn" aria-hidden />
            )}
          </span>
          <span className="min-w-0 flex-1 truncate">
            <span className="mr-1 text-[10px] text-muted-foreground">{kind}</span>
            <span className={cn("text-xs", isFailingText(node.status) && "text-status-fail")}>
              {node.label}
            </span>
          </span>
          {node.fail_count > 0 && (
            <span
              data-tip={`${node.fail_count} item(ns) falhando`}
              className="rounded bg-status-fail/20 px-1 text-[10px] font-medium tabular-nums text-status-fail"
            >
              {node.fail_count}
            </span>
          )}
          {node.children.length > 0 && (
            <span
              data-tip={`${node.children.length} item(ns)`}
              className="text-[10px] tabular-nums text-muted-foreground"
            >
              {node.children.length}
            </span>
          )}
          {/* The status column, on a straight right edge so the eye reads it down the
              whole tree without the labels in the way. The word is the tooltip. */}
          <StatusIcon status={node.status} labels={labels} detail={node.reason} className="ml-auto" />
        </button>
        <RowActions
          node={node}
          label={node.label}
          folder={folder}
          destinations={here}
          onCreateFolder={onCreateFolder}
          onMoveCampaign={onMoveCampaign}
          onRemoveProject={onRemoveProject}
        />
      </div>
      {hasChildren && open && (
        <ul className="tree-guide ml-2 flex flex-col pl-1">
          {shown.map((child) => (
            <TreeBranch
              key={child.key}
              node={child}
              depth={depth + 1}
              selected={selected}
              live={live}
              labels={labels}
              visible={visible}
              isOpen={isOpen}
              destinations={destinations}
              onCreateFolder={onCreateFolder}
              onMoveCampaign={onMoveCampaign}
              onRemoveProject={onRemoveProject}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * The two writes a reviewer makes to the *collection*, on the row they are about.
 *
 * Which actions a row offers is decided by its kind and by what the server will accept:
 * a folder can be created inside anything that holds campaigns, only a campaign can be
 * moved, and only a project can be closed. Every one of those rules lives on the server
 * too — this hides a button, it does not grant a permission — and the menu says what it
 * cannot do by not offering it, which is cheaper to read than a refusal.
 *
 * The create form is inline rather than a second dialog: it is one field, and a modal
 * on top of a sidebar is a lot of ceremony for a folder name.
 */
function RowActions({
  node,
  label,
  folder,
  destinations,
  onCreateFolder,
  onMoveCampaign,
  onRemoveProject,
}: {
  node: TreeNode;
  label: string;
  folder: string;
  destinations: { path: string; label: string }[];
  onCreateFolder: (path: string, project: string) => Promise<void>;
  onMoveCampaign: (path: string, directory: string, project: string) => Promise<void>;
  onRemoveProject: (id: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const canFolder = FOLDER_PARENTS.has(node.kind);
  const canMove = node.kind === "campaign" && Boolean(node.path) && destinations.length > 0;
  const canRemove = node.kind === "project";
  if (!canFolder && !canMove && !canRemove) return null;

  const create = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await onCreateFolder(`${folder}/${trimmed}`, node.project);
      setName("");
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Ações de ${label}`}
          title={`Ações de ${label}`}
          className="flex size-4 shrink-0 items-center justify-center rounded text-[10px] text-muted-foreground opacity-0 outline-none transition-opacity hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring/60 group-hover:opacity-100"
        >
          ⋯
        </button>
      </PopoverTrigger>
      <PopoverContent className="flex flex-col gap-1.5">
        {canFolder && (
          <div className="flex flex-col gap-1">
            <span className="px-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Nova pasta em {folder}
            </span>
            <div className="flex items-center gap-1">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void create();
                }}
                placeholder="ingest"
                aria-label={`Nome da pasta dentro de ${folder}`}
                autoComplete="off"
                className="h-6 text-[11px]"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-6 shrink-0 px-1.5"
                onClick={() => void create()}
                disabled={busy || !name.trim()}
              >
                <FolderPlus className="size-3" />
              </Button>
            </div>
          </div>
        )}
        {canMove && (
          <div className="flex flex-col gap-0.5">
            <span className="px-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Mover para…
            </span>
            {destinations.map((candidate) => (
              <button
                key={candidate.path}
                type="button"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  void onMoveCampaign(node.path ?? "", candidate.path, node.project).finally(
                    () => setBusy(false),
                  );
                  setOpen(false);
                }}
                className="flex items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                <MoveRight className="size-3 shrink-0" />
                <span className="truncate font-mono">{candidate.label}</span>
              </button>
            ))}
          </div>
        )}
        {canRemove && (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void onRemoveProject(node.project).finally(() => setBusy(false));
              setOpen(false);
            }}
            className="flex items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] text-status-fail outline-none hover:bg-status-fail/10 focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <Trash2 className="size-3 shrink-0" />
            Remover projeto
          </button>
        )}
      </PopoverContent>
    </Popover>
  );
}

/** The folder a content path lives in, as a move destination would name it. */
function parentOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut <= 0 ? CAMPAIGNS_ROOT : path.slice(0, cut);
}

function isFailingText(status: string): boolean {
  return status === "fail" || status === "http_5xx";
}
