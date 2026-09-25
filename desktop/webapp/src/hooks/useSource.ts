import { useCallback, useEffect, useRef, useState } from "react";

import { ApiRefusal, fetchSource, saveSource } from "@/lib/api";
import type { HarnessError, SourceDocument } from "@/types";

export interface OpenSource {
  /** The file as the server last gave it to us, findings included. */
  document: SourceDocument | null;
  /** The text in the editor, which may be ahead of `document.text`. */
  text: string;
  dirty: boolean;
  loading: boolean;
  saving: boolean;
  error: HarnessError | null;
  setText: (next: string) => void;
  save: () => Promise<SourceDocument | null>;
  revert: () => void;
  reload: () => Promise<void>;
}

/**
 * One open file, and whether the editor is ahead of it.
 *
 * The dirty flag is a comparison and not a second piece of state: `text !== document.text`
 * is the only definition that cannot drift, and a boolean kept alongside the text is a
 * boolean that will be wrong after an undo. The text is seeded from the server's own
 * bytes so that "unchanged" means exactly that.
 *
 * `save` resolves with the document so the caller can report the findings, and with
 * `null` when the save was refused — a path that left the content root, or a file the
 * client may not write. Both are refusals the reader has to see, and they are not the
 * same kind of thing as "your YAML has a typo", which is a 200 with findings.
 */
export function useSource(path: string | null): OpenSource {
  const [document, setDocument] = useState<SourceDocument | null>(null);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<HarnessError | null>(null);
  // Guards a slow load from landing after the reviewer has moved to another file.
  const wanted = useRef<string | null>(null);

  const load = useCallback(async (target: string) => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchSource(target);
      if (wanted.current !== target) return;
      setDocument(next);
      setText(next.text);
    } catch (failure) {
      if (wanted.current !== target) return;
      setError(asHarnessError(failure));
      setDocument(null);
    } finally {
      if (wanted.current === target) setLoading(false);
    }
  }, []);

  useEffect(() => {
    wanted.current = path;
    if (!path) {
      setDocument(null);
      setText("");
      setError(null);
      return;
    }
    void load(path);
  }, [path, load]);

  const save = useCallback(async () => {
    if (!path) return null;
    setSaving(true);
    setError(null);
    try {
      const next = await saveSource(path, text);
      setDocument(next);
      // The text is *not* reset to what was sent: the server normalises nothing, so a
      // difference here can only be a keystroke that landed during the round trip, and
      // discarding it would lose work the reviewer can see in front of them.
      return next;
    } catch (failure) {
      setError(asHarnessError(failure));
      return null;
    } finally {
      setSaving(false);
    }
  }, [path, text]);

  const revert = useCallback(() => {
    if (document) setText(document.text);
  }, [document]);

  const reload = useCallback(async () => {
    if (path) await load(path);
  }, [path, load]);

  return {
    document,
    text,
    dirty: document !== null && text !== document.text,
    loading,
    saving,
    error,
    setText,
    save,
    revert,
    reload,
  };
}

function asHarnessError(error: unknown): HarnessError {
  if (error instanceof ApiRefusal) return error.payload.error;
  const message = error instanceof Error ? error.message : String(error);
  return {
    code: "TRANSPORT",
    message,
    hint: "the harness's own process may have stopped",
    details: [],
    exit_code: 1,
  };
}
