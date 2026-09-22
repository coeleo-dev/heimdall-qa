"""Shared helpers for the `ui` step tests (E2).

Kept out of `conftest.py` because only the browser tests need it, and out of
`test_*.py` so the artifacts and correlation tests can share one fake session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Callable

import httpx
import yaml

from nokr_qa.browser import PageRead
from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.errors import HarnessError

PRIMARY_PATH = "/platform/dashboard/metrics"
SURFACE = "overview.metrics.gross_volume"


class FakeUiSession:
    """Stands in for `UiSession`: same contract, no Chromium and no network."""

    def __init__(
        self,
        *,
        error: HarnessError | None = None,
        aria: str = '- heading "Overview" [level=1]\n- text: R$ 1.234,50',
        values: dict[str, str] | None = None,
        console: list[dict[str, Any]] | None = None,
        screenshot: bytes | None = b"\x89PNG\r\n\x1a\nfake",
        on_read: Callable[[str], None] | None = None,
        primary_status: int = 200,
        region: str = "main.main-content",
        structure: dict[str, Any] | None = None,
        a11y: dict[str, Any] | None = None,
    ) -> None:
        self.error = error
        self.aria = aria
        self.values = {SURFACE: "1234.5"} if values is None else values
        # Clean by default: E2 used to seed an error here, but `ui.render` (E3)
        # now judges the console, so a default error would make every fixture
        # fail for a reason it never declared.
        self.console = [] if console is None else console
        self.screenshot_bytes = screenshot
        self.on_read = on_read
        self.primary_status = primary_status
        self.region = region
        self.structure = structure
        self.a11y = a11y if a11y is not None else {"enabled": False, "violations": []}
        self.trace_ids: list[str] = []
        self.reads: list[dict[str, Any]] = []
        self.closed = False

    def set_trace_id(self, trace_id: str) -> None:
        self.trace_ids.append(trace_id)

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
        self.reads.append(
            {
                "path": path,
                "wait_for": wait_for,
                "region": region,
                "timeout_ms": timeout_ms,
                "screenshot": screenshot,
                "trace_id": trace_id,
                "baseline": baseline,
                "a11y": a11y,
            }
        )
        if self.error is not None:
            raise self.error
        if self.on_read is not None:
            self.on_read(trace_id)
        network = [
            {
                "method": "GET",
                "url": f"http://localhost:8080{PRIMARY_PATH}",
                "path": PRIMARY_PATH,
                "status": self.primary_status,
                "trace_id": trace_id,
                "content_type": "application/json",
            }
        ]
        return PageRead(
            path=path,
            url=f"http://localhost:4200{path}",
            region=self.region,
            values=dict(self.values),
            aria=self.aria,
            console=list(self.console),
            network=network,
            primary={
                "method": "GET",
                "url": f"http://localhost:8080{PRIMARY_PATH}",
                "path": PRIMARY_PATH,
                "status": self.primary_status,
                "headers": {"X-Trace-Id": trace_id, "Content-Type": "application/json"},
                "body": '{"gross_volume": 1234.5}',
            },
            screenshot=self.screenshot_bytes if screenshot else None,
            elapsed_ms=42.0,
            trace_id=trace_id,
            structure=(
                dict(self.structure)
                if self.structure is not None
                else {"enabled": baseline is not None, "matched": True if baseline else None, "error": None}
            ),
            a11y=dict(self.a11y) if a11y else {"enabled": False, "violations": []},
        )

    def close(self) -> None:  # pragma: no cover - asserted, never raised
        self.closed = True


class FakeBrowserFactory:
    """Hands the same fake to the suite and remembers how it was called."""

    def __init__(self, session: FakeUiSession) -> None:
        self.session = session
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeUiSession:
        self.calls.append(kwargs)
        return self.session


def ui_config(
    root: Path,
    *,
    logs_ms: int = 0,
    page_ms: int = 15000,
    screenshot: bool = True,
    dashboard: str = "http://localhost:4200",
) -> HarnessConfig:
    web_log = root / "nokr-web.log"
    worker_log = root / "nokr-worker.log"
    web_log.write_text(web_log.read_text(encoding="utf-8") if web_log.exists() else "", encoding="utf-8")
    worker_log.write_text(
        worker_log.read_text(encoding="utf-8") if worker_log.exists() else "",
        encoding="utf-8",
    )
    config = HarnessConfig(
        log_files=LogFiles(web=str(web_log), worker=str(worker_log)),
        nokr_dashboard=dashboard,
    )
    config.ui.logs_ms = logs_ms
    config.ui.page_ms = page_ms
    config.ui.screenshot = screenshot
    return config

def ui_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)


def dashboard_up(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/":
        return httpx.Response(
            200, text="<html><body>ok</body></html>", headers={"Content-Type": "text/html"}
        )
    return httpx.Response(404, json={"error": "not found", "traceId": "t"})


def dashboard_down(request: httpx.Request) -> httpx.Response:
    """Preflight target that refuses connections, like a stopped dev-server."""
    raise httpx.ConnectError("connection refused", request=request)


class FakeLocator:
    def __init__(self, count: int, aria: str = "") -> None:
        self._count = count
        self._aria = aria

    def count(self) -> int:
        return self._count

    @property
    def first(self) -> "FakeLocator":
        return self

    def aria_snapshot(self) -> str:
        return self._aria


class FakePage:
    """The slice of the Playwright `Page` that `UiSession` actually touches.

    Lets `browser.py` be unit tested — region fallback, surface reading, primary
    selection — without downloading or launching anything.
    """

    def __init__(
        self,
        *,
        url: str = "http://localhost:4200/overview",
        selectors: dict[str, int] | None = None,
        evaluate: Callable[[str, Any], Any] | None = None,
        aria: str = '- heading "Overview" [level=1]',
    ) -> None:
        self.url = url
        self.selectors = selectors or {}
        self._evaluate = evaluate or (lambda script, arg=None: None)
        self._aria = aria
        self.waits: list[int] = []
        self.pushes: list[str] = []

    def evaluate(self, script: str, arg: Any = None) -> Any:
        if "history.pushState" in script and arg is not None:
            self.pushes.append(arg)
            return None
        return self._evaluate(script, arg)

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.selectors.get(selector, 0), self._aria)

    def screenshot(self, full_page: bool = False) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake"


def build_ui_tree(
    root: Path,
    *,
    path: str = "/overview",
    wait_for: str | None = PRIMARY_PATH,
    region: str = "main",
    timeout_ms: int | None = None,
    with_loop: bool = False,
    baseline: str | None = None,
    baseline_text: str | None = None,
    waive: list[dict[str, Any]] | None = None,
) -> Path:
    (root / "suites").mkdir(parents=True, exist_ok=True)
    ui_block: dict[str, Any] = {"id": "overview", "path": path, "region": region}
    if wait_for is not None:
        ui_block["wait_for"] = wait_for
    if timeout_ms is not None:
        ui_block["timeout_ms"] = timeout_ms
    if baseline is not None:
        ui_block["baseline"] = baseline
        # Written under the root, which is what `_load_baseline` resolves against.
        target = root / baseline
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(baseline_text or '- heading "Overview" [level=1]\n', encoding="utf-8")
    if waive is not None:
        ui_block["waive"] = waive
    steps: list[dict[str, Any]] = [{"ui": ui_block}]
    if with_loop:
        steps.append(
            {
                "loop": {
                    "times": 1,
                    "case": "cases/ingest/ingest-H01.yaml",
                }
            }
        )
    (root / "suites" / "ui.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "ui",
                "catalog": {"pricing_model": "FLAT"},
                "steps": steps,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    round_path = root / "round.yaml"
    round_path.write_text(
        "\n".join(
            [
                "id: ui-demo",
                "suite: suites/ui.yaml",
                "mode: headless",
                "environment: sandbox",
                "include: []",
            ]
        ),
        encoding="utf-8",
    )
    return round_path


def run_ui_round(
    round_path: Path,
    root: Path,
    *,
    session: FakeUiSession,
    config: HarnessConfig,
    handler: Callable[[httpx.Request], httpx.Response] = dashboard_up,
    secrets: dict[str, str] | None = None,
) -> tuple[Path, FakeBrowserFactory]:
    from nokr_qa.schema.load import load_round
    from nokr_qa.schema.load import load_suite
    from nokr_qa.suite_run import execute_suite_round

    factory = FakeBrowserFactory(session)
    run_dir = execute_suite_round(
        round_path,
        load_suite(root / "suites" / "ui.yaml"),
        load_round(round_path),
        root=root,
        config=config,
        client=ui_client(handler),
        runs_dir=root / "runs",
        secrets=secrets if secrets is not None else {"email": "qa@nokr.dev", "password": "pw"},
        mode="headless",
        browser_factory=factory,
    )
    return run_dir, factory


def write_log_line(
    path: Path | str,
    trace_id: str,
    *,
    level: str = "INFO",
    message: str = "request handled",
) -> None:
    """Appends one JVM-shaped line so the real collector can find it by trace_id.

    The timestamp is irrelevant while the marker matches: `collect` returns the
    marker hits before it ever falls back to the time window.
    """
    line = (
        "2026-09-16 12:00:00.123 "
        f"{level} 1 --- [io-8080-exec-1] c.n.c.DashboardController.metrics - "
        f"{message} - trace_id: [{trace_id}]"
    )
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
