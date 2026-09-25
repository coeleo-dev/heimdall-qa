/**
 * The token, and where it comes from.
 *
 * The harness refuses every `/api/*` call without a per-run token, because a JSON
 * mutation API on loopback is reachable by any page the host's browser loads. The
 * token is handed to the window **in the URL fragment**:
 *
 *     http://127.0.0.1:8731/app/#token=<secret>
 *
 * A fragment is the right channel for three reasons. It is never sent to the server,
 * so it stays out of access logs; it is not visible to a cross-origin page, which
 * cannot read another origin's `location`; and it is available before the first
 * request, so there is no race where the app fetches bootstrap before it has a token.
 *
 * The cost is that a fragment survives in the history of the window. The shell opens
 * one window with no history to speak of, and we scrub it immediately below, so the
 * window it could be read from is a few milliseconds wide and belongs to us.
 */

const STORAGE_KEY = "heimdall.token";

let cached: string | null = null;

function fromFragment(): string | null {
  const raw = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  const token = params.get("token");
  if (!token) return null;
  // Put the URL back the way the shell wrote it minus the secret, so a reload or a
  // screenshot of the address bar does not carry it.
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return token;
}

/** The token for this run, or `null` when the window was opened without one. */
export function apiToken(): string | null {
  if (cached) return cached;
  const fromHash = fromFragment();
  if (fromHash) {
    cached = fromHash;
    try {
      // sessionStorage, not localStorage: the token is per run and must not outlive
      // the window that was given it.
      window.sessionStorage.setItem(STORAGE_KEY, fromHash);
    } catch {
      // A webview with storage disabled still works; the fragment is the source.
    }
    return cached;
  }
  try {
    cached = window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    cached = null;
  }
  return cached;
}

/** For tests: forget the cached token so a fresh fragment is read. */
export function resetTokenCache(): void {
  cached = null;
}
