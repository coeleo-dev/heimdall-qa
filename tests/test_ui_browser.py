"""Unit tests for `browser.py` internals — no Chromium involved.

Everything here runs against `FakePage`, which exposes only the Playwright
surface `UiSession` touches. The real browser is exercised by the `slow` test.
"""

from pathlib import Path
from time import monotonic
from typing import Any

import httpx
import pytest

from nokr_qa.browser import UiSession
from nokr_qa.browser import preflight_dashboard
from nokr_qa.config import HarnessConfig
from nokr_qa.errors import HarnessError
from support_ui import FakePage
from support_ui import dashboard_down
from support_ui import ui_client

SURFACES_SCRIPT_MARKER = "[data-surface]"


def _session_with(page: FakePage) -> UiSession:
    session = UiSession(dashboard_url="http://localhost:4200")
    session._page = page
    return session


def _evaluate(values_by_script):
    def evaluate(script: str, arg=None):
        for needle, value in values_by_script.items():
            if needle in script:
                return value
        raise AssertionError(f"unexpected script: {script}")

    return evaluate


# ── preflight ────────────────────────────────────────────────────────────────


def test_preflight_reports_reachable_dashboard():
    config = HarnessConfig()
    probe = preflight_dashboard(config, ui_client(lambda request: httpx.Response(200, text="ok")))
    assert probe.reachable is True
    assert probe.status == 200


def test_preflight_reports_refused_connection():
    config = HarnessConfig()
    probe = preflight_dashboard(config, ui_client(dashboard_down))
    assert probe.reachable is False
    assert "unreachable" in probe.reason


def test_preflight_reports_a_5xx_as_down():
    config = HarnessConfig()
    probe = preflight_dashboard(
        config, ui_client(lambda request: httpx.Response(503, text="down"))
    )
    assert probe.reachable is False
    assert "503" in probe.reason


# ── region resolution ────────────────────────────────────────────────────────


def test_region_prefers_the_dashboard_main():
    session = _session_with(FakePage(selectors={"main.main-content": 1, "main": 1}))
    assert session._resolve_region("main") == "main.main-content"


def test_region_falls_back_to_the_auth_wrapper():
    # The auth layout has no <main>; freezing "main" would fail the login step.
    session = _session_with(FakePage(selectors={"div.auth-layout": 1, "body": 1}))
    assert session._resolve_region("main") == "div.auth-layout"


def test_region_is_used_verbatim_when_declared():
    session = _session_with(FakePage(selectors={"main.main-content": 1, "form.login-form": 1}))
    assert session._resolve_region("form.login-form") == "form.login-form"


def test_region_missing_raises_a_clear_instrument_error():
    session = _session_with(FakePage(selectors={}))
    # "body" is always present on a real page; without even that, the harness broke.
    with pytest.raises(HarnessError) as err:
        session._resolve_region("main")
    assert err.value.code == "UI_REGION_MISSING"


# ── surface reading ──────────────────────────────────────────────────────────


def test_surfaces_skip_empty_values_and_keep_the_first_duplicate():
    page = FakePage(
        evaluate=_evaluate(
            {
                SURFACES_SCRIPT_MARKER: [
                    {"surface": "customers.content.0.balance", "value": "10.00"},
                    {"surface": "customers.content.0.balance", "value": "99.99"},
                    {"surface": "customers.summary.total_accounts", "value": ""},
                    {"surface": "customers.content.1.balance", "value": "  "},
                    {"surface": "overview.metrics.gross_volume", "value": "1234.5"},
                    {"surface": None, "value": "1"},
                ]
            }
        )
    )
    session = _session_with(page)
    assert session._read_surfaces() == {
        "customers.content.0.balance": "10.00",
        "overview.metrics.gross_volume": "1234.5",
    }


# ── readiness ────────────────────────────────────────────────────────────────


