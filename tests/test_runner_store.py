import json
from pathlib import Path

import httpx

from nokr_qa.config import HarnessConfig
from nokr_qa.config import LogFiles
from nokr_qa.runner import execute_step
from nokr_qa.run_store import read_shared_captures
from nokr_qa.schema.load import load_contract
from nokr_qa.schema.models import CaseFile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASELINE = json.loads(
    (FIXTURES / "baselines" / "ingest-sum.json").read_text(encoding="utf-8")
)
CONTRACT = load_contract(FIXTURES / "contracts" / "api-ingest-post.yaml")


def test_execute_step_writes_redacted_request_without_omitted_timestamp(tmp_path: Path):
    web_log = tmp_path / "nokr-web.log"
    worker_log = tmp_path / "nokr-worker.log"
    stale = (
        "2026-09-01 14:29:00 [vt] INFO  c.n.Foo [SANDBOX] - "
        "trace_id: [stale-trace] - old\n"
    )
    web_log.write_text(stale, encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "")
        body = json.loads(request.content.decode("utf-8"))
        assert "timestamp" not in body
        with web_log.open("a", encoding="utf-8") as handle:
            handle.write(
                "2026-09-01 14:30:00 [vt] INFO  c.n.Foo [SANDBOX] - "
                f"trace_id: [{trace}] - accepted\n"
            )
        return httpx.Response(
            202,
            json={"status": "ACCEPTED"},
            headers={"X-Trace-Id": trace, "Content-Type": "application/json"},
        )

    case = CaseFile.model_validate(
        {
            "id": "ingest-N-omit-timestamp",
            "contract": "contracts/api-ingest-post.yaml",
            "kind": "N-omit-timestamp",
            "diff": {"omit": ["timestamp"]},
            "expect": {"status": 202},
        }
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:8080",
    )
    run_dir = tmp_path / "runs" / "2026-09-01T1430-omit"
    result = execute_step(
        case=case,
        contract=CONTRACT,
        baseline=BASELINE,
        config=HarnessConfig(
            log_files=LogFiles(web=str(web_log), worker=str(worker_log)),
        ),
        client=client,
        run_dir=run_dir,
        step_index=3,
        run_id="omit",
        secrets={"api_key": "nk_test_secretvalue"},
        dimensions=[],
    )
    request_path = result.step_dir / "request.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    dumped = json.dumps(payload)
    assert "timestamp" not in payload.get("body", payload)
    assert "nk_test_secretvalue" not in dumped
    assert (result.step_dir / "packs.json").is_file()
    assert (result.step_dir / "response.json").is_file()
    assert (result.step_dir / "timing.json").is_file()
    assert result.step_dir.name == "003-ingest-N-omit-timestamp"
    logs_web = (result.step_dir / "logs-web.txt").read_text(encoding="utf-8")
    assert "nokrqa-omit-3" in logs_web
    assert "stale-trace" not in logs_web
    packs = json.loads((result.step_dir / "packs.json").read_text(encoding="utf-8"))
    assert "logs_incomplete" in packs
    assert (result.step_dir / "logs-worker.txt").is_file()


def test_read_shared_captures_ignores_stale_run_files(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    old_run = runs_dir / "2026-09-10T2009-feature-keys-post"
    old_run.mkdir(parents=True)
    (old_run / "captures.json").write_text(
        json.dumps({"feature_key": "gpt4o_access"}),
        encoding="utf-8",
    )
    (runs_dir / "shared-captures.json").write_text("{}", encoding="utf-8")
    assert read_shared_captures(runs_dir) == {}
