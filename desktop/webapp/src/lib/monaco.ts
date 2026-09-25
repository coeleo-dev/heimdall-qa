import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
// The two languages the client actually shows, and their workers' contributions. The
// bare `monaco-editor` import is a barrel that registers all ~120 basic languages
// (~3.7 MB of chunks, a hundred files in the wheel) for tokenizers nothing can reach.
import "monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution";
import "monaco-editor/esm/vs/language/json/monaco.contribution";

/**
 * Monaco, vendored.
 *
 * The default way to run Monaco is from a CDN, which would make the desktop client
 * need the network to show a response body — in a harness whose whole promise is that
 * reviewing an API works offline. So the package is a dependency, the workers come
 * from it through Vite's `?worker` imports, and nothing is fetched at runtime.
 *
 * Only two workers are wired on purpose. JSON gets its real language service, because
 * a reviewer wants to fold and validate a response; YAML is highlighted by Monaco's
 * basic-language tokenizer, which needs no worker and is enough to read a round.
 * Registering workers nothing uses would only grow the bundle.
 */

let configured = false;

const THEME = "heimdall-dark";

export function ensureMonaco(): typeof monaco {
  if (!configured) {
    configured = true;
    window.MonacoEnvironment = {
      getWorker(_workerId: string, label: string) {
        if (label === "json") return new JsonWorker();
        return new EditorWorker();
      },
    };
    monaco.editor.defineTheme(THEME, {
      base: "vs-dark",
      inherit: true,
      rules: [
        { token: "string", foreground: "9ece6a" },
        { token: "number", foreground: "ff9e64" },
        { token: "keyword", foreground: "7aa2f7" },
        { token: "delimiter", foreground: "8a8a8a" },
        { token: "type", foreground: "2ac3de" },
        { token: "comment", foreground: "6b7089", fontStyle: "italic" },
      ],
      colors: {
        // Transparent so the card behind the editor decides the background and a
        // read-only body does not sit in its own slightly-different rectangle.
        "editor.background": "#00000000",
        "editorGutter.background": "#00000000",
        "editorLineNumber.foreground": "#5a5a5a",
        "editorLineNumber.activeForeground": "#9a9a9a",
        "editor.selectionBackground": "#3a4a7a66",
        "editorIndentGuide.background1": "#2a2a2a",
        "editorIndentGuide.activeBackground1": "#3a3a3a",
        "scrollbarSlider.background": "#ffffff18",
        "scrollbarSlider.hoverBackground": "#ffffff28",
        "scrollbarSlider.activeBackground": "#ffffff38",
        "editorWidget.background": "#1c1c1c",
        "editorWidget.border": "#2e2e2e",
      },
    });
  }
  return monaco;
}

export const MONACO_THEME = THEME;

/** The language id Monaco should tokenize a value as. */
export type EditorLanguage = "json" | "yaml" | "markdown" | "plaintext";

export { monaco };