def test_missing_data_value_blocks_readiness():
    page = FakePage(
        evaluate=_evaluate(
            {
                "window.location.pathname": True,
                "__NOKR_QA_BOOT__": True,
                "document.querySelector('[data-surface]')": True,
                SURFACES_SCRIPT_MARKER: [],
            }
        )
    )
    session = _session_with(page)
    assert session._blocking_reason("/overview", None) == (
        "the screen rendered surfaces but none carries a value"
    )


def test_prerender_html_alone_is_not_ready():
    # `__NOKR_QA_BOOT__` only exists in the browser context, so its absence is
    # exactly the "we are looking at server HTML" case A.19 warns about.
    page = FakePage(
        evaluate=_evaluate(
            {
                "window.location.pathname": True,
                "__NOKR_QA_BOOT__": False,
                "document.querySelector('[data-surface]')": False,
            }
        )
    )
    session = _session_with(page)
    assert session._blocking_reason("/overview", None) == (
        "the page is not running in a hydrated browser context"
    )


def test_readiness_needs_the_wait_for_response():
    page = FakePage(
        evaluate=_evaluate(
            {
                "window.location.pathname": True,
                "__NOKR_QA_BOOT__": True,
                "document.querySelector('[data-surface]')": False,
            }
        )
    )
    session = _session_with(page)
    reason = session._blocking_reason("/overview", "/platform/dashboard/metrics")
    assert reason == "no response traced with this step arrived for /platform/dashboard/metrics"


def test_page_timeout_raises_after_the_deadline():
    page = FakePage(
        evaluate=_evaluate(
            {
                "window.location.pathname": True,
                "__NOKR_QA_BOOT__": True,
                "document.querySelector('[data-surface]')": False,
            }
        )
    )
    session = _session_with(page)
    with pytest.raises(HarnessError) as err:
        session._wait_for_ready("/overview", "/platform/dashboard/metrics", timeout_ms=1)
    assert err.value.code == "UI_PAGE_TIMEOUT"
    assert "no response traced" in err.value.message


def test_a_screen_without_surfaces_is_not_blocked():
    # A settings form has no `[data-surface]`: once the grace window passes, the
    # step proceeds instead of hanging until `ui.page_ms`.
    page = FakePage(
        evaluate=_evaluate(
            {
                "window.location.pathname": True,
                "__NOKR_QA_BOOT__": True,
                "document.querySelector('[data-surface]')": False,
            }
        )
    )
    session = _session_with(page)
    assert session._surface_grace_exceeded() is False
    session._surface_seen_at = monotonic() - 10
    assert session._surface_grace_exceeded() is True


def test_surfaces_present_but_valueless_block_readiness():
    page = FakePage(
        evaluate=_evaluate(
            {
                "window.location.pathname": True,
                "__NOKR_QA_BOOT__": True,
                "document.querySelector('[data-surface]')": True,
                SURFACES_SCRIPT_MARKER: [],
            }
        )
    )
    session = _session_with(page)
    assert session._blocking_reason("/overview", None) == (
        "the screen rendered surfaces but none carries a value"
    )


# ── primary response selection ───────────────────────────────────────────────
#
# The invariant under test: a step may only claim a response it produced itself.
# Everything here is matched by the trace id the session sent for that step.


def _with_traffic(session: UiSession, entries: list[dict], responses: list | None = None):
    session._traffic = list(zip(entries, responses or [None] * len(entries)))
    return session


def _entry(
    path: str,
    *,
    url: str | None = None,
    status: int = 200,
    trace_id: str = "nokrqa-demo-1",
    body: str | None = "{}",
) -> dict:
    return {
        "method": "GET",
        "url": url or f"http://localhost:8080{path}",
        "path": path,
        "status": status,
        "trace_id": trace_id,
        "content_type": "application/json",
        "body": body,
    }


