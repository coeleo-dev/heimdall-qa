from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from time import monotonic
from time import perf_counter
from time import sleep
from typing import Any
from typing import Callable

import httpx

from nokr_qa.browser import UiDriver
from nokr_qa.browser import UiSession
from nokr_qa.bru_parser import parse_bru
from nokr_qa.config import HarnessConfig
from nokr_qa.http_client import send
from nokr_qa.jsonpath import MISSING
from nokr_qa.jsonpath import lookup
from nokr_qa.oracle.book import Book
from nokr_qa.oracle.money import require_flat_model
from nokr_qa.oracle.money import to_decimal
from nokr_qa.packs import PackResult
from nokr_qa.packs.values import SurfaceEval
from nokr_qa.packs.values import expected_after
from nokr_qa.packs.values import surface_matches
from nokr_qa.packs.values import values_packs
from nokr_qa.run_store import create_run
from nokr_qa.run_store import link_latest
from nokr_qa.run_store import write_book
from nokr_qa.run_store import write_evidence
from nokr_qa.run_store import write_probe
from nokr_qa.run_store import write_summary
from nokr_qa.run_store import write_verdict
from nokr_qa.runner import RoundStep
from nokr_qa.runner import _SECRET_CAPTURE_KEYS
from nokr_qa.runner import _auto_verdict
from nokr_qa.runner import _build_evidence
from nokr_qa.runner import _build_summary
from nokr_qa.runner import _load_included_cases
from nokr_qa.runner import _prepare_case
from nokr_qa.runner import _reject_todo_status
from nokr_qa.runner import _response_body
from nokr_qa.runner import _round_step
from nokr_qa.runner import _usable_secret
from nokr_qa.runner import execute_step
from nokr_qa.runner import interpolate
from nokr_qa.schema.load import load_case
from nokr_qa.schema.models import CaseFile
from nokr_qa.schema.models import LoopSpec
from nokr_qa.schema.models import PollSpec
from nokr_qa.schema.models import ProbeSpec
from nokr_qa.schema.models import RoundFile
from nokr_qa.schema.models import SuiteFile
from nokr_qa.schema.models import SurfaceSpec
from nokr_qa.schema.models import UiStep
from nokr_qa.ui_step import UiOutcome
from nokr_qa.ui_step import execute_ui_step
from nokr_qa.validate import resolve_path

_POLL_INTERVAL_S = 0.25


BrowserFactory = Callable[..., UiDriver]


def default_browser_factory(
    *,
    dashboard_url: str,
    credentials: dict[str, str],
    environment: str,
) -> UiDriver:
    """Boots Chromium. Kept as a seam so tests can run without a browser."""
    session = UiSession(
        dashboard_url=dashboard_url,
        credentials=credentials,
        environment=environment,
    )
    session.start()
    return session


def has_ui_steps(suite: SuiteFile) -> bool:
    return any(step.ui is not None for step in suite.steps)


@dataclass(frozen=True)
class VisibleStep:
    suite_index: int
    kind: str
    label: str
    loop: LoopSpec | None
    probe: ProbeSpec | None


@dataclass
class LoopOutcome:
    records: list[RoundStep]
    last_dir: Path | None
    stop_suite: bool
    pack_failed: bool


@dataclass
class ProbeOutcome:
    record: RoundStep
    probe_dir: Path
    auto: dict[str, Any]
    failed: bool


def visible_steps(suite: SuiteFile) -> list[VisibleStep]:
    items: list[VisibleStep] = []
    for index, step in enumerate(suite.steps):
        if step.loop is not None:
            items.append(
                VisibleStep(
                    index,
                    "loop",
                    loop_label(step.loop),
                    step.loop,
                    None,
                )
            )
        elif step.probe is not None:
            items.append(
                VisibleStep(
                    index,
                    "probe",
                    f"probe {step.probe.id}",
                    None,
                    step.probe,
                )
            )
    return items


def loop_label(loop: LoopSpec) -> str:
    family = Path(loop.case).stem.split("-")[0]
    return f"loop {family} ×{loop.times}"


