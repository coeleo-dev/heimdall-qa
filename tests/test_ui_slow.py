"""Gate 5 of E2: the real Chromium drives the real dashboard.

Marked `slow` because it needs the whole stack up:

    pnpm exec nx run nokr-b2b-dashboard:serve      # http://localhost:4200
    (the API on :8080 with the JVM logs wired in config.yaml)
    python -m playwright install chromium

Run it with `NOKR_QA_SLOW=1 pytest -m slow tests/test_ui_slow.py`. It skips
(rather than passing silently) when the stack is not there, and the E2 gate
requires it to have actually run.
"""

import json
from pathlib import Path

import httpx
import pytest
import yaml

from nokr_qa.config import load_config
from nokr_qa.schema.load import load_round
from nokr_qa.schema.load import load_suite
from nokr_qa.suite_run import execute_suite_round
from nokr_qa.ui_step import execute_ui_step

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]


def _secrets() -> dict[str, str]:
    """Credentials from secrets.local.yaml, falling back to register captures."""
    local = REPO_ROOT / "secrets.local.yaml"
    values: dict[str, str] = {}
    if local.is_file():
        values.update(yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    captures = REPO_ROOT / "runs" / "shared-captures.json"
    if captures.is_file():
        stored = json.loads(captures.read_text(encoding="utf-8"))
        values.setdefault("email", stored.get("register_email", ""))
        values.setdefault("password", stored.get("register_password", ""))
    return {str(key): str(value) for key, value in values.items() if value}


def _stack():
    config = load_config(REPO_ROOT / "config.yaml")
    return config, _secrets()


def _skip_unless_up(config) -> None:
    try:
        httpx.get(config.nokr_dashboard, timeout=2.0)
    except httpx.HTTPError as err:
        pytest.skip(f"dashboard not running at {config.nokr_dashboard}: {err}")


def test_ui_round_drives_login_and_a_second_screen_in_a_real_browser(tmp_path: Path):
    config, secrets = _stack()
    _skip_unless_up(config)
    if not secrets.get("email") or not secrets.get("password"):
        pytest.skip("no dashboard credentials in secrets.local.yaml or register captures")

    run_dir = execute_suite_round(
        REPO_ROOT / "rounds" / "ui-overview.yaml",
        load_suite(REPO_ROOT / "suites" / "ui-smoke.yaml"),
        load_round(REPO_ROOT / "rounds" / "ui-overview.yaml"),
        root=REPO_ROOT,
        config=config,
        client=httpx.Client(timeout=10.0),
        runs_dir=tmp_path / "runs",
        secrets=secrets,
        mode="headless",
    )

    login = run_dir / "steps" / "001-ui-login"
    customers = run_dir / "steps" / "002-ui-customers"
    for step_dir in (login, customers):
        for name in ("ui.json", "aria.yml", "ui-values.json", "network.json", "packs.json"):
            assert (step_dir / name).is_file(), f"{step_dir.name} missing {name}"
        assert (step_dir / "aria.yml").read_text(encoding="utf-8").strip()
        assert (step_dir / "screenshot.png").is_file()

    # Step 1 authenticates through the form and reads the screen the guard lands
    # on; step 2 navigates in-app, which is what proves the session survives.
    login_ui = json.loads((login / "ui.json").read_text(encoding="utf-8"))
    assert login_ui["declared_path"] == "/auth/login"
    assert login_ui["path"] == "/overview"
    assert login_ui["trace_id"] == "nokrqa-ui-overview-1"
    assert login_ui["status"] == 200

    customers_ui = json.loads((customers / "ui.json").read_text(encoding="utf-8"))
    assert customers_ui["path"] == "/customers?tab=metrics"
    assert customers_ui["trace_id"] == "nokrqa-ui-overview-2"
    assert customers_ui["status"] == 200

    for step_dir, trace_id, prefix in (
        (login, "nokrqa-ui-overview-1", "overview."),
        (customers, "nokrqa-ui-overview-2", "customers."),
    ):
        values = json.loads((step_dir / "ui-values.json").read_text(encoding="utf-8"))
        assert values, f"{step_dir.name} read no surface"
        assert any(name.startswith(prefix) for name in values), values

        # A traced request of this step must exist in the browser's own traffic.
        network = json.loads((step_dir / "network.json").read_text(encoding="utf-8"))
        assert all(entry["trace_id"] == trace_id for entry in network)
        assert network, f"{step_dir.name} recorded no API traffic"

        # And the JVM must have logged lines under exactly that trace id.
        logs = (step_dir / "logs-web.txt").read_text(encoding="utf-8")
        assert logs.strip(), f"{step_dir.name} captured no log line for {trace_id}"
        assert all(f"trace_id: [{trace_id}]" in line for line in logs.splitlines())

        packs = json.loads((step_dir / "packs.json").read_text(encoding="utf-8"))
        statuses = {item["pack_id"]: item["status"] for item in packs["results"]}
        assert statuses.get("http.baseline") == "pass", statuses
        assert statuses.get("observability") == "pass", statuses

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["fail"] == 0, summary["counts"]


def test_real_login_by_form_succeeds(tmp_path: Path):
    config, secrets = _stack()
    _skip_unless_up(config)
    if not secrets.get("email") or not secrets.get("password"):
        pytest.skip("no dashboard credentials in secrets.local.yaml or register captures")

    from nokr_qa.browser import UiSession
    from nokr_qa.schema.models import UiStep

    holder: dict[str, object] = {}

    def factory() -> UiSession:
        session = UiSession(
            dashboard_url=config.nokr_dashboard,
            credentials={"email": secrets["email"], "password": secrets["password"]},
            environment="sandbox",
        )
        session.start()
        holder["session"] = session
        return session

    try:
        outcome = execute_ui_step(
            UiStep(id="overview", path="/overview", wait_for="/platform/dashboard/metrics"),
            run_dir=tmp_path / "runs" / "real",
            root=REPO_ROOT,
            step_index=1,
            run_id="ui-real",
            config=config,
            client=httpx.Client(timeout=10.0),
            driver_factory=factory,
            environment="sandbox",
        )
    finally:
        session = holder.get("session")
        if isinstance(session, UiSession):
            session.close()

    assert outcome.verdict["status"] == "pass", outcome.verdict


def test_dashboard_down_is_a_failed_instrument_step(tmp_path: Path, monkeypatch):
    # A.19 requires a hard failure, not a SKIP, when the harness cannot reach the
    # dashboard — and the round must still leave evidence behind.
    config, _ = _stack()
    _skip_unless_up(config)
    monkeypatch.setattr(config, "nokr_dashboard", "http://127.0.0.1:9")

    from nokr_qa.browser import UiSession
    from nokr_qa.schema.models import UiStep

    def factory() -> UiSession:  # pragma: no cover - must never be called
        raise AssertionError("the browser must not start when the dashboard is down")

    outcome = execute_ui_step(
        UiStep(id="overview", path="/overview"),
        run_dir=tmp_path / "runs" / "down",
        root=REPO_ROOT,
        step_index=1,
        run_id="ui-down",
        config=config,
        client=httpx.Client(timeout=2.0),
        driver_factory=factory,
        environment="sandbox",
    )

    assert outcome.verdict["status"] == "fail"
    assert outcome.verdict["cause"] == "instrument"
    assert outcome.verdict["continue"] is False
    assert (outcome.step_dir / "ui.json").is_file()
    assert (outcome.step_dir / "verdict.json").is_file()


def test_a_declared_but_missing_baseline_fails_as_an_instrument(tmp_path: Path):
    # A.19: a baseline the step points at but that is not in the repo is a broken
    # instrument. Downgrading it to `skipped` would let a typo delete the check.
    config, _ = _stack()
    _skip_unless_up(config)

    from nokr_qa.browser import UiSession
    from nokr_qa.schema.models import UiStep

    def factory() -> UiSession:  # pragma: no cover - must never be called
        raise AssertionError("the browser must not start without a baseline on disk")

    outcome = execute_ui_step(
        UiStep(
            id="overview",
            path="/overview",
            baseline="baselines/ui/does-not-exist.aria.yml",
        ),
        run_dir=tmp_path / "runs" / "missing-baseline",
        root=REPO_ROOT,
        step_index=1,
        run_id="ui-missing-baseline",
        config=config,
        client=httpx.Client(timeout=2.0),
        driver_factory=factory,
        environment="sandbox",
    )

    assert outcome.verdict["status"] == "fail"
    assert outcome.verdict["cause"] == "instrument"
    assert "UI_BASELINE_MISSING" in outcome.verdict["comment"]


# ── E3: the real engines, driven through the real `execute_ui_step` ──────────


def test_a_real_axe_scan_flags_a_synthetic_violation(tmp_path: Path):
    """`ui.a11y` end to end: a real axe run, a real violation in `a11y.json`.

    The page is synthetic so the finding is planted and not a moving target: the
    pack must fire on a *known* violation before anyone trusts it on the five
    screens.
    """
    config, _ = _stack()
    from playwright.sync_api import sync_playwright

    from nokr_qa.browser import UiSession

    holder: dict[str, object] = {}

    def factory() -> UiSession:
        session = UiSession(dashboard_url=config.nokr_dashboard)
        session.start()
        holder["session"] = session
        return session

    # A red-on-grey button is a textbook `color-contrast` violation; the page is
    # served from `set_content`, so no dashboard and no API are involved.
    html = (
        "<main><h1>Overview</h1>"
        '<button style="color:#c9c7c0;background:#d6d3cb">Contrast</button>'
        "</main>"
    )
    try:
        session = factory()
        page = session._page  # noqa: SLF001 - the scan is the unit under test
        page.set_content(html)
        report = session._a11y("main")
    finally:
        actor = holder.get("session")
        if isinstance(actor, UiSession):
            actor.close()

    assert report["enabled"] is True
    assert report["count"] >= 1
    assert any(item["id"] == "color-contrast" for item in report["violations"])


def test_a_real_axe_scan_stays_green_on_a_clean_page(tmp_path: Path):
    config, _ = _stack()
    from nokr_qa.browser import UiSession

    holder: dict[str, object] = {}

    def factory() -> UiSession:
        session = UiSession(dashboard_url=config.nokr_dashboard)
        session.start()
        holder["session"] = session
        return session

    try:
        session = factory()
        session._page.set_content(  # noqa: SLF001 - the scan is the unit under test
            '<main lang="en"><h1>Overview</h1><p>Nothing to report.</p></main>'
        )
        report = session._a11y("main")
    finally:
        actor = holder.get("session")
        if isinstance(actor, UiSession):
            actor.close()

    assert report["enabled"] is True
    assert report["violations"] == []


def test_the_committed_baseline_matches_the_live_overview_and_a_mutation_fails(
    tmp_path: Path,
):
    """The structural gate, against the real dashboard.

    Two assertions in one test on purpose: a baseline that "passes" is only
    evidence if a perturbed copy of it *fails*. Otherwise the matcher could be
    matching nothing and the suite would still be green.
    """
    config, secrets = _stack()
    _skip_unless_up(config)
    if not secrets.get("email") or not secrets.get("password"):
        pytest.skip("no dashboard credentials in secrets.local.yaml or register captures")

    from nokr_qa.browser import UiSession

    baseline_path = REPO_ROOT / "baselines" / "ui" / "overview.aria.yml"
    if not baseline_path.is_file():
        pytest.skip("baselines/ui/overview.aria.yml not committed yet")

    holder: dict[str, object] = {}

    def factory() -> UiSession:
        session = UiSession(
            dashboard_url=config.nokr_dashboard,
            credentials={"email": secrets["email"], "password": secrets["password"]},
            environment="sandbox",
        )
        session.start()
        holder["session"] = session
        return session

    try:
        session = factory()
        session.set_trace_id("nokrqa-structure-1")
        read = session.read_screen(
            path="/auth/login",
            wait_for="/platform/dashboard/metrics",
            region="main",
            timeout_ms=config.ui.page_ms,
            screenshot=False,
            trace_id="nokrqa-structure-1",
            baseline=baseline_path.read_text(encoding="utf-8"),
            a11y=False,
        )
        assert read.structure is not None
        assert read.structure["matched"] is True, read.structure["error"]

        mutated = (baseline_path.read_text(encoding="utf-8") + '\n- heading "Not here" [level=1]').strip()
        session.set_trace_id("nokrqa-structure-2")
        diverged = session.read_screen(
            path="/overview",
            wait_for="/platform/dashboard/metrics",
            region="main",
            timeout_ms=config.ui.page_ms,
            screenshot=False,
            trace_id="nokrqa-structure-2",
            baseline=mutated,
            a11y=False,
        )
        assert diverged.structure is not None
        assert diverged.structure["matched"] is False
    finally:
        actor = holder.get("session")
        if isinstance(actor, UiSession):
            actor.close()
