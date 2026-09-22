from pathlib import Path
import json

import httpx
import yaml

from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.jsonpath import lookup
from nokr_qa.runner import execute_round

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_jsonpath_nested_index_and_object():
    body = {
        "quantities": [{"rating": {"status": "SUCCESS", "amount": 0.003}}],
        "conversion": {"test_drive_consumed_percent": 12.5},
    }
    assert lookup(body, "$.quantities[0].rating.status") == "SUCCESS"
    assert lookup(body, "$.conversion.test_drive_consumed_percent") == 12.5


def _write_values_tree(
    tmp_path: Path,
    *,
    metering_times: int = 10,
    ingest_times: int = 7,
    poll_timeout_ms: int = 8000,
    ingest_generate: dict | None = None,
    ingest_set: dict | None = None,
) -> Path:
    for folder in ("cases/metering", "cases/ingest", "contracts", "baselines", "suites"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "contracts" / "api-metering-post.yaml").write_text(
        "\n".join(
            [
                "endpoint: POST /api/metering",
                "dto: com.nokr.domain.metering.human.dto.MeteringRequest",
                "auth: api_key",
                "idempotency: header_uuid_v4",
                "baseline: baselines/metering.json",
                "fields:",
                "  nokr_user_id:",
                "    required: true",
                "    json: nokr_user_id",
                "  amount:",
                "    required: true",
                "    json: amount",
                "  description:",
                "    required: true",
                "    json: description",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "baselines" / "metering.json").write_text(
        json.dumps(
            {
                "nokr_user_id": "{{nokr_user_id}}",
                "amount": 0.01,
                "description": "qa-values",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "cases" / "metering" / "metering-H01.yaml").write_text(
        "\n".join(
            [
                "id: metering-H01",
                "contract: contracts/api-metering-post.yaml",
                "kind: H01",
                "tags: [function]",
                "expect:",
                "  status: 202",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "contracts" / "api-ingest-post.yaml").write_text(
        (FIXTURES / "contracts" / "api-ingest-post.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "baselines" / "ingest-sum.json").write_text(
        (FIXTURES / "baselines" / "ingest-sum.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    ingest_case = {
        "id": "ingest-H01",
        "contract": "contracts/api-ingest-post.yaml",
        "kind": "H01",
        "tags": ["function"],
        "expect": {"status": 202},
    }
    if ingest_set:
        ingest_case["diff"] = {"set": ingest_set}
    (tmp_path / "cases" / "ingest" / "ingest-H01.yaml").write_text(
        yaml.safe_dump(ingest_case),
        encoding="utf-8",
    )
    generate = ingest_generate or {
        "transaction_id": "uuid",
        "idempotency_key": "uuid_v4",
        "timestamp": "now_iso",
    }
    steps: list[dict] = []
    if metering_times >= 1:
        steps.append(
            {
                "loop": {
                    "times": metering_times,
                    "case": "cases/metering/metering-H01.yaml",
                    "generate": {"idempotency_key": "uuid_v4"},
                }
            }
        )
    if ingest_times >= 1:
        steps.append(
            {
                "loop": {
                    "times": ingest_times,
                    "case": "cases/ingest/ingest-H01.yaml",
                    "generate": generate,
                    "after_each": {
                        "poll": {
                            "get": "/api/ingest/{{transaction_id}}",
                            "until_jsonpath": "$.quantities[0].rating.status",
                            "until_not": "PENDING",
                            "timeout_ms": poll_timeout_ms,
                        }
                    },
                }
            }
        )
    suite = {
        "id": "values-10m-7i",
        "catalog": {
            "pricing_model": "FLAT",
            "unit_amount": "0.00003",
            "flat_amount": "0",
            "property_path": "properties.tokens",
        },
        "steps": steps,
    }
    (tmp_path / "suites" / "values.yaml").write_text(
        yaml.safe_dump(suite, sort_keys=False),
        encoding="utf-8",
    )
    round_path = tmp_path / "round.yaml"
    round_path.write_text(
        "\n".join(
            [
                "id: values-10m-7i",
                "suite: suites/values.yaml",
                "mode: headless",
                "environment: sandbox",
                "include:",
                "  - cases/metering/metering-H01.yaml",
                "  - cases/ingest/ingest-H01.yaml",
            ]
        ),
        encoding="utf-8",
    )
    return round_path


def _config(tmp_path: Path) -> HarnessConfig:
    web_log = tmp_path / "nokr-web.log"
    worker_log = tmp_path / "nokr-worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")
    return HarnessConfig(log_files=LogFiles(web=str(web_log), worker=str(worker_log)))


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)


def _secrets() -> dict[str, str]:
    return {
        "api_key": "nk_test_secret",
        "nokr_user_id": "11111111-1111-4111-8111-111111111111",
        "external_user_id": "ext-ta-001",
    }


def _happy_handler(request: httpx.Request) -> httpx.Response:
    trace = request.headers.get("x-trace-id", "missing")
    path = request.url.path
    if request.method == "POST" and path.endswith("/api/metering"):
        return httpx.Response(
            202,
            json={"status": "SUCCESS", "balance_remaining": 4.99},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )
    if request.method == "POST" and path.endswith("/api/ingest"):
        return httpx.Response(
            202,
            json={"status": "ACCEPTED"},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )
    if request.method == "GET" and "/api/ingest/" in path:
        return httpx.Response(
            200,
            json={"quantities": [{"rating": {"status": "SUCCESS", "amount": 0.003}}]},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )
    return httpx.Response(404, json={"error": "nope", "traceId": trace})


def test_loop_10_metering_7_ingest_writes_17_included_book_lines(tmp_path: Path):
    round_path = _write_values_tree(tmp_path)
    run_dir = execute_round(
        round_path,
        root=tmp_path,
        config=_config(tmp_path),
        client=_client(_happy_handler),
        runs_dir=tmp_path / "runs",
        secrets=_secrets(),
    )
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    assert len(book["included"]) == 17
    assert book["excluded"] == []
    assert not (run_dir / "probes").exists()


def test_metering_402_is_excluded_from_book(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/api/metering"):
            return httpx.Response(
                402,
                json={"error": "insufficient", "traceId": "t", "code": "INSUFFICIENT_BALANCE"},
                headers={"X-Trace-Id": "t", "Content-Type": "application/json"},
            )
        return _happy_handler(request)

    round_path = _write_values_tree(tmp_path, metering_times=1, ingest_times=0)
    run_dir = execute_round(
        round_path,
        root=tmp_path,
        config=_config(tmp_path),
        client=_client(handler),
        runs_dir=tmp_path / "runs",
        secrets=_secrets(),
    )
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    assert book["included"] == []
    assert book["excluded"][0]["exclusion"] == "http_402"


def test_ingest_insufficient_is_excluded_from_book(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/api/ingest/" in request.url.path:
            return httpx.Response(
                200,
                json={"quantities": [{"rating": {"status": "INSUFFICIENT"}}]},
                headers={"X-Trace-Id": "t", "Content-Type": "application/json"},
            )
        return _happy_handler(request)

    round_path = _write_values_tree(tmp_path, metering_times=0, ingest_times=1)
    run_dir = execute_round(
        round_path,
        root=tmp_path,
        config=_config(tmp_path),
        client=_client(handler),
        runs_dir=tmp_path / "runs",
        secrets=_secrets(),
    )
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    assert book["included"] == []
    assert book["excluded"][0]["exclusion"] == "ingest_insufficient"


def test_replay_same_transaction_id_does_not_sum_twice(tmp_path: Path):
    round_path = _write_values_tree(
        tmp_path,
        metering_times=0,
        ingest_times=2,
        ingest_generate={
            "idempotency_key": "uuid_v4",
            "timestamp": "now_iso",
        },
        ingest_set={"transaction_id": "evt-replay-1"},
    )
    run_dir = execute_round(
        round_path,
        root=tmp_path,
        config=_config(tmp_path),
        client=_client(_happy_handler),
        runs_dir=tmp_path / "runs",
        secrets=_secrets(),
    )
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    assert len(book["included"]) == 1
    assert book["excluded"][0]["exclusion"] == "replay"


def test_pending_poll_fails_before_probes_dir(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/api/ingest/" in request.url.path:
            return httpx.Response(
                200,
                json={"quantities": [{"rating": {"status": "PENDING"}}]},
                headers={"X-Trace-Id": "t", "Content-Type": "application/json"},
            )
        return _happy_handler(request)

    round_path = _write_values_tree(
        tmp_path, metering_times=0, ingest_times=1, poll_timeout_ms=50
    )
    run_dir = execute_round(
        round_path,
        root=tmp_path,
        config=_config(tmp_path),
        client=_client(handler),
        runs_dir=tmp_path / "runs",
        secrets=_secrets(),
    )
    assert not (run_dir / "probes").exists()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["fail"] >= 1
