import json
from pathlib import Path

import httpx

from heimdall_qa.run_store import read_shared_captures
from heimdall_qa.runner import execute_step
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DESCRIPTOR = FIXTURES / "qa" / "project.yaml"
_PROJECT = project_at(_DESCRIPTOR)
BASELINE = json.loads(
    (FIXTURES / "baselines" / "ingest-sum.json").read_text(encoding="utf-8")
)
CONTRACT = load_contract(FIXTURES / "contracts" / "api-ingest-post.yaml")


def test_execute_step_writes_redacted_request_without_omitted_timestamp(tmp_path: Path):
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
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
        config=config_for(
            project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))
        ),
        client=client,
        run_dir=run_dir,
        step_index=3,
        run_id="omit",
        secrets={"api_key": "test_key_secretvalue"},
        dimensions=[],
    )
    request_path = result.step_dir / "request.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    dumped = json.dumps(payload)
    assert "timestamp" not in payload.get("body", payload)
    assert "test_key_secretvalue" not in dumped
    assert (result.step_dir / "packs.json").is_file()
    assert (result.step_dir / "response.json").is_file()
    assert (result.step_dir / "timing.json").is_file()
    assert result.step_dir.name == "003-ingest-N-omit-timestamp"
    logs_web = (result.step_dir / "logs-web.txt").read_text(encoding="utf-8")
    # The prefix is the project's, so the test does not pin a product's name.
    assert f"{_PROJECT.trace_prefix()}omit-3" in logs_web
    assert "stale-trace" not in logs_web
    packs = json.loads((result.step_dir / "packs.json").read_text(encoding="utf-8"))
    # The step accepted the work (202) on an endpoint declared `async: worker`, so
    # the consumer owes a line for this trace — and this run's worker file is the
    # empty one it started with. Before the wait was fixed this said `false`.
    assert packs["logs_incomplete"] is True
    assert (result.step_dir / "logs-worker.txt").is_file()


def test_every_declared_source_gets_its_own_evidence_file(tmp_path: Path):
    """`admin` was declared in the descriptor and had no file to be read from.

    The run directory is the API: a source that is read but not written is
    indistinguishable, to whoever reads a run afterwards, from one never read.
    """
    web_log = tmp_path / "web.log"
    worker_log = tmp_path / "worker.log"
    web_log.write_text("", encoding="utf-8")
    worker_log.write_text("", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.headers.get("x-trace-id", "")
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
            "id": "ingest-H01",
            "contract": "contracts/api-ingest-post.yaml",
            "kind": "H01",
            "expect": {"status": 202},
        }
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8080"
    )
    result = execute_step(
        case=case,
        contract=CONTRACT,
        baseline=BASELINE,
        config=config_for(
            project_at(_DESCRIPTOR, web=str(web_log), worker=str(worker_log))
        ),
        client=client,
        run_dir=tmp_path / "runs" / "2026-09-01T1430-sources",
        step_index=0,
        run_id="sources",
        dimensions=[],
    )

    evidence = json.loads((result.step_dir / "logs.json").read_text(encoding="utf-8"))
    assert [source["id"] for source in evidence["sources"]] == ["web", "worker", "admin"]
    for source in evidence["sources"]:
        assert (result.step_dir / f"logs-{source['id']}.txt").is_file()
    by_id = {source["id"]: source for source in evidence["sources"]}
    assert by_id["web"]["found"] is True
    assert by_id["web"]["reason"] == "found"
    # Accepted work on an async endpoint: the consumer owes a line and this run's
    # worker file is empty, so the gap is reported — by the source that owes it.
    assert by_id["worker"]["found"] is False
    assert by_id["worker"]["reason"] == "marker_not_found"
    assert by_id["worker"]["role"] == "async"
    assert evidence["incomplete"] is True
    # Declared and not instrumented: reported, and not counted as a gap.
    assert by_id["admin"]["propagate"] is False
    assert evidence["timeline"][0]["source"] == "web"


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