def test_primary_prefers_the_declared_wait_for():
    session = _session_with(FakePage())
    session.set_trace_id("nokrqa-demo-1")
    _with_traffic(
        session,
        [
            _entry("/platform/other", body='{"other": 1}'),
            _entry("/platform/dashboard/metrics", body='{"gross_volume": 1}'),
        ],
    )
    primary = session._primary("/platform/dashboard/metrics")
    assert primary is not None
    assert primary["path"] == "/platform/dashboard/metrics"
    assert primary["body"] == '{"gross_volume": 1}'


def test_primary_ignores_a_response_from_another_step():
    # The defect this guards: step 2 finding the response step 1 produced, which
    # made the screen look loaded when step 2 had loaded nothing.
    session = _session_with(FakePage())
    session.set_trace_id("nokrqa-demo-2")
    _with_traffic(session, [_entry("/platform/dashboard/metrics", trace_id="nokrqa-demo-1")])
    assert session._primary("/platform/dashboard/metrics") is None


def test_primary_ignores_a_failing_wait_for_response():
    session = _session_with(FakePage())
    session.set_trace_id("nokrqa-demo-1")
    _with_traffic(session, [_entry("/platform/dashboard/metrics", status=503)])
    assert session._primary("/platform/dashboard/metrics") is None


def test_primary_falls_back_to_the_platform_call():
    session = _session_with(FakePage())
    session.set_trace_id("nokrqa-demo-1")
    _with_traffic(
        session,
        [
            # Asset traffic is never recorded, so the fallback sees API calls only.
            _entry("/platform/dashboard/metrics", trace_id="nokrqa-demo-1"),
        ],
    )
    primary = session._primary(None)
    assert primary is not None
    assert primary["path"] == "/platform/dashboard/metrics"


def test_fallback_skips_a_response_from_an_earlier_step():
    session = _session_with(FakePage())
    session.set_trace_id("nokrqa-demo-2")
    _with_traffic(
        session,
        [
            _entry("/platform/dashboard/metrics", trace_id="nokrqa-demo-1"),
            _entry("/platform/tenants/users", trace_id="nokrqa-demo-2"),
        ],
    )
    primary = session._primary(None)
    assert primary is not None
    assert primary["path"] == "/platform/tenants/users"


def test_traced_traffic_only_returns_this_step_calls():
    session = _session_with(FakePage())
    session.set_trace_id("nokrqa-demo-2")
    _with_traffic(
        session,
        [
            _entry("/platform/dashboard/metrics", trace_id="nokrqa-demo-1"),
            _entry("/platform/tenants/users", trace_id="nokrqa-demo-2"),
            _entry("/platform/tenants/users/summary", trace_id="nokrqa-demo-2"),
        ],
    )
    paths = [item["path"] for item in session._traced_traffic("nokrqa-demo-2")]
    assert paths == ["/platform/tenants/users", "/platform/tenants/users/summary"]


def test_primary_payload_tolerates_unreadable_headers():
    class _Dead:
        @property
        def headers(self) -> dict[str, str]:
            raise RuntimeError("response already released")

    session = _session_with(FakePage())
    session.set_trace_id("nokrqa-demo-1")
    entry = _entry("/platform/dashboard/metrics")
    _with_traffic(session, [entry], [_Dead()])
    primary = session._primary("/platform/dashboard/metrics")
    assert primary is not None
    # The body was read eagerly in the handler, so a dead response object does
    # not cost the step its evidence.
    assert primary["body"] == "{}"
    assert primary["headers"] == {}


# ── API traffic filter ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/ingest", True),
        ("/platform/dashboard/metrics", True),
        ("/auth/login", True),
        ("/chunk-ABC.js", False),
        ("/main.js", False),
        ("/@ng/component", False),
    ],
)
def test_only_backend_traffic_is_recorded(path: str, expected: bool):
    from nokr_qa.browser import _is_api_path

    assert _is_api_path(path) is expected


# ── navigation ───────────────────────────────────────────────────────────────


