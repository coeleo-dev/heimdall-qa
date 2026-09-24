from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from time import perf_counter
from time import sleep
from typing import Any
from urllib.parse import urlparse

import httpx

from heimdall_qa.bru_parser import parse_bru
from heimdall_qa.config import HarnessConfig
from heimdall_qa.errors import HarnessError
from heimdall_qa.http_client import send
from heimdall_qa.jsonpath import MISSING
from heimdall_qa.jsonpath import lookup
from heimdall_qa.money import to_decimal
from heimdall_qa.packs import PackResult
from heimdall_qa.packs.values import SurfaceEval
from heimdall_qa.packs.values import values_packs
from heimdall_qa.project import ProjectView
from heimdall_qa.run_store import create_run
from heimdall_qa.run_store import link_latest
from heimdall_qa.run_store import write_book
from heimdall_qa.run_store import write_evidence
from heimdall_qa.run_store import write_probe
from heimdall_qa.run_store import write_summary
from heimdall_qa.run_store import write_verdict
from heimdall_qa.runner import _AUTH_SECRET_KEYS
from heimdall_qa.runner import RoundStep
from heimdall_qa.runner import _auto_verdict
from heimdall_qa.runner import _build_evidence
from heimdall_qa.runner import _build_summary
from heimdall_qa.runner import _load_included_cases
from heimdall_qa.runner import _prepare_case
from heimdall_qa.runner import _reject_placeholders
from heimdall_qa.runner import _response_body
from heimdall_qa.runner import _round_step
from heimdall_qa.runner import execute_step
from heimdall_qa.runner import interpolate
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import split_selector
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import LoopSpec
from heimdall_qa.schema.models import PollSpec
from heimdall_qa.schema.models import ProbeSpec
from heimdall_qa.schema.models import RoundFile
from heimdall_qa.schema.models import SuiteFile
from heimdall_qa.schema.models import SurfaceSpec
from heimdall_qa.validate import resolve_path

