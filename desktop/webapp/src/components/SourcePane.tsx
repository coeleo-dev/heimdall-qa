import { useCallback, useEffect, useMemo } from "react";
import { AlertTriangle, CheckCircle2, FileWarning, RotateCcw, Save, X } from "lucide-react";

import { JsonEditor } from "@/components/JsonEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useSource } from "@/hooks/useSource";

/**
 * The editor: one contract, round, campaign or suite, and what `validate` says of it.
 *
 * The findings are the reason this pane exists rather than a `$EDITOR` handoff. `validate`
 * is the gate the whole harness runs behind, and its answer is four fields — a code, the
 * place, the fix and the reason — which is exactly a panel's worth of structure and
 * exactly what a terminal makes you scroll back for. Here it sits under the file that
 * produced it.
 *
 * Three things it does not do, each on purpose:
 *
 * - **It does not save on a timer.** A save is a keystroke (`⌘S`) or a button, because
 *   every save runs the validator over the file and its includes, and an autosave would
 *   do that on every pause in typing.
 * - **It does not block a bad save.** The server keeps an invalid document and answers
 *   with the findings; refusing here would throw away the edit to be tidy.
 * - **It does not guess the language.** The kind the server sent decides it, so YAML
 *   tokenizes as YAML and a client cannot disagree with the server about what it opened.
 */
export function SourcePane({
  path,
  onClose,
  onSaved,
}: {
  path: string;
  onClose: () => void;
  /** Told after a save, so the shell can re-read the tree a save may have changed. */
  onSaved: () => void;
}) {
  const source = useSource(path);
  const document = source.document;

  const save = useCallback(async () => {
    const saved = await source.save();
    if (saved) onSaved();
  }, [source, onSaved]);

  // `⌘S`, the one shortcut every editor has already taught the reader.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "s" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      if (!source.saving && source.dirty) void save();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, source.dirty, source.saving]);

  const findings = document?.findings ?? [];
  const language = useMemo(() => languageOf(document?.kind), [document?.kind]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5">
        <FileWarning className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="truncate font-mono text-xs" title={path}>
          {path}
        </span>
        {document && <Badge variant="muted">{document.kind}</Badge>}
        {source.dirty && (
          <Badge variant="warn" title="há alterações não salvas">
            não salvo
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {document && <ValidityBadge valid={document.valid} count={findings.length} />}
          <Button
            variant="ghost"
            size="sm"
            className="h-6 gap-1"
            onClick={source.revert}
            disabled={!source.dirty}
            title="Descartar as alterações e voltar ao arquivo salvo"
          >
            <RotateCcw className="size-3" /> Reverter
          </Button>
          <Button
            variant={source.dirty ? "default" : "outline"}
            size="sm"
            className="h-6 gap-1"
            onClick={() => void save()}
            disabled={source.saving || !document?.editable}
            title="Salvar e validar (⌘S)"
          >
            <Save className="size-3" /> {source.saving ? "Salvando…" : "Salvar e validar"}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label="Fechar o editor"
            title="Fechar o editor"
          >
            <X className="size-3.5" />
          </Button>
        </div>
      </header>

      {source.error && (
        <div className="shrink-0 border-b border-status-fail/40 bg-status-fail/10 px-3 py-1.5 text-[11px]">
          <span className="font-medium text-status-fail">{source.error.message}</span>
          {source.error.hint && (
            <span className="text-muted-foreground"> — {source.error.hint}</span>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1">
        {source.loading && !document ? (
          <p className="p-4 text-xs text-muted-foreground">Lendo {path}…</p>
        ) : (
          <JsonEditor
            value={source.text}
            language={language}
            readOnly={!document?.editable}
            minLines={24}
            className="h-full rounded-none border-0"
            ariaLabel={`Editar ${path}`}
            onChange={source.setText}
          />
        )}
      </div>

      <FindingsPanel findings={findings} dirty={source.dirty} />
    </div>
  );
}

/**
 * The validity line: one badge, and the findings under it.
 *
 * `valid` and `findings` are the same fact, so the badge and the list are always drawn
 * from the same payload in the same render — there is no state in which the header says
 * "válido" over a list of problems.
 */
function ValidityBadge({ valid, count }: { valid: boolean; count: number }) {
  if (valid) {
    return (
      <Badge variant="pass" className="gap-1">
        <CheckCircle2 className="size-3" /> válido
      </Badge>
    );
  }
  return (
    <Badge variant="fail" className="gap-1">
      <AlertTriangle className="size-3" />
      {count === 1 ? "1 achado" : `${count} achados`}
    </Badge>
  );
}

function FindingsPanel({
  findings,
  dirty,
}: {
  findings: { code: string; where: string; message: string; fix: string; why: string }[];
  dirty: boolean;
}) {
  if (findings.length === 0) {
    return (
      <div className="shrink-0 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        {dirty
          ? "Salve para validar o que está no editor — a validação lê o arquivo em disco."
          : "O validate não tem nada a apontar."}
      </div>
    );
  }
  return (
    <div className="max-h-56 shrink-0 overflow-y-auto scroll-thin border-t border-border">
      <div className="px-3 py-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        validate
        {dirty && " — achados do arquivo salvo, não do editor"}
      </div>
      {findings.map((item) => (
        <div
          key={`${item.code}:${item.where}:${item.message}`}
          className="border-t border-border/60 px-3 py-2"
        >
          <div className="flex items-baseline gap-2">
            <Badge variant="fail" className="shrink-0">
              {item.code}
            </Badge>
            <span className="truncate font-mono text-[11px] text-muted-foreground" title={item.where}>
              {item.where}
            </span>
          </div>
          <p className="mt-1 whitespace-pre-wrap text-[11px]">{item.message}</p>
          <p className="mt-1 text-[11px] text-status-warn">{item.fix}</p>
          <p className="mt-0.5 text-[10px] italic text-muted-foreground">{item.why}</p>
        </div>
      ))}
    </div>
  );
}

/** The kind the server sent decides the tokenizer. `.yaml` alone would be a guess. */
function languageOf(kind: string | undefined) {
  if (kind === "contract" || kind === "round" || kind === "campaign" || kind === "suite") {
    return "yaml" as const;
  }
  return "plaintext" as const;
}