def test_navigation_is_a_noop_when_already_on_the_route():
    page = FakePage(evaluate=lambda script, arg=None: True)
    session = _session_with(page)
    session._navigate("/overview", 1000)
    assert page.pushes == []


def test_navigation_uses_push_state_for_in_app_routes():
    page = FakePage()
    # The router lands on the route as soon as the in-app push happens.
    page._evaluate = lambda script, arg=None: bool(page.pushes)
    session = _session_with(page)
    session._navigate("/customers?tab=ledger", 1000)
    assert page.pushes == ["/customers?tab=ledger"]


def test_navigation_failure_is_an_instrument_error():
    page = FakePage(evaluate=lambda script, arg=None: False)
    session = _session_with(page)
    with pytest.raises(HarnessError) as err:
        session._navigate("/nowhere", 50)
    assert err.value.code == "UI_NAVIGATION_FAILED"
    # It polls for the URL instead of guessing with a fixed sleep.
    assert page.waits


def test_url_matching_compares_path_and_query():
    page = FakePage(evaluate=lambda script, arg=None: True)
    session = _session_with(page)
    assert session._url_matches("/customers?tab=ledger") is True


# ── ui.structure (E3) ────────────────────────────────────────────────────────
#
# `expect(...).to_match_aria_snapshot(...)` is reached through a monkeypatched
# `playwright.sync_api.expect`, so the *decision* logic is tested without a
# browser. The real matcher is exercised by the `slow` test.


class _RecordingExpect:
    """The slice of Playwright's `expect` the session calls, with a fixed outcome."""

    def __init__(self, *, raises: AssertionError | None = None) -> None:
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, locator: Any) -> "_RecordingExpect":
        self.locator = locator
        return self

    def to_match_aria_snapshot(self, template: str, *, timeout: int) -> None:
        self.calls.append({"template": template, "timeout": timeout})
        if self.raises is not None:
            raise self.raises


def _patch_expect(monkeypatch: pytest.MonkeyPatch, fake: _RecordingExpect) -> None:
    import playwright.sync_api

    monkeypatch.setattr(playwright.sync_api, "expect", fake)


def test_structure_is_disabled_when_no_baseline_is_declared(monkeypatch):
    # The distinction A.19 insists on: an absent declaration is not a comparison
    # that passed, it is a comparison that never happened.
    fake = _RecordingExpect()
    _patch_expect(monkeypatch, fake)
    session = _session_with(FakePage(selectors={"main": 1}))
    assert session._structure("main", None, 15000) == {
        "enabled": False,
        "matched": None,
        "error": None,
    }
    assert fake.calls == []


def test_structure_reports_a_match(monkeypatch):
    fake = _RecordingExpect()
    _patch_expect(monkeypatch, fake)
    session = _session_with(FakePage(selectors={"main": 1}))
    report = session._structure("main", '- heading "Overview" [level=1]', 15000)
    assert report == {"enabled": True, "matched": True, "error": None}
    assert fake.calls[0]["template"] == '- heading "Overview" [level=1]'


def test_a_structure_mismatch_is_data_not_an_exception(monkeypatch):
    # A diverged tree is a product finding. Letting the AssertionError escape
    # would classify it as an instrument failure and hide the diff.
    fake = _RecordingExpect(raises=AssertionError("line 2: expected 'Overview'"))
    _patch_expect(monkeypatch, fake)
    session = _session_with(FakePage(selectors={"main": 1}))
    report = session._structure("main", '- heading "Overview" [level=1]', 15000)
    assert report["enabled"] is True
    assert report["matched"] is False
    assert "expected 'Overview'" in report["error"]