def execute_suite_round(
    round_path: Path,
    suite: SuiteFile,
    round_file: RoundFile,
    *,
    root: Path,
    config: HarnessConfig,
    client: httpx.Client,
    runs_dir: Path,
    secrets: dict[str, str],
    mode: str,
    browser_factory: BrowserFactory | None = None,
) -> Path:
    cases = _load_included_cases(round_file, root)
    _reject_todo_status(cases)
    started = perf_counter()
    run_dir = create_run(runs_dir, round_file.id)
    shutil.copy(round_path, run_dir / "round.yaml")
    ctx = SuiteRun(
        suite=suite,
        round_file=round_file,
        root=root,
        config=config,
        client=client,
        run_dir=run_dir,
        secrets=secrets,
        browser_factory=browser_factory,
    )
    try:
        ctx.execute_all(auto=True)
    finally:
        ctx.close()
    write_book(run_dir, ctx.book)
    write_summary(
        run_dir,
        _build_summary(round_file, mode, cases, ctx.records, started, root),
    )
    write_evidence(run_dir, _build_evidence(ctx.records))
    link_latest(runs_dir, run_dir)
    return run_dir


class SuiteRun:
    def __init__(
        self,
        *,
        suite: SuiteFile,
        round_file: RoundFile,
        root: Path,
        config: HarnessConfig,
        client: httpx.Client,
        run_dir: Path,
        secrets: dict[str, str],
        captures: dict[str, str] | None = None,
        pacer: dict[str, float] | None = None,
        browser_factory: BrowserFactory | None = None,
    ) -> None:
        require_flat_model(str(suite.catalog.get("pricing_model", "FLAT")))
        self.suite = suite
        self.round_file = round_file
        self.root = root
        self.config = config
        self.client = client
        self.run_dir = run_dir
        self.secrets = secrets
        self.captures = captures if captures is not None else {}
        self.pacer = pacer if pacer is not None else {}
        self.browser_factory = browser_factory
        self.book = Book()
        self.records: list[RoundStep] = []
        self.step_index = 1
        self.photos: dict[str, dict[str, Any]] = {}
        self.last_transaction_id = ""
        self._failed = False
        self._driver: UiDriver | None = None
        self._probes = {
            step.probe.id: step.probe for step in suite.steps if step.probe is not None
        }

    @property
    def stopped(self) -> bool:
        return self._failed

    def close(self) -> None:
        """Releases Chromium. Runs even when the suite aborted."""
        if self._driver is None:
            return
        try:
            self._driver.close()
        finally:
            self._driver = None

    def execute_all(self, *, auto: bool) -> None:
        for step in self.suite.steps:
            if self._failed:
                break
            if step.probe_begin is not None:
                self.snapshot_begin(step.probe_begin)
                continue
            if step.loop is not None:
                self.snapshot_missing()
                outcome = self.run_loop(step.loop, auto=auto)
                self.records.extend(outcome.records)
                if outcome.stop_suite:
                    self._failed = True
                continue
            if step.ui is not None:
                self.snapshot_missing()
                ui_outcome = self.run_ui(step.ui)
                self.records.append(ui_outcome.record)
                # A broken instrument stops the round; a product pack failure does
                # not, exactly like a failing step inside a loop.
                if ui_outcome.failed and not ui_outcome.verdict.get("continue", True):
                    self._failed = True
                continue
            if step.probe is not None:
                if self._failed:
                    break
                outcome = self.run_probe(step.probe, auto=auto)
                self.records.append(outcome.record)

    def snapshot_begin(self, probe_id: str) -> None:
        probe = self._probes.get(probe_id)
        if probe is None:
            return
        self.snapshot(probe)

    def snapshot_missing(self) -> None:
        for probe in self._probes.values():
            if probe.id not in self.photos:
                self.snapshot(probe)

    def snapshot(self, probe: ProbeSpec) -> dict[str, Any]:
        mapping = self._mapping()
        surfaces: dict[str, Any] = {}
        for surface in probe.surfaces:
            got = self._get_surface(surface, mapping)
            surfaces[surface.id] = {
                "jsonpath": surface.jsonpath,
                "value": _jsonable(got["value"]),
                "status": got["status"],
                "body": got["body"],
            }
        self.photos[probe.id] = surfaces
        write_probe(self.run_dir, probe.id, "before.json", surfaces)
        return surfaces

    def run_loop(self, loop: LoopSpec, *, auto: bool) -> LoopOutcome:
        case = load_case(resolve_path(self.root, loop.case))
        _case, contract, baseline = _prepare_case(
            case, self.root, self.secrets, runs_dir=self.run_dir.parent
        )
        records: list[RoundStep] = []
        last_dir: Path | None = None
        for _ in range(loop.times):
            iteration = _clone_for_iteration(_case, loop)
            result = execute_step(
                case=iteration,
                contract=contract,
                baseline=baseline,
                config=self.config,
                client=self.client,
                run_dir=self.run_dir,
                step_index=self.step_index,
                run_id=self.round_file.id,
                secrets=self.secrets,
                dimensions=self.round_file.dimensions,
                environment=self.round_file.environment,
                captures=self.captures,
                pacer=self.pacer,
            )
            last_dir = result.step_dir
            verdict = _auto_verdict(iteration, result)
            request = _step_request(result.step_dir)
            body = request.get("body") if isinstance(request.get("body"), dict) else {}
            idempotency = _header_ci(request.get("headers") or {}, "X-Idempotency-Key")
            poll_ok = True
            if loop.after_each and loop.after_each.poll:
                poll_ok = self._poll_and_book_ingest(
                    loop.after_each.poll,
                    body=body,
                    idempotency=idempotency,
                    http_status=result.status_code,
                )
                if not poll_ok:
                    verdict = {
                        "status": "fail",
                        "actor": "auto",
                        "comment": "ingest poll still PENDING",
                        "continue": False,
                    }
            else:
                self.book.add_metering(
                    status_code=result.status_code,
                    body=_step_response(result.step_dir),
                    amount=body.get("amount"),
                    idempotency_key=idempotency,
                )
            if auto:
                write_verdict(result.step_dir, verdict)
            records.append(_round_step(iteration.id, result, verdict))
            self.step_index += 1
            if not poll_ok:
                return LoopOutcome(records, last_dir, True, True)
        pack_failed = any(item.verdict == "fail" for item in records)
        return LoopOutcome(records, last_dir, False, pack_failed)

    def run_ui(self, step: UiStep) -> UiOutcome:
        """Drives one dashboard screen and records the A.13 evidence (A.19)."""
        outcome = execute_ui_step(
            step,
            run_dir=self.run_dir,
            root=self.root,
            step_index=self.step_index,
            run_id=self.round_file.id,
            config=self.config,
            client=self.client,
            driver_factory=self._driver_session,
            environment=self.round_file.environment,
        )
        self.step_index += 1
        return outcome

    def _driver_session(self) -> UiDriver:
        """One browser context per run: the dashboard JWT lives in memory only.

        Called by `execute_ui_step` *after* the dashboard preflight, so a dead
        dashboard never wakes Chromium up.
        """
        if self._driver is None:
            factory = self.browser_factory or default_browser_factory
            self._driver = factory(
                dashboard_url=self.config.nokr_dashboard,
                credentials=self._dashboard_credentials(),
                environment=self.round_file.environment,
            )
        return self._driver

    def _dashboard_credentials(self) -> dict[str, str]:
        credentials: dict[str, str] = {}
        for name in ("email", "password"):
            alias = _SECRET_CAPTURE_KEYS.get(name, name)
            credentials[name] = (
                _usable_secret(self.secrets, name)
                or self.captures.get(alias)
                or self.captures.get(name)
                or ""
            )
        return credentials

    def run_probe(self, probe: ProbeSpec, *, auto: bool) -> ProbeOutcome:
        if probe.id not in self.photos:
            self.snapshot(probe)
        started = perf_counter()
        evals, after, delta, oracle = self._compare_probe(probe)
        packs = values_packs(evals)
        elapsed_ms = (perf_counter() - started) * 1000.0
        probe_dir = write_probe(self.run_dir, probe.id, "after.json", after)
        write_probe(self.run_dir, probe.id, "delta.json", delta)
        write_probe(self.run_dir, probe.id, "oracle.json", oracle)
        write_probe(
            self.run_dir,
            probe.id,
            "packs.json",
            {"results": [asdict(item) for item in packs]},
        )
        write_probe(
            self.run_dir,
            probe.id,
            "timing.json",
            {"elapsed_ms": elapsed_ms},
        )
        failed = any(item.status == "fail" for item in packs)
        auto_verdict = {
            "status": "fail" if failed else "pass",
            "actor": "auto",
            "comment": _probe_comment(packs),
            "continue": True,
        }
        if auto:
            write_verdict(probe_dir, auto_verdict)
        record = RoundStep(
            case_id=f"probe {probe.id}",
            status_code=200,
            verdict=auto_verdict["status"],
            pack_fails=tuple(
                item.pack_id for item in packs if item.status == "fail"
            ),
            pack_warns=tuple(
                item.pack_id for item in packs if item.status == "warn"
            ),
            elapsed_ms=elapsed_ms,
            logs_incomplete=False,
        )
        return ProbeOutcome(record, probe_dir, auto_verdict, failed)

    def _compare_probe(
        self, probe: ProbeSpec
    ) -> tuple[list[SurfaceEval], dict[str, Any], dict[str, Any], dict[str, Any]]:
        oracle_total = self.book.included_total()
        mapping = self._mapping()
        before_doc = self.photos[probe.id]
        after_doc: dict[str, Any] = {}
        delta_doc: dict[str, Any] = {}
        evals: list[SurfaceEval] = []
        surfaces_ui: list[dict[str, Any]] = []
        for surface in probe.surfaces:
            before_value = before_doc.get(surface.id, {}).get("value")
            got, timed_out = self._wait_surface(
                surface, before_value, oracle_total, mapping
            )
            after_value = _jsonable(got["value"])
            expected = expected_after(surface, before_value, oracle_total)
            matched = surface_matches(surface, before_value, got["value"], oracle_total)
            after_doc[surface.id] = {
                "jsonpath": surface.jsonpath,
                "value": after_value,
                "status": got["status"],
                "body": got["body"],
                "timed_out": timed_out,
            }
            esperado = _esperado(surface, expected, before_value)
            lido = after_value
            delta_doc[surface.id] = {
                "before": before_value,
                "after": after_value,
                "esperado": esperado,
                "lido": lido,
                "timed_out": timed_out,
            }
            evals.append(
                SurfaceEval(
                    surface.id,
                    surface.jsonpath,
                    surface.expect,
                    before_value,
                    after_value,
                    esperado,
                    matched,
                    timed_out,
                )
            )
            surfaces_ui.append(
                {
                    "id": surface.id,
                    "jsonpath": surface.jsonpath,
                    "expect": surface.expect,
                    "before": before_value,
                    "after": after_value,
                    "delta": _delta_display(before_value, after_value),
                    "esperado": esperado,
                    "lido": lido,
                    "timed_out": timed_out,
                }
            )
        oracle_doc = {
            "catalog": self.suite.catalog,
            "book_total": str(oracle_total),
            "surfaces": surfaces_ui,
        }
        return evals, after_doc, delta_doc, oracle_doc

    def _wait_surface(
        self,
        surface: SurfaceSpec,
        before_value: Any,
        oracle_total: Any,
        mapping: dict[str, str],
    ) -> tuple[dict[str, Any], bool]:
        timeout_ms = _surface_timeout(surface, self.config)
        deadline = monotonic() + timeout_ms / 1000.0
        last = self._get_surface(surface, mapping)
        if surface_matches(surface, before_value, last["value"], oracle_total):
            return last, False
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return last, True
            sleep(min(_POLL_INTERVAL_S, remaining))
            last = self._get_surface(surface, mapping)
            if surface_matches(surface, before_value, last["value"], oracle_total):
                return last, False

    def _poll_and_book_ingest(
        self,
        poll: PollSpec,
        *,
        body: dict[str, Any],
        idempotency: str | None,
        http_status: int,
    ) -> bool:
        transaction_id = str(body.get("transaction_id") or "")
        self.last_transaction_id = transaction_id
        mapping = self._mapping()
        timeout_ms = poll.timeout_ms or self.config.probes.ingest_poll_ms
        url = _poll_url(poll, self.config, mapping)
        headers = _api_headers(self.secrets, self.round_file.environment)
        polled, ok = _poll_until(
            self.client, url, headers, poll.until_jsonpath, poll.until_not, timeout_ms
        )
        rating = lookup(polled or {}, poll.until_jsonpath)
        rating_status = None if rating is MISSING else str(rating)
        self.book.add_ingest(
            status_code=http_status,
            transaction_id=transaction_id or None,
            idempotency_key=idempotency,
            rating_status=rating_status,
            quantity=_quantity(body, self.suite.catalog),
            unit_amount=self.suite.catalog.get("unit_amount", "0"),
            flat_amount=self.suite.catalog.get("flat_amount", "0"),
        )
        return ok

    def _get_surface(
        self, surface: SurfaceSpec, mapping: dict[str, str]
    ) -> dict[str, Any]:
        path = interpolate(surface.get, mapping)
        url = path if str(path).startswith("http") else f"{self.config.nokr_web.rstrip('/')}{path}"
        headers = _surface_headers(str(path), self.secrets, self.round_file.environment)
        exchange = send(self.client, "GET", url, headers=headers)
        body = _response_body(exchange.response_text)
        value = lookup(body, surface.jsonpath) if isinstance(body, dict) else MISSING
        return {"status": exchange.status_code, "body": body, "value": value}

    def _mapping(self) -> dict[str, str]:
        return {**self.secrets, "transaction_id": self.last_transaction_id}


