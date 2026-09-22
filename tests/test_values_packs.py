from pathlib import Path
import json

import httpx
import pytest
import yaml

from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.runner import execute_round

from test_loop_probe import _config
from test_loop_probe import _secrets
from test_loop_probe import _write_values_tree


def _values_secrets() -> dict[str, str]:
    return {**_secrets(), "jwt": "platform-jwt-token"}


def _add_probe(
    tmp_path: Path,
    *,
    probe_begin: bool = True,
    surfaces: list[dict] | None = None,
) -> None:
    suite_path = tmp_path / "suites" / "values.yaml"
    suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    default_surfaces = surfaces or [
        {
            "id": "wallet",
            "get": "/api/users/{{external_user_id}}/balance",
            "jsonpath": "$.balance",
            "expect": "exact",
            "timeout_ms": 200,
        },
        {
            "id": "customer_tx",
            "get": "/platform/tenants/users/{{nokr_user_id}}/transactions",
            "jsonpath": "$.page.total_elements",
            "expect": "increase",
            "timeout_ms": 200,
        },
        {
            "id": "customer_metrics",
            "get": "/platform/tenants/users/{{nokr_user_id}}/metrics",
            "jsonpath": "$.total_billed_lifetime",
            "expect": "exact",
            "timeout_ms": 200,
        },
        {
            "id": "overview",
            "get": "/platform/dashboard/metrics",
            "jsonpath": "$.conversion.test_drive_consumed_percent",
            "expect": "increase",
            "timeout_ms": 200,
        },
        {
            "id": "cashout",
            "get": "/platform/tenants/cashout/limits",
            "jsonpath": "$.max_withdrawal",
            "expect": "unchanged",
            "timeout_ms": 200,
        },
    ]
    steps = []
    if probe_begin:
        steps.append({"probe_begin": "values-10m-7i"})
    steps.extend(suite.get("steps") or [])
    steps.append(
        {
            "probe": {
                "id": "values-10m-7i",
                "oracle": {
                    "catalog": "session",
                    "metering": "body_amount",
                    "ingest": "flat_session",
                },
                "surfaces": default_surfaces,
                "exclude": ["http_402", "ingest_insufficient", "replay", "http_429"],
            }
        }
    )
    suite["steps"] = steps
    suite_path.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)


def _stateful_handler(
    *,
    wallet_after: str,
    metrics_after: str,
    tx_after: int,
    overview_after: str,
    cashout: str = "100.00",
    wallet_before: str = "1.00000",
    metrics_before: str = "0.00000",
    tx_before: int = 0,
    overview_before: str = "1.0",
):
    seen_mutation = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "t")
        path = request.url.path
        headers = {"X-Trace-Id": trace, "Content-Type": "application/json"}
        after = seen_mutation["done"]
        if request.method == "POST" and path.endswith("/api/metering"):
            seen_mutation["done"] = True
            return httpx.Response(
                202,
                json={"status": "SUCCESS", "balance_remaining": wallet_after},
                headers=headers,
            )
        if request.method == "POST" and path.endswith("/api/ingest"):
            seen_mutation["done"] = True
            return httpx.Response(202, json={"status": "ACCEPTED"}, headers=headers)
        if request.method == "GET" and "/api/ingest/" in path:
            return httpx.Response(
                200,
                json={"quantities": [{"rating": {"status": "SUCCESS", "amount": "0.00300"}}]},
                headers=headers,
            )
        if path.endswith("/balance"):
            value = wallet_after if after else wallet_before
            return httpx.Response(200, json={"balance": value}, headers=headers)
        if path.endswith("/transactions"):
            total = tx_after if after else tx_before
            return httpx.Response(200, json={"page": {"total_elements": total}}, headers=headers)
        if "/dashboard/metrics" in path:
            value = overview_after if after else overview_before
            return httpx.Response(
                200,
                json={"conversion": {"test_drive_consumed_percent": value}},
                headers=headers,
            )
        if path.endswith("/metrics"):
            value = metrics_after if after else metrics_before
            return httpx.Response(
                200, json={"total_billed_lifetime": value}, headers=headers
            )
        if path.endswith("/cashout/limits"):
            return httpx.Response(200, json={"max_withdrawal": cashout}, headers=headers)
        return httpx.Response(404, json={"error": "nope", "traceId": trace}, headers=headers)

    return handler