def test_structure_uses_a_short_budget_not_the_whole_page_timeout(monkeypatch):
    # `to_match_aria_snapshot` retries until its timeout, so a 15 s budget would
    # spend the page budget re-proving a diff it already has.
    fake = _RecordingExpect()
    _patch_expect(monkeypatch, fake)
    session = _session_with(FakePage(selectors={"main": 1}))
    session._structure("main", '- heading "x"', 15000)
    assert fake.calls[0]["timeout"] == 2000


def test_structure_shrinks_the_budget_when_the_step_declares_less(monkeypatch):
    fake = _RecordingExpect()
    _patch_expect(monkeypatch, fake)
    session = _session_with(FakePage(selectors={"main": 1}))
    session._structure("main", '- heading "x"', 500)
    assert fake.calls[0]["timeout"] == 500


def test_a_huge_structure_diff_is_truncated_for_the_artifact(monkeypatch):
    fake = _RecordingExpect(raises=AssertionError("x" * 9000))
    _patch_expect(monkeypatch, fake)
    session = _session_with(FakePage(selectors={"main": 1}))
    report = session._structure("main", '- heading "x"', 15000)
    assert len(report["error"]) < 9000
    assert "truncated" in report["error"]


# ── ui.a11y (E3) ─────────────────────────────────────────────────────────────


def test_a11y_injects_the_vendored_axe_and_runs_it_against_the_region():
    scripts: list[tuple[str, Any]] = []
    page = FakePage(
        selectors={"main": 1},
        evaluate=lambda script, arg=None: scripts.append((script, arg)) or [],
    )
    session = _session_with(page)
    report = session._a11y("main.main-content")
    assert report == {"enabled": True, "violations": [], "count": 0}
    # The first evaluate is the vendored build, the second is the scoped run.
    assert "axe" in scripts[0][0]
    context, options = scripts[1][1]["context"], scripts[1][1]["options"]
    assert context == "main.main-content"
    assert options["runOnly"]["values"] == [
        "wcag2a",
        "wcag2aa",
        "wcag21a",
        "wcag21aa",
        "wcag22aa",
    ]


def test_a11y_reports_the_violations_the_run_returned():
    page = FakePage(
        selectors={"main": 1},
        evaluate=lambda script, arg=None: (
            [{"id": "color-contrast", "impact": "serious", "nodes": []}]
            if "axe.run" in script
            else None
        ),
    )
    session = _session_with(page)
    report = session._a11y("main")
    assert report["enabled"] is True
    assert report["count"] == 1
    assert report["violations"][0]["id"] == "color-contrast"


def test_a_broken_axe_run_is_an_instrument_error():
    # The harness could not run the check — that is not the screen's fault. The
    # injection carries no argument; the scoped run is the call that passes one.
    def evaluate(script: str, arg: Any = None) -> Any:
        if arg is not None:
            raise RuntimeError("axe is not defined")
        return None

    session = _session_with(FakePage(selectors={"main": 1}, evaluate=evaluate))
    with pytest.raises(HarnessError) as err:
        session._a11y("main")
    assert err.value.code == "A11Y_RUN_FAILED"


def test_a_missing_axe_script_is_reported_with_the_install_hint():
    def evaluate(script: str, arg: Any = None) -> Any:
        raise RuntimeError("evaluation failed")

    session = _session_with(FakePage(selectors={"main": 1}, evaluate=evaluate))
    with pytest.raises(HarnessError) as err:
        session._a11y("main")
    assert err.value.code == "A11Y_SCRIPT_MISSING"
    assert "axe-playwright-python" in err.value.hint


def test_axe_is_reinjected_per_check_so_a_full_reload_cannot_drop_it():
    # The login `goto` replaces the document and the `axe` global with it; a
    # cached "injected once" flag would then scan an undefined global.
    injections: list[str] = []
    page = FakePage(
        selectors={"main": 1},
        evaluate=lambda script, arg=None: injections.append(script) or ([] if arg else None),
    )
    session = _session_with(page)
    session._a11y("main")
    session._a11y("main")
    assert len(injections) == 4  # two injections, two runs