_POLL_INTERVAL_S = 0.25


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
    """What a suite step calls itself in the queue.

    The family comes from the **case id**, not from the path: with a selector the
    path is `cases/metering.yaml#metering-H01` and its stem is the file's name, so
    a label read off the path says `loop metering.yaml#metering ×10` — wrong
    without failing, which is the worst kind of wrong. A path with no selector
    names a file that holds one case, and its stem is still the family.
    """
    path, case_id = split_selector(loop.case)
    family = (case_id or Path(path).stem).split("-")[0]
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
    descriptor: dict[str, str] | None = None,
) -> Path:
    cases = _load_included_cases(round_file, root, config.project)
    _reject_placeholders(cases)
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
    )
    ctx.execute_all(auto=True)
    write_book(run_dir, ctx.oracle)
    write_summary(
        run_dir,
        _build_summary(
            round_file,
            mode,
            cases,
            ctx.records,
            started,
            root,
            project=config.project,
            descriptor=descriptor,
        ),
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
    ) -> None:
        self.suite = suite
        self.round_file = round_file
        self.root = root
        self.config = config
        self.client = client
        self.run_dir = run_dir
        self.secrets = secrets
        self.captures = captures if captures is not None else {}
        self.pacer = pacer if pacer is not None else {}
        self.oracle = config.project.oracle()
        self.oracle.require_pricing_model(_pricing_model(suite))
        self.records: list[RoundStep] = []
        self.step_index = 1
        self.photos: dict[str, dict[str, Any]] = {}
        self.last_transaction_id = ""
        self._failed = False
        self._probes = {
            step.probe.id: step.probe for step in suite.steps if step.probe is not None
        }

    @property
    def stopped(self) -> bool:
        return self._failed

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
        project = self.config.project
        relative, case_id = split_selector(loop.case)
        case = load_case(resolve_path(self.root, relative, project), case_id)
        _case, contract, baseline = _prepare_case(
            case,
            self.root,
            self.secrets,
            project=project,
            runs_dir=self.run_dir.parent,
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
                self.oracle.add_metering(
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
        oracle_total = self.oracle.included_total()
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
            expected = self.oracle.expected_after(surface, before_value, oracle_total)
            matched = self.oracle.matches(
                surface, before_value, got["value"], oracle_total
            )
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
        if self.oracle.matches(surface, before_value, last["value"], oracle_total):
            return last, False
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return last, True
            sleep(min(_POLL_INTERVAL_S, remaining))
            last = self._get_surface(surface, mapping)
            if self.oracle.matches(surface, before_value, last["value"], oracle_total):
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
        url = _poll_url(poll, self.config, mapping, self.round_file.environment)
        headers = _probe_headers(
            url, self.secrets, self.round_file.environment, self.config.project
        )
        polled, ok = _poll_until(
            self.client, url, headers, poll.until_jsonpath, poll.until_not, timeout_ms
        )
        rating = lookup(polled or {}, poll.until_jsonpath)
        rating_status = None if rating is MISSING else str(rating)
        self.oracle.add_ingest(
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
        url = self.config.project.url(str(path), self.round_file.environment)
        headers = _probe_headers(
            str(path),
            self.secrets,
            self.round_file.environment,
            self.config.project,
        )
        exchange = send(self.client, "GET", url, headers=headers)
        body = _response_body(exchange.response_text)
        value = lookup(body, surface.jsonpath) if isinstance(body, dict) else MISSING
        return {"status": exchange.status_code, "body": body, "value": value}

    def _mapping(self) -> dict[str, str]:
        return {**self.secrets, "transaction_id": self.last_transaction_id}


def _pricing_model(suite: SuiteFile) -> str:
    """The model the suite bills under. Absent means the safe, boring answer."""
    return str(suite.catalog.get("pricing_model", "FLAT"))


def _clone_for_iteration(case: CaseFile, loop: LoopSpec) -> CaseFile:
    generate = dict(case.generate or {})
    generate.update(loop.generate)
    return case.model_copy(update={"generate": generate})


def _poll_url(
    poll: PollSpec,
    config: HarnessConfig,
    mapping: dict[str, str],
    environment: str,
) -> str:
    path = poll.get or ""
    if not path and poll.bru:
        path = _path_from_bru(poll.bru, config, mapping)
    if not path:
        raise HarnessError(
            code="POLL_TARGET_MISSING",
            message="an after_each poll declares neither `get` nor `bru`",
            hint="declare the url the loop polls, e.g. get: /api/ingest/{{transaction_id}}",
        )
    path = interpolate(path, mapping)
    if str(path).startswith("http"):
        return str(path)
    return config.project.url(str(path), environment)


def _path_from_bru(bru: str, config: HarnessConfig, mapping: dict[str, str]) -> str:
    path = Path(bru)
    if not path.is_absolute():
        path = config.project.required_request_collection() / bru
    if not path.is_file():
        raise HarnessError(
            code="BRU_MISSING",
            message=f"poll references a .bru file that does not exist: {path}",
            hint="fix the after_each.poll.bru path, or declare `get:` instead",
        )
    parsed = parse_bru(path)
    url = parsed.url.replace("{{base_url}}", "")
    url = url.replace("{{transactionId}}", mapping.get("transaction_id", ""))
    return interpolate(url, mapping)


def _probe_headers(
    target: str,
    secrets: dict[str, str],
    environment: str,
    project: ProjectView,
) -> dict[str, str]:
    """The headers a probe carries, taken from the credential its route requires.

    A poll reads the same policy a case does: `routes[].auth` names the
    credential, so the harness never has to be told "polls use the API key".
    """
    name = project.auth_name_for(urlparse(target).path)
    if name is None:
        return _environment_header(environment, project)
    return _credential_headers(name, secrets, environment, project)


def _credential_headers(
    auth_name: str,
    secrets: dict[str, str],
    environment: str,
    project: ProjectView,
) -> dict[str, str]:
    headers = _environment_header(environment, project)
    scheme = project.auth_named(auth_name)
    credential = secrets.get(_AUTH_SECRET_KEYS.get(auth_name, auth_name))
    if scheme is None or not credential or scheme.scheme not in {"bearer", "raw"}:
        return headers
    headers[scheme.header] = (
        f"Bearer {credential}" if scheme.scheme == "bearer" else credential
    )
    return headers


def _environment_header(environment: str, project: ProjectView) -> dict[str, str]:
    name = project.environment_header_name()
    if name is None:
        return {}
    return {name: project.environment_value(environment)}


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
