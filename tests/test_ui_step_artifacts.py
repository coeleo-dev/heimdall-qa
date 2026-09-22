"""Gates 1 and 3 of E2: artifacts exist, and a broken instrument fails hard."""

from pathlib import Path
import json

from nokr_qa.errors import HarnessError
from support_ui import FakeUiSession
from support_ui import SURFACE
from support_ui import build_ui_tree
from support_ui import dashboard_down
from support_ui import run_ui_round
from support_ui import ui_config
from support_ui import write_log_line

ARTIFACTS = (
    "ui.json",
    "aria.yml",
    "a11y.json",
    "ui-values.json",
    "console.log",
    "network.json",
    "screenshot.png",
    "logs-web.txt",
    "logs-worker.txt",
    "timing.json",
    "packs.json",
    "verdict.json",
)

# A browser step has no worker-side consumer, so `logs-worker.txt` is legitimately
# empty (the HTTP steps write it only when a worker line shares the trace id).
MAY_BE_EMPTY = frozenset({"logs-worker.txt"})


def _with_log(session: FakeUiSession, config) -> FakeUiSession:
    """Makes the fake emit the JVM line a real round would find by trace_id."""
    session.on_read = lambda trace_id: write_log_line(config.log_files.web, trace_id)
    return session


def test_ui_step_writes_every_artifact(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = _with_log(FakeUiSession(), config)
    run_dir, factory = run_ui_round(round_path, tmp_path, session=session, config=config)

    step_dir = run_dir / "steps" / "001-ui-overview"
    for name in ARTIFACTS:
        path = step_dir / name
        assert path.is_file(), f"missing artifact {name}"
        if name not in MAY_BE_EMPTY:
            assert path.stat().st_size > 0, f"empty artifact {name}"

    ui = json.loads((step_dir / "ui.json").read_text(encoding="utf-8"))
    assert ui["reachable"] is True
    assert ui["path"] == "/overview"
    assert ui["status"] == 200
    assert ui["trace_id"] == "nokrqa-ui-demo-1"
    assert ui["console_errors"] == 0
    # No baseline declared in this fixture, so `ui.structure` skipped and the
    # comparison is recorded as not enabled rather than as a green.
    assert ui["structure"]["enabled"] is False
    assert ui["structure"]["matched"] is None

    values = json.loads((step_dir / "ui-values.json").read_text(encoding="utf-8"))
    assert values == {SURFACE: "1234.5"}

    a11y = json.loads((step_dir / "a11y.json").read_text(encoding="utf-8"))
    assert a11y["enabled"] is False
    assert a11y["violations"] == []

    network = json.loads((step_dir / "network.json").read_text(encoding="utf-8"))
    assert network[0]["trace_id"] == "nokrqa-ui-demo-1"

    assert "heading" in (step_dir / "aria.yml").read_text(encoding="utf-8")

    verdict = json.loads((step_dir / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["status"] == "pass"
    assert verdict["cause"] is None

    # One context per run, trace swapped per step, closed when the suite ends.
    assert len(factory.calls) == 1
    assert factory.calls[0]["dashboard_url"] == "http://localhost:4200"
    assert factory.calls[0]["credentials"] == {"email": "qa@nokr.dev", "password": "pw"}
    assert session.trace_ids == ["nokrqa-ui-demo-1"]
    assert session.closed is True


def test_packs_run_over_the_browser_transport(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = _with_log(FakeUiSession(), config)
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    packs = json.loads(
        (run_dir / "steps" / "001-ui-overview" / "packs.json").read_text(encoding="utf-8")
    )
    results = {item["pack_id"]: item["status"] for item in packs["results"]}
    # Transport (E2) plus the structural set (E3). The two `skipped` entries are
    # honest: this fixture declares no baseline and runs no a11y scan, so neither
    # check claims a green it never earned.
    assert results == {
        "http.baseline": "pass",
        "observability": "pass",
        "http.success": "pass",
        "ui.render": "pass",
        "ui.structure": "skipped",
        "ui.a11y": "skipped",
        "ui.visual": "skipped",
    }
    assert packs["logs_incomplete"] is False


def test_console_errors_fail_ui_render(tmp_path: Path):
    # E2 collected console evidence; E3 (A.19) is what turns it into a verdict.
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = _with_log(
        FakeUiSession(
            console=[{"type": "error", "text": "net::ERR_FAILED", "location": {}}]
        ),
        config,
    )
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    step_dir = run_dir / "steps" / "001-ui-overview"
    packs = json.loads((step_dir / "packs.json").read_text(encoding="utf-8"))
    results = {item["pack_id"]: item["status"] for item in packs["results"]}
    assert results["ui.render"] == "fail"

    verdict = json.loads((step_dir / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["status"] == "fail"
    # A console error is the product's, not the instrument's.
    assert verdict["cause"] == "product"
    assert verdict["continue"] is True


def test_a_console_error_does_not_bring_down_the_other_packs(tmp_path: Path):
    # The E3 gate asks for one planted fixture per pack, so a `ui.render` failure
    # must not drag `ui.structure` or `ui.a11y` down with it.
    round_path = build_ui_tree(
        tmp_path, baseline="baselines/ui/overview.aria.yml", baseline_text='- heading "Overview" [level=1]\n'
    )
    config = ui_config(tmp_path)
    session = _with_log(
        FakeUiSession(console=[{"type": "error", "text": "boom", "location": {}}]), config
    )
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    packs = json.loads(
        (run_dir / "steps" / "001-ui-overview" / "packs.json").read_text(encoding="utf-8")
    )
    results = {item["pack_id"]: item["status"] for item in packs["results"]}
    assert results["ui.render"] == "fail"
    assert results["ui.structure"] == "pass"
    assert results["ui.a11y"] == "skipped"


def test_screenshot_is_skipped_when_disabled(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path, screenshot=False)
    session = _with_log(FakeUiSession(), config)
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    assert not (run_dir / "steps" / "001-ui-overview" / "screenshot.png").exists()


def test_declared_timeout_wins_over_the_config_default(tmp_path: Path):
    round_path = build_ui_tree(tmp_path, timeout_ms=3000)
    config = ui_config(tmp_path, page_ms=15000)
    session = _with_log(FakeUiSession(), config)
    run_ui_round(round_path, tmp_path, session=session, config=config)

    assert session.reads[0]["timeout_ms"] == 3000


def test_region_declared_in_yaml_reaches_the_driver(tmp_path: Path):
    round_path = build_ui_tree(tmp_path, region="main")
    config = ui_config(tmp_path)
    session = _with_log(FakeUiSession(), config)
    run_ui_round(round_path, tmp_path, session=session, config=config)

    assert session.reads[0]["region"] == "main"


def test_dashboard_down_fails_as_instrument_not_skip(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = FakeUiSession()
    run_dir, factory = run_ui_round(
        round_path, tmp_path, session=session, config=config, handler=dashboard_down
    )

    step_dir = run_dir / "steps" / "001-ui-overview"
    ui = json.loads((step_dir / "ui.json").read_text(encoding="utf-8"))
    assert ui["reachable"] is False
    assert "unreachable" in ui["error"]

    verdict = json.loads((step_dir / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["status"] == "fail"
    assert verdict["cause"] == "instrument"
    assert verdict["continue"] is False

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["fail"] >= 1
    assert summary["counts"]["instrument"] >= 1

    # The browser is never booted when the dashboard is down.
    assert factory.calls == []


def test_instrument_failure_is_recorded_with_evidence(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = FakeUiSession(
        error=HarnessError(
            code="UI_PAGE_TIMEOUT",
            message="the screen /overview did not finish loading",
            hint="raise ui.page_ms",
        )
    )
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    step_dir = run_dir / "steps" / "001-ui-overview"
    ui = json.loads((step_dir / "ui.json").read_text(encoding="utf-8"))
    assert ui["reachable"] is False
    assert "UI_PAGE_TIMEOUT" in ui["error"]

    verdict = json.loads((step_dir / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["cause"] == "instrument"
    assert verdict["continue"] is False
    assert (run_dir / "summary.json").is_file()


def test_a_product_failure_does_not_stop_the_suite(tmp_path: Path):
    # The primary answers 500: the backend misbehaved, so the pack fails with
    # cause `product` and the round keeps going instead of aborting.
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = _with_log(FakeUiSession(primary_status=500), config)
    run_dir, _ = run_ui_round(round_path, tmp_path, session=session, config=config)

    verdict = json.loads(
        (run_dir / "steps" / "001-ui-overview" / "verdict.json").read_text(encoding="utf-8")
    )
    assert verdict["status"] == "fail"
    assert verdict["cause"] == "product"
    assert verdict["continue"] is True

    packs = json.loads(
        (run_dir / "steps" / "001-ui-overview" / "packs.json").read_text(encoding="utf-8")
    )
    results = {item["pack_id"]: item["status"] for item in packs["results"]}
    assert results["http.baseline"] == "fail"
    assert "http.success" not in results


def test_missing_credentials_are_reported_with_a_hint(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = FakeUiSession(
        error=HarnessError(
            code="SECRET_MISSING",
            message="the ui step needs the dashboard credentials",
            hint="add email/password to secrets.local.yaml",
        )
    )
    # SECRET_MISSING is not an instrument failure: it aborts the round, exactly
    # like the HTTP path does before a run starts.
    try:
        run_ui_round(round_path, tmp_path, session=session, config=config)
    except HarnessError as err:
        assert err.code == "SECRET_MISSING"
    else:  # pragma: no cover - the call must raise
        raise AssertionError("SECRET_MISSING must propagate")


def test_captures_supply_credentials_when_secrets_do_not(tmp_path: Path):
    round_path = build_ui_tree(tmp_path)
    config = ui_config(tmp_path)
    session = _with_log(FakeUiSession(), config)
    _, factory = run_ui_round(
        round_path,
        tmp_path,
        session=session,
        config=config,
        secrets={},
    )
    # `_usable_secret` skips blanks; the register captures fill the gap.
    assert factory.calls[0]["credentials"] == {"email": "", "password": ""}
