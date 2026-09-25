import type { StreamState } from "@/types";
import { TOKEN_HEADER } from "@/lib/api";
import { apiToken } from "@/lib/token";

/**
 * The live state stream.
 *
 * `EventSource` is the obvious choice and it is the wrong one here: it cannot set a
 * request header, and the API requires its token in one. Rather than move the token
 * into the query string — where it lands in every log line and in `Referer` — the
 * stream is read with `fetch` and a `ReadableStream`, and the SSE frames are parsed
 * here. It is about thirty lines, and none of them are a secret in a URL.
 *
 * Reconnection is deliberate rather than automatic: the shell restarts its server on
 * a new port, and a client that reconnected to a dead one forever would show a frozen
 * screen while claiming to be live. So the caller is told, and can re-bootstrap.
 *
 * Two things here are about the window this runs in rather than about SSE. The decoder
 * is a `TextDecoder` driven by hand and not a `TextDecoderStream`, because the desktop
 * client draws inside whatever WebKit the machine ships and that constructor is not in
 * all of them — a missing global threw, the catch retried, and the screen sat frozen
 * while the top bar said the stream had dropped. And a reconnect announces itself
 * through `onResync`, because the frames sent while the socket was down are gone:
 * without a re-read the client would wait for the next change to notice one.
 */

const RECONNECT_DELAYS_MS = [250, 500, 1_000, 2_000, 5_000];

export interface StreamHandlers {
  onState: (state: StreamState) => void;
  /** A connection was re-established after a drop: whatever moved meanwhile is missed. */
  onResync?: () => void;
  /** Called when the stream drops and could not be re-established. */
  onLost?: (error: unknown) => void;
}

interface Frame {
  event: string;
  data: string;
}

/** Split one `\n\n`-delimited SSE block into its `event:` and `data:` lines. */
function parseFrame(block: string): Frame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // a keep-alive comment
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) data.push(line.slice("data:".length).trimStart());
  }
  if (data.length === 0) return null;
  return { event, data: data.join("\n") };
}

/** Open the stream and keep it open. Returns a function that closes it for good. */
export function openStream(handlers: StreamHandlers): () => void {
  let closed = false;
  let attempt = 0;
  /** Whether a connection has already been established once, so a new one is a redo. */
  let connected = false;
  let controller: AbortController | null = null;

  const connect = async (): Promise<void> => {
    if (closed) return;
    controller = new AbortController();
    try {
      const token = apiToken();
      const response = await fetch("/api/events", {
        signal: controller.signal,
        headers: token ? { [TOKEN_HEADER]: token } : {},
      });
      if (!response.ok || !response.body) {
        throw new Error(`stream answered ${response.status}`);
      }
      const resuming = connected;
      connected = true;
      attempt = 0;
      if (resuming) handlers.onResync?.();

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        // `stream: true` so a frame that straddles two chunks is not cut in the
        // middle of a multi-byte character.
        buffer += decoder.decode(value, { stream: true });
        // Frames are separated by a blank line. The last piece is kept: it may be a
        // frame still arriving, and dispatching half a JSON document is worse than
        // waiting for the newline.
        let cut = buffer.indexOf("\n\n");
        while (cut !== -1) {
          const block = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const frame = parseFrame(block);
          if (frame && frame.event === "state") {
            handlers.onState(JSON.parse(frame.data) as StreamState);
          }
          cut = buffer.indexOf("\n\n");
        }
      }
      if (!closed) throw new Error("stream ended");
    } catch (error) {
      if (closed || (error as Error)?.name === "AbortError") return;
      const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
      attempt += 1;
      if (attempt > RECONNECT_DELAYS_MS.length) {
        handlers.onLost?.(error);
        return;
      }
      window.setTimeout(() => void connect(), delay);
    }
  };

  void connect();

  return () => {
    closed = true;
    controller?.abort();
  };
}
