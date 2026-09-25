import { useEffect, useRef } from "react";
import type { editor as MonacoEditor } from "monaco-editor";

import { ensureMonaco, MONACO_THEME, type EditorLanguage } from "@/lib/monaco";
import { cn } from "@/lib/utils";

/**
 * A Monaco editor sized to its container, used read-only throughout the review pane
 * and (in phase 5) for editing a contract.
 *
 * Two decisions worth naming:
 *
 * - **It is a real editor even read-only.** `readOnly` rather than `plaintext`: the
 *   reviewer keeps folding, bracket matching and the JSON outline, which is most of
 *   why Monaco is here at all.
 * - **Height is the container's, never Monaco's.** `automaticLayout` makes Monaco
 *   observe its own box, so a body that is 400 lines does not push the page — the
 *   pane scrolls and the verdict bar below stays where it is.
 */
export function JsonEditor({
  value,
  language = "json",
  readOnly = true,
  minLines = 6,
  maxHeight,
  className,
  ariaLabel,
  onChange,
}: {
  value: string;
  language?: EditorLanguage;
  readOnly?: boolean;
  /** A floor so an empty body is still a place, not a hyphen. */
  minLines?: number;
  maxHeight?: number;
  className?: string;
  ariaLabel?: string;
  onChange?: (next: string) => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const instance = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  const onChangeRef = useRef(onChange);

  // Kept current in an effect, not during render: the subscription below is created
  // once and would otherwise capture the first `onChange` forever.
  useEffect(() => {
    onChangeRef.current = onChange;
  });

  useEffect(() => {
    if (!host.current) return;
    const monaco = ensureMonaco();
    const created = monaco.editor.create(host.current, {
      value,
      language,
      theme: MONACO_THEME,
      readOnly,
      domReadOnly: readOnly,
      automaticLayout: true,
      minimap: { enabled: false },
      lineNumbers: "on",
      lineNumbersMinChars: 3,
      folding: true,
      glyphMargin: false,
      scrollBeyondLastLine: false,
      renderLineHighlight: readOnly ? "none" : "line",
      contextmenu: false,
      // The pane already has a scroller; letting Monaco scroll horizontally inside a
      // bounded height keeps the long response body readable without a page scroll.
      wordWrap: "on",
      wrappingIndent: "same",
      fontSize: 12,
      fontFamily: "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace",
      scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10, useShadows: false },
      padding: { top: 8, bottom: 8 },
      ariaLabel,
      ...(maxHeight ? { maxHeight } : {}),
    });
    instance.current = created;
    const subscription = created.onDidChangeModelContent(() => {
      onChangeRef.current?.(created.getValue());
    });
    // A minimum line count is expressed by the container's height; Monaco sets its own
    // from the model, so the floor is applied as a style on the host instead.
    return () => {
      subscription.dispose();
      created.dispose();
      instance.current = null;
    };
    // Recreating the editor on every keystroke would throw the cursor away; the value
    // is pushed in below instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language, readOnly, ariaLabel, maxHeight]);

  useEffect(() => {
    const created = instance.current;
    if (!created) return;
    if (created.getValue() === value) return;
    // `setValue` and not a model swap: it keeps the undo stack and the scroll position
    // sane, and for a read-only body the only cost is a re-tokenize.
    created.setValue(value);
  }, [value]);

  return (
    <div
      ref={host}
      className={cn(
        "overflow-hidden rounded-md border border-border/70 bg-black/20",
        className,
      )}
      style={{ minHeight: `${minLines * 1.5 + 1}rem` }}
    />
  );
}