def _clone_for_iteration(case: CaseFile, loop: LoopSpec) -> CaseFile:
    generate = dict(case.generate or {})
    generate.update(loop.generate)
    return case.model_copy(update={"generate": generate})


def _poll_url(poll: PollSpec, config: HarnessConfig, mapping: dict[str, str]) -> str:
    path = poll.get
    if not path and poll.bru:
        path = _path_from_bru(poll.bru, config, mapping)
    path = interpolate(path or "/api/ingest/{{transaction_id}}", mapping)
    if path.startswith("http"):
        return path
    return f"{config.nokr_web.rstrip('/')}{path}"


def _path_from_bru(bru: str, config: HarnessConfig, mapping: dict[str, str]) -> str:
    path = Path(bru)
    if not path.is_absolute():
        path = Path(config.bruno_collection) / bru
    if not path.is_file():
        return "/api/ingest/{{transaction_id}}"
    parsed = parse_bru(path)
    url = parsed.url.replace("{{base_url}}", "")
    url = url.replace("{{transactionId}}", mapping.get("transaction_id", ""))
    return interpolate(url, mapping)


def _api_headers(secrets: dict[str, str], environment: str) -> dict[str, str]:
    headers = {"X-Nokr-Environment": environment}
    api_key = secrets.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _surface_headers(
    path: str, secrets: dict[str, str], environment: str
) -> dict[str, str]:
    headers = {"X-Nokr-Environment": environment}
    if "/platform/" in path or path.startswith("/platform/"):
        token = secrets.get("jwt")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers
    api_key = secrets.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _poll_until(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    jsonpath: str,
    until_not: str,
    timeout_ms: int,
) -> tuple[Any, bool]:
    deadline = monotonic() + timeout_ms / 1000.0
    last: Any = None
    while True:
        exchange = send(client, "GET", url, headers=headers)
        last = _response_body(exchange.response_text)
        got = lookup(last, jsonpath) if isinstance(last, dict) else MISSING
        if got is not MISSING and str(got) != until_not:
            return last, True
        remaining = deadline - monotonic()
        if remaining <= 0:
            return last, False
        sleep(min(_POLL_INTERVAL_S, remaining))