def _run(tmp_path: Path, handler) -> Path:
    round_path = tmp_path / "round.yaml"
    return execute_round(
        round_path,
        root=tmp_path,
        config=_config(tmp_path),
        client=_client(handler),
        runs_dir=tmp_path / "runs",
        secrets=_values_secrets(),
    )


def test_probe_happy_path_writes_artifacts_and_passes_packs(tmp_path: Path):
    _write_values_tree(tmp_path, metering_times=1, ingest_times=1)
    _add_probe(tmp_path)
    # 0.01000 metering + 0.00300 ingest
    run_dir = _run(
        tmp_path,
        _stateful_handler(
            wallet_after="0.98700",
            metrics_after="0.01300",
            tx_after=2,
            overview_after="2.5",
        ),
    )
    probe_dir = run_dir / "probes" / "values-10m-7i"
    assert (probe_dir / "before.json").is_file()
    assert (probe_dir / "after.json").is_file()
    assert (probe_dir / "delta.json").is_file()
    oracle = json.loads((probe_dir / "oracle.json").read_text(encoding="utf-8"))
    assert oracle["book_total"] == "0.01300"
    packs = json.loads((probe_dir / "packs.json").read_text(encoding="utf-8"))
    by_id = {item["pack_id"]: item["status"] for item in packs["results"]}
    assert by_id["values.oracle"] == "pass"
    assert by_id["values.consistency"] == "pass"
    metering_packs = json.loads(
        next(run_dir.glob("steps/*-metering-H01")).joinpath("packs.json").read_text(
            encoding="utf-8"
        )
    )
    http_ids = {item["pack_id"] for item in metering_packs["results"]}
    assert "values.oracle" not in http_ids
    assert "values.consistency" not in http_ids


def test_wallet_delta_00301_fails_values_oracle(tmp_path: Path):
    _write_values_tree(tmp_path, metering_times=0, ingest_times=1)
    _add_probe(
        tmp_path,
        surfaces=[
            {
                "id": "wallet",
                "get": "/api/users/{{external_user_id}}/balance",
                "jsonpath": "$.balance",
                "expect": "exact",
                "timeout_ms": 50,
            }
        ],
    )
    run_dir = _run(
        tmp_path,
        _stateful_handler(
            wallet_after="0.99699",
            metrics_after="0.00300",
            tx_after=1,
            overview_after="2.0",
        ),
    )
    packs = json.loads(
        (run_dir / "probes" / "values-10m-7i" / "packs.json").read_text(encoding="utf-8")
    )
    by_id = {item["pack_id"]: item for item in packs["results"]}
    assert by_id["values.oracle"]["status"] == "fail"
    oracle = json.loads(
        (run_dir / "probes" / "values-10m-7i" / "oracle.json").read_text(encoding="utf-8")
    )
    wallet = oracle["surfaces"][0]
    assert wallet["esperado"] in {"0.99700", "0.9970"}
    assert wallet["lido"] == "0.99699"


