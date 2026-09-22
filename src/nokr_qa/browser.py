"""Chromium driver for the `ui` step (emenda 11, fases A5/A6).

The step opens one dashboard screen, waits for it to finish loading, reads the
surfaces E1 anchored, and returns a plain data structure — no Playwright object
escapes this module, so `ui_step.py` and the tests never depend on the SDK.

Two design points worth knowing before reading the code:

* **One browser context per run.** The dashboard keeps the JWT in memory only
  (`AuthService._session`), so a fresh `page.goto` to another route would need a
  second login. The context is reused across steps and the `X-Trace-Id` header is
  swapped per step instead.
* **In-app navigation.** Moving between screens is `history.pushState` +
  `popstate` (with a sidebar click as fallback) so the Angular router swaps the
  view without a document load, which is what keeps the session alive.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from time import monotonic
from typing import Any
from typing import Protocol
from urllib.parse import parse_qs
from urllib.parse import urlparse

import httpx

from nokr_qa.config import HarnessConfig
from nokr_qa.errors import HarnessError

_POLL_MS = 50
# How long a screen may take to paint its first surface before the step gives up
# on waiting for a value (see `_surface_grace_exceeded`).
_SURFACE_GRACE_MS = 3000
# `to_match_aria_snapshot` retries until its timeout expires, so a structural
# mismatch must use a short budget: the full `ui.page_ms` would burn the whole
# page budget re-checking a diff it already has.
_STRUCTURE_MATCH_MS = 2000
# A.19 asks for WCAG A/AA on the screen state, not the whole ruleset.
_A11Y_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa")
_READY_SCRIPT = "() => window.__NOKR_QA_BOOT__ === true"
# `axe.run` scoped to the read region, projected down to what the pack and the
# E6 panel need: the offending node's selector and HTML, and the check messages.
_AXE_RUN_SCRIPT = """
(args) => axe.run(args.context, args.options).then((results) =>
  (results.violations || []).map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    help: violation.help,
    helpUrl: violation.helpUrl,
    tags: violation.tags,
    nodes: (violation.nodes || []).map((node) => ({
      target: node.target,
      html: node.html,
      messages: [...(node.all || []), ...(node.any || []), ...(node.none || [])].map(
        (check) => check.message
      ),
    })),
  }))
)
"""
_SURFACES_SCRIPT = """
() => Array.from(document.querySelectorAll('[data-surface]')).map((node) => ({
  surface: node.getAttribute('data-surface'),
  value: node.getAttribute('data-value'),
}))
"""
_URL_SCRIPT = """
(path) => {
  const wanted = new URL(path, window.location.origin);
  const samePath = window.location.pathname === wanted.pathname;
  const sameSearch = window.location.search === wanted.search;
  return samePath && sameSearch;
}
"""
# `main` is the dashboard shell; the auth layout has no <main>, so the region
# falls back through the wrappers instead of silently snapshotting the body.
_DEFAULT_REGION_SELECTORS = (
    "main.main-content",
    "main",
    "[role=main]",
    "div.auth-layout",
    "body",
)

_MISSING_BROWSER_HINT = (
    "install the browser once: python -m playwright install chromium"
)


@dataclass(frozen=True)
class DashboardProbe:
    """Result of the dashboard preflight (A.19: down means FALHOU, never SKIP)."""

    reachable: bool
    url: str
    status: int | None = None
    elapsed_ms: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class PageRead:
    """Everything one `ui` step observed, ready to be written as an artifact."""

    path: str
    url: str
    region: str
    values: dict[str, str | None]
    aria: str
    console: list[dict[str, Any]] = field(default_factory=list)
    network: list[dict[str, Any]] = field(default_factory=list)
    primary: dict[str, Any] | None = None
    screenshot: bytes | None = None
    elapsed_ms: float = 0.0
    trace_id: str = ""
    # ARIA comparison and a11y findings are *collected* here and judged by the
    # packs, so `ui.structure` / `ui.a11y` stay testable without a browser.
    structure: dict[str, Any] | None = None
    a11y: dict[str, Any] | None = None


class UiDriver(Protocol):
    """The slice of `UiSession` the step needs, so tests can pass a fake."""

    def set_trace_id(self, trace_id: str) -> None: ...

    def read_screen(
        self,
        *,
        path: str,
        wait_for: str | None,
        region: str,
        timeout_ms: int,
        screenshot: bool,
        trace_id: str,
        baseline: str | None = None,
        a11y: bool = False,
    ) -> PageRead: ...

    def close(self) -> None: ...


def preflight_dashboard(config: HarnessConfig, client: httpx.Client) -> DashboardProbe:
    """Checks the dashboard is up before a round starts driving it.

    Returns a probe instead of raising: the caller owns the verdict, which per
    A.19 is a hard `fail` with cause `instrument` — the harness is what broke,
    not the product.
    """
    url = config.nokr_dashboard
    started = monotonic()
    try:
        response = client.get(url, follow_redirects=True)
    except httpx.HTTPError as err:
        return DashboardProbe(
            reachable=False,
            url=url,
            elapsed_ms=(monotonic() - started) * 1000.0,
            reason=f"dashboard unreachable at {url}: {err.__class__.__name__}",
        )
    elapsed_ms = (monotonic() - started) * 1000.0
    if response.status_code >= 500:
        return DashboardProbe(
            reachable=False,
            url=url,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            reason=f"dashboard answered HTTP {response.status_code} at {url}",
        )
    return DashboardProbe(
        reachable=True,
        url=url,
        status=response.status_code,
        elapsed_ms=elapsed_ms,
    )


def require_dashboard(config: HarnessConfig, client: httpx.Client) -> DashboardProbe:
    """Preflight that raises when the dashboard is down (fail hard, not SKIP)."""
    probe = preflight_dashboard(config, client)
    if not probe.reachable:
        raise HarnessError(
            code="DASHBOARD_DOWN",
            message=probe.reason,
            hint=(
                "start the dashboard (nx run nokr-b2b-dashboard:serve) and keep "
                "nokr_dashboard pointing at localhost:4200"
            ),
        )
    return probe


class UiSession:
    """A Chromium context that survives the whole run and re-logs in once."""

    def __init__(
        self,
        *,
        dashboard_url: str,
        credentials: dict[str, str] | None = None,
        environment: str = "sandbox",
        axe_script: str | None = None,
    ) -> None:
        self._dashboard_url = dashboard_url.rstrip("/")
        self._credentials = credentials or {}
        self._environment = environment
        # Injectable so a deployment can pin its own axe build (`Axe.from_file`).
        self._axe_script = axe_script
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        # (entry, response) pairs, appended in the response handler and never
        # cleared: the trace id, not a buffer reset, is what separates two steps.
        self._traffic: list[tuple[dict[str, Any], Any]] = []
        self._console: list[dict[str, Any]] = []
        self._trace_id = ""
        self._logged_in = False
        self._surface_seen_at: float | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Boots Chromium. Import is lazy so the harness runs without Playwright."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as err:  # pragma: no cover - depends on the environment
            raise HarnessError(
                code="PLAYWRIGHT_MISSING",
                message="the ui step needs the playwright package",
                hint=_MISSING_BROWSER_HINT,
            ) from err

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                base_url=self._dashboard_url,
                viewport={"width": 1440, "height": 900},
            )
        except Exception as err:  # pragma: no cover - depends on the environment
            raise HarnessError(
                code="PLAYWRIGHT_MISSING",
                message=f"chromium could not start: {err}",
                hint=_MISSING_BROWSER_HINT,
            ) from err

        # Runs in the browser context only (never during SSR), so its presence is
        # proof that a read is happening after hydration and not on prerender HTML.
        self._context.add_init_script("window.__NOKR_QA_BOOT__ = true;")
        self._context.add_init_script(
            "window.localStorage.setItem('nokr_selected_environment', "
            f"{self._environment!r});"
        )
        self._page = self._context.new_page()
        self._page.on("response", self._on_response)
        self._page.on("console", self._on_console)
        self._page.on("pageerror", self._on_page_error)

    def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:  # noqa: BLE001 - shutdown must never mask the verdict
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass

    # ── recording ────────────────────────────────────────────────────────────

    def _on_response(self, response: Any) -> None:
        """Records API traffic, including the body while it is still readable.

        Playwright keeps a body only until the page navigates away ("Response body
        is not available for a response that was navigated away from"), and the
        primary response is found *after* the screen settles. So the body is read
        here, at the only moment it is guaranteed to exist. Asset traffic from the
        dev-server is skipped: it is noise, and it has no trace id to correlate.
        """
        try:
            url = response.url
            if not _is_api_path(urlparse(url).path):
                return
            request = response.request
            headers = response.headers
            entry = {
                "method": request.method,
                "url": url,
                "path": urlparse(url).path,
                "status": response.status,
                "trace_id": _header(headers, "x-trace-id"),
                "content_type": _header(headers, "content-type"),
                "body": _safe_body(response),
            }
        except Exception:  # noqa: BLE001 - a broken event must not kill the step
            return
        self._traffic.append((entry, response))

    def _on_console(self, message: Any) -> None:
        try:
            location = message.location
            entry = {
                "type": message.type,
                "text": message.text,
                "location": {
                    "url": location.get("url") if isinstance(location, dict) else "",
                    "line": location.get("lineNumber") if isinstance(location, dict) else None,
                },
            }
        except Exception:  # noqa: BLE001
            return
        self._console.append(entry)

    def _on_page_error(self, error: Any) -> None:
        self._console.append(
            {"type": "error", "text": str(error), "source": "pageerror", "location": {}}
        )

    # ── per-step driving ─────────────────────────────────────────────────────

    def set_trace_id(self, trace_id: str) -> None:
        """Swaps the trace header so every call this step makes carries its own id.

        The id is recorded even before the context exists, because it is also the
        key that attributes responses to this step.
        """
        self._trace_id = trace_id
        if self._context is None:
            return
        self._context.set_extra_http_headers({"X-Trace-Id": trace_id})

    def ensure_logged_in(self) -> None:
        """Logs in through the real form; `storageState` cannot work (in-memory JWT)."""
        if self._logged_in:
            return
        email = self._credentials.get("email", "")
        password = self._credentials.get("password", "")
        if not email or not password:
            raise HarnessError(
                code="SECRET_MISSING",
                message="the ui step needs the dashboard credentials",
                hint=(
                    "add email/password to secrets.local.yaml, or run the register "
                    "round first (they persist in runs/shared-captures.json)"
                ),
            )
        timeout = 15000
        self._goto(f"{self._dashboard_url}/auth/login", timeout)
        self._page.wait_for_selector("#login-email", timeout=timeout)
        self._page.fill("#login-email", email)
        self._page.fill("#login-password", password)
        # Enter submits the enclosing form, so the step does not depend on the
        # `<nokr-button>` forwarding `type=submit` to its native button.
        self._page.press("#login-password", "Enter")
        self._page.wait_for_function(_READY_SCRIPT, timeout=timeout)
        self._wait_for_path("/overview", timeout)
        self._logged_in = True

    def read_screen(
        self,
        *,
        path: str,
        wait_for: str | None,
        region: str,
        timeout_ms: int,
        screenshot: bool,
        trace_id: str,
        baseline: str | None = None,
        a11y: bool = False,
    ) -> PageRead:
        # `/auth/login` is an auth assertion, not a screen to read values from:
        # what matters is where the app lands once the guard lets it through.
        target = "/overview" if path == "/auth/login" else path
        self.ensure_logged_in()
        return self._read(
            target, wait_for, region, timeout_ms, screenshot, trace_id, baseline, a11y
        )

    def _read(
        self,
        path: str,
        wait_for: str | None,
        region: str,
        timeout_ms: int,
        screenshot: bool,
        trace_id: str,
        baseline: str | None,
        a11y: bool,
    ) -> PageRead:
        started = monotonic()
        # Console is reset per step because it carries no trace id: entries from a
        # previous step would otherwise be attributed to this one. The API traffic
        # is *not* reset — see `_primary`.
        self._console.clear()
        self._navigate(path, timeout_ms)
        self._wait_for_ready(path, wait_for, timeout_ms)
        selector = self._resolve_region(region)
        values = self._read_surfaces()
        aria = self._aria_snapshot(selector)
        structure = self._structure(selector, baseline, timeout_ms)
        a11y_report = self._a11y(selector) if a11y else {"enabled": False, "violations": []}
        primary = self._primary(wait_for)
        shot = self._page.screenshot(full_page=False) if screenshot else None
        return PageRead(
            path=path,
            url=self._page.url,
            region=selector,
            values=values,
            aria=aria,
            console=list(self._console),
            network=self._traced_traffic(trace_id),
            primary=primary,
            screenshot=shot,
            elapsed_ms=(monotonic() - started) * 1000.0,
            trace_id=trace_id,
            structure=structure,
            a11y=a11y_report,
        )

    def _traced_traffic(self, trace_id: str) -> list[dict[str, Any]]:
        """Only the API calls this step made, matched by the trace id it sent."""
        return [
            dict(entry)
            for entry, _ in self._traffic
            if entry["trace_id"] == trace_id
        ]

    # ── navigation ───────────────────────────────────────────────────────────

    def _navigate(self, path: str, timeout_ms: int) -> None:
        if self._url_matches(path):
            return
        self._push_state(path)
        if self._wait_for_path(path, min(timeout_ms, 2000)):
            return
        if self._click_link(path):
            self._wait_for_path(path, timeout_ms)
            return
        raise HarnessError(
            code="UI_NAVIGATION_FAILED",
            message=f"could not navigate the dashboard to {path}",
            hint=(
                "declare the route exactly as app.routes.ts spells it; in-app "
                "navigation uses history.pushState and falls back to a sidebar click"
            ),
        )

    def _goto(self, url: str, timeout_ms: int) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def _push_state(self, path: str) -> None:
        self._page.evaluate(
            "(path) => history.pushState(null, '', path)",
            path,
        )
        self._page.evaluate(
            "() => window.dispatchEvent(new PopStateEvent('popstate', {state: null}))"
        )

    def _click_link(self, path: str) -> bool:
        parsed = urlparse(path)
        link = self._page.locator(f'a[href="{parsed.path}"]').first
        if link.count() == 0:
            return False
        link.click()
        tab = parse_qs(parsed.query).get("tab", [None])[0]
        if tab:
            button = self._page.locator(f"#tab-{tab}").first
            if button.count() > 0:
                button.click()
        return True

    def _url_matches(self, path: str) -> bool:
        return bool(self._page.evaluate(_URL_SCRIPT, path))

    def _wait_for_path(self, path: str, timeout_ms: int) -> bool:
        deadline = monotonic() + timeout_ms / 1000.0
        while monotonic() < deadline:
            if self._url_matches(path):
                return True
            self._page.wait_for_timeout(_POLL_MS)
        return self._url_matches(path)

    # ── readiness ────────────────────────────────────────────────────────────

    def _wait_for_ready(self, path: str, wait_for: str | None, timeout_ms: int) -> None:
        deadline = monotonic() + timeout_ms / 1000.0
        while True:
            reason = self._blocking_reason(path, wait_for)
            if reason is None:
                return
            if monotonic() >= deadline:
                raise HarnessError(
                    code="UI_PAGE_TIMEOUT",
                    message=f"the screen {path} did not finish loading: {reason}",
                    hint=(
                        "raise ui.page_ms, or declare wait_for with the endpoint "
                        "that actually feeds the screen (A.19)"
                    ),
                )
            self._page.wait_for_timeout(_POLL_MS)

    def _blocking_reason(self, path: str, wait_for: str | None) -> str | None:
        if self._url_matches(path) is False:
            return "the router did not reach the declared path"
        if not self._page.evaluate(_READY_SCRIPT):
            return "the page is not running in a hydrated browser context"
        if wait_for and self._primary(wait_for) is None:
            return f"no response traced with this step arrived for {wait_for}"
        if self._surface_grace_exceeded() and not self._read_surfaces():
            return "the screen rendered surfaces but none carries a value"
        return None

    def _surface_grace_exceeded(self) -> bool:
        """True once the screen has had a fair chance to paint its surfaces.

        A screen may legitimately have no `[data-surface]` at all, so an absent
        element is not a failure — but the moment one appears, it must carry a
        value, otherwise `ui-values.json` would be written empty and a later
        `ui.value` pack (E4) would go green on nothing.
        """
        if self._has_surface_element():
            self._surface_seen_at = None
            return True
        if self._surface_seen_at is None:
            self._surface_seen_at = monotonic()
        return (monotonic() - self._surface_seen_at) * 1000.0 >= _SURFACE_GRACE_MS

    def _has_surface_element(self) -> bool:
        return bool(self._page.evaluate("() => document.querySelector('[data-surface]') !== null"))

    # ── reading ──────────────────────────────────────────────────────────────

    def _read_surfaces(self) -> dict[str, str | None]:
        raw = self._page.evaluate(_SURFACES_SCRIPT)
        values: dict[str, str | None] = {}
        for item in raw or []:
            name = item.get("surface")
            value = item.get("value")
            if not name or name in values:
                continue
            if value is None or str(value).strip() == "":
                continue
            values[name] = str(value)
        return values

    def _resolve_region(self, region: str) -> str:
        candidates = (
            _DEFAULT_REGION_SELECTORS if region.strip() in {"", "main"} else (region,)
        )
        for selector in candidates:
            if self._page.locator(selector).count() > 0:
                return selector
        raise HarnessError(
            code="UI_REGION_MISSING",
            message=f"no element matched the region {region!r}",
            hint="declare region with a selector that exists on the screen",
        )

    def _aria_snapshot(self, selector: str) -> str:
        try:
            snapshot = self._page.locator(selector).first.aria_snapshot()
        except Exception as err:  # noqa: BLE001 - reported as an instrument failure
            raise HarnessError(
                code="UI_ARIA_UNAVAILABLE",
                message=f"ARIA snapshot failed for {selector}: {err}",
                hint="the step needs the ARIA tree; check the region selector",
            ) from err
        return snapshot

    # ── structural packs (E3, A.19) ───────────────────────────────────────────
    #
    # Both helpers *collect*; the verdict belongs to `packs.run_ui`. That split is
    # what keeps `ui.structure` and `ui.a11y` hermetic: they can be driven from a
    # fake driver without Chromium or axe.

    def _structure(
        self, selector: str, baseline: str | None, timeout_ms: int
    ) -> dict[str, Any]:
        """Compares the region's ARIA tree against the committed template.

        A mismatch is a *product* finding, so it is returned as data and never
        raised: raising would turn a real regression into an instrument failure
        and hide the diff the pack exists to report.
        """
        if baseline is None:
            return {"enabled": False, "matched": None, "error": None}
        try:
            from playwright.sync_api import expect
        except ImportError as err:  # pragma: no cover - depends on the environment
            raise HarnessError(
                code="PLAYWRIGHT_MISSING",
                message="the ui step needs the playwright package",
                hint=_MISSING_BROWSER_HINT,
            ) from err
        budget = min(_STRUCTURE_MATCH_MS, max(1, timeout_ms))
        try:
            expect(self._page.locator(selector).first).to_match_aria_snapshot(
                baseline, timeout=budget
            )
        except AssertionError as err:
            return {"enabled": True, "matched": False, "error": _condense(str(err))}
        return {"enabled": True, "matched": True, "error": None}

    def _a11y(self, selector: str) -> dict[str, Any]:
        """Runs axe-core scoped to the read region and returns the violations."""
        self._inject_axe()
        try:
            raw = self._page.evaluate(
                _AXE_RUN_SCRIPT,
                {
                    "context": selector,
                    "options": {
                        "resultTypes": ["violations"],
                        "runOnly": {"type": "tag", "values": list(_A11Y_TAGS)},
                    },
                },
            )
        except Exception as err:  # noqa: BLE001 - reported as an instrument failure
            raise HarnessError(
                code="A11Y_RUN_FAILED",
                message=f"axe-core could not run against {selector}: {err}",
                hint="check that the region selector still exists on the screen",
            ) from err
        violations = [item for item in (raw or []) if isinstance(item, dict)]
        return {
            "enabled": True,
            "violations": violations,
            "count": len(violations),
        }

    def _inject_axe(self) -> None:
        """Injects the vendored axe build into the current document.

        Not cached across steps on purpose: a full page load (the login `goto`)
        replaces the document and drops the `axe` global with it, so a session
        level "already injected" flag would silently run the scan against an
        undefined global. In-app navigation uses `pushState` and would survive,
        but re-injecting is idempotent and one `evaluate` is noise next to the
        scan itself.
        """
        script = self._axe_script or _default_axe_script()
        try:
            self._page.evaluate(script)
        except Exception as err:  # noqa: BLE001 - reported as an instrument failure
            raise HarnessError(
                code="A11Y_SCRIPT_MISSING",
                message=f"axe-core script could not be injected: {err}",
                hint="reinstall the package: pip install 'axe-playwright-python>=0.1.8'",
            ) from err

    def _primary(self, wait_for: str | None) -> dict[str, Any] | None:
        """The response that proves the screen loaded — this step's own traffic.

        Matching on the trace id is what makes the correlation airtight: step N
        can never claim a response that step M produced, which is exactly how a
        step would otherwise pass on a screen it never loaded.
        """
        if wait_for:
            for entry, _ in reversed(self._traffic):
                if entry["trace_id"] != self._trace_id:
                    continue
                if wait_for in entry["url"] and _is_success(entry["status"]):
                    return self._primary_payload(entry)
            return None
        for entry, _ in reversed(self._traffic):
            if entry["trace_id"] != self._trace_id:
                continue
            if entry["path"].startswith("/platform/") and _is_success(entry["status"]):
                return self._primary_payload(entry)
        return None

    def _primary_payload(self, entry: dict[str, Any]) -> dict[str, Any]:
        payload = dict(entry)
        for candidate, response in self._traffic:
            if candidate is entry:
                try:
                    payload["headers"] = dict(response.headers)
                except Exception:  # noqa: BLE001 - headers are a nice-to-have
                    payload["headers"] = {}
                break
        return payload


def _header(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _is_api_path(path: str) -> bool:
    """Backend traffic worth correlating; the dev-server assets are noise."""
    return path.startswith(("/api/", "/platform/", "/auth/", "/admin/", "/webhooks/"))


def _default_axe_script() -> str:
    """The axe-core build shipped inside the wheel — never a CDN (§5.4)."""
    try:
        from axe_playwright_python.base import AXE_SCRIPT
    except ImportError as err:
        raise HarnessError(
            code="A11Y_SCRIPT_MISSING",
            message="the ui.a11y pack needs the axe-playwright-python package",
            hint="pip install 'axe-playwright-python>=0.1.8' (or inject a script)",
        ) from err
    return AXE_SCRIPT


def _condense(text: str, *, limit: int = 4000) -> str:
    """Keeps the Playwright snapshot diff legible in `ui.json`."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… (diff truncated)"


def _is_success(status: Any) -> bool:
    return isinstance(status, int) and 200 <= status < 400


def _safe_body(response: Any) -> Any:
    """Never let a discarded body break the step (redirects, 204, aborted streams)."""
    try:
        return response.text()
    except Exception:  # noqa: BLE001
        return None