def _quantity(body: dict[str, Any], catalog: dict[str, Any]) -> Any:
    raw = str(catalog.get("property_path") or "properties.tokens")
    path = raw if raw.startswith("$") else f"$.{raw}"
    value = lookup(body, path)
    return 0 if value is MISSING else value


def _step_request(step_dir: Path) -> dict[str, Any]:
    payload = json.loads((step_dir / "request.json").read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _step_response(step_dir: Path) -> dict[str, Any] | None:
    payload = json.loads((step_dir / "response.json").read_text(encoding="utf-8"))
    body = payload.get("body") if isinstance(payload, dict) else None
    return body if isinstance(body, dict) else None


def _header_ci(headers: dict[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _surface_timeout(surface: SurfaceSpec, config: HarnessConfig) -> int:
    if surface.timeout_ms is not None:
        return surface.timeout_ms
    if surface.id == "overview" or "/dashboard/" in surface.get:
        return config.probes.overview_ms
    return config.probes.ledger_ms


def _jsonable(value: Any) -> Any:
    if value is MISSING:
        return None
    if hasattr(value, "as_tuple"):
        return str(value)
    return value


def _esperado(surface: SurfaceSpec, expected: Any, before: Any) -> Any:
    if surface.expect == "increase":
        return f"> {before}"
    return _jsonable(expected)


def _delta_display(before: Any, after: Any) -> Any:
    try:
        return str(to_decimal(after) - to_decimal(before))
    except Exception:
        return None


def _probe_comment(packs: list[PackResult]) -> str:
    fails = [item.pack_id for item in packs if item.status == "fail"]
    if not fails:
        return ""
    return f"pack {fails[0]} failed"