def test_overview_timeout_fails_consistency_not_skip(tmp_path: Path):
    _write_values_tree(tmp_path, metering_times=0, ingest_times=1)
    _add_probe(
        tmp_path,
        surfaces=[
            {
                "id": "wallet",
                "get": "/api/users/{{external_user_id}}/balance",
                "jsonpath": "$.balance",
                "expect": "exact",
                "timeout_ms": 50,
            },
            {
                "id": "overview",
                "get": "/platform/dashboard/metrics",
                "jsonpath": "$.conversion.test_drive_consumed_percent",
                "expect": "increase",
                "timeout_ms": 50,
            },
            {
                "id": "cashout",
                "get": "/platform/tenants/cashout/limits",
                "jsonpath": "$.max_withdrawal",
                "expect": "unchanged",
                "timeout_ms": 50,
            },
        ],
    )
    run_dir = _run(
        tmp_path,
        _stateful_handler(
            wallet_after="0.99700",
            metrics_after="0.00300",
            tx_after=1,
            overview_after="1.0",
            overview_before="1.0",
        ),
    )
    packs = json.loads(
        (run_dir / "probes" / "values-10m-7i" / "packs.json").read_text(encoding="utf-8")
    )
    by_id = {item["pack_id"]: item for item in packs["results"]}
    assert by_id["values.oracle"]["status"] == "pass"
    assert by_id["values.consistency"]["status"] == "fail"
    assert by_id["values.consistency"]["status"] != "skipped"
    assert "overview" in by_id["values.consistency"]["detail"]


def test_unchanged_max_withdrawal_passes(tmp_path: Path):
    _write_values_tree(tmp_path, metering_times=0, ingest_times=1)
    _add_probe(
        tmp_path,
        surfaces=[
            {
                "id": "cashout",
                "get": "/platform/tenants/cashout/limits",
                "jsonpath": "$.max_withdrawal",
                "expect": "unchanged",
                "timeout_ms": 50,
            }
        ],
    )
    run_dir = _run(
        tmp_path,
        _stateful_handler(
            wallet_after="0.99700",
            metrics_after="0.00300",
            tx_after=1,
            overview_after="2.0",
        ),
    )
    packs = json.loads(
        (run_dir / "probes" / "values-10m-7i" / "packs.json").read_text(encoding="utf-8")
    )
    by_id = {item["pack_id"]: item["status"] for item in packs["results"]}
    assert by_id["values.consistency"] == "pass"


def test_implicit_begin_writes_before_without_probe_begin(tmp_path: Path):
    _write_values_tree(tmp_path, metering_times=0, ingest_times=1)
    _add_probe(tmp_path, probe_begin=False, surfaces=[
        {
            "id": "wallet",
            "get": "/api/users/{{external_user_id}}/balance",
            "jsonpath": "$.balance",
            "expect": "exact",
            "timeout_ms": 50,
        }
    ])
    run_dir = _run(
        tmp_path,
        _stateful_handler(
            wallet_after="0.99700",
            metrics_after="0.00300",
            tx_after=1,
            overview_after="2.0",
        ),
    )
    assert (run_dir / "probes" / "values-10m-7i" / "before.json").is_file()


def test_poll_pending_skips_probe(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "t")
        headers = {"X-Trace-Id": trace, "Content-Type": "application/json"}
        if request.method == "POST" and request.url.path.endswith("/api/ingest"):
            return httpx.Response(202, json={"status": "ACCEPTED"}, headers=headers)
        if request.method == "GET" and "/api/ingest/" in request.url.path:
            return httpx.Response(
                200,
                json={"quantities": [{"rating": {"status": "PENDING"}}]},
                headers=headers,
            )
        if request.url.path.endswith("/balance"):
            return httpx.Response(200, json={"balance": "1.00000"}, headers=headers)
        return httpx.Response(404, json={"error": "nope", "traceId": trace}, headers=headers)

    _write_values_tree(tmp_path, metering_times=0, ingest_times=1, poll_timeout_ms=50)
    _add_probe(
        tmp_path,
        surfaces=[
            {
                "id": "wallet",
                "get": "/api/users/{{external_user_id}}/balance",
                "jsonpath": "$.balance",
                "expect": "exact",
            }
        ],
    )
    run_dir = _run(tmp_path, handler)
    probe_dir = run_dir / "probes" / "values-10m-7i"
    assert (probe_dir / "before.json").is_file()
    assert not (probe_dir / "after.json").is_file()
    assert not (probe_dir / "oracle.json").is_file()


@pytest.mark.slow
def test_live_values_round_is_human_gate():
    pytest.skip("live gate: nokr-qa serve rounds/values-10m-7i.yaml with web+worker+ClickHouse")
