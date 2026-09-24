"""The entry ramp, pinned: `examples/toy-provider/` must keep running.

Fase 1.6's gate is a shell procedure against a clean wheel, and a shell procedure
a human runs once rots silently. This test is the same claim in a form CI can
repeat: a project that declares no auth, no routes, no log sources and no provider
runs end to end, and the packs that need a log line report *not measured* instead
of a failure the project never had.

The mock here is the example's three routes, not a copy of the harness's own
fixtures, so a change to the example is a change this test sees.
"""

import json
from pathlib import Path

import httpx

from heimdall_qa.runner import execute_round
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at

#: The example, run in place. It is the artifact a stranger would start from.
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "toy-provider"
DESCRIPTOR = EXAMPLE / "qa" / "project.yaml"
ROUND = EXAMPLE / "rounds" / "smoke.yaml"


def _toy(request: httpx.Request) -> httpx.Response:
    """`app.py` over a mock transport: same routes, no server to babysit."""
    trace = request.headers.get("x-trace-id", "missing")
    headers = {"X-Trace-Id": trace, "Content-Type": "application/json"}
    path = request.url.path
    if request.method == "GET" and path == "/toy/health":
        return httpx.Response(200, json={"status": "ok"}, headers=headers)
    if request.method == "GET" and path == "/toy/items":
        return httpx.Response(
            200, json={"items": [{"id": "itm_1"}], "total": 1}, headers=headers
        )
    if request.method == "POST" and path == "/toy/items":
        return httpx.Response(201, json={"id": "itm_3", "name": "widget"}, headers=headers)
    return httpx.Response(404, json={"detail": "Not Found"}, headers=headers)


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_toy), timeout=10.0)


def _run(tmp_path: Path) -> Path:
    return execute_round(
        ROUND,
        root=EXAMPLE,
        config=config_for(project_at(DESCRIPTOR)),
        client=_client(),
        runs_dir=tmp_path / "runs",
        mode="headless",
        descriptor={"origin": "target", "path": str(DESCRIPTOR)},
    )


def test_the_example_round_passes_without_a_provider(tmp_path: Path):
    run_dir = _run(tmp_path)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    assert summary["counts"]["pass"] == 3
    assert summary["counts"]["fail"] == 0
    assert summary["descriptor"]["origin"] == "target"
    assert summary["descriptor"]["path"] == str(DESCRIPTOR)


def test_the_example_declares_no_logs_and_is_not_scolded_for_it(tmp_path: Path):
    """An absent log source is unmeasured, never incomplete."""
    run_dir = _run(tmp_path)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    assert summary["logs_incomplete"] == 0

    step = next((run_dir / "steps").iterdir())
    packs = json.loads((step / "packs.json").read_text(encoding="utf-8"))
    by_id = {item["pack_id"]: item for item in packs["results"]}

    assert by_id["observability"]["status"] == "skipped"
    assert by_id["http.success"]["status"] == "skipped"
    assert "no log source declared" in by_id["observability"]["detail"]


def test_the_example_charges_nothing_and_says_why(tmp_path: Path):
    """With no provider the book is the neutral one: nothing to price, no crash.

    The suite's loop books every iteration it can price; a `POST` with no `amount`
    in its body has nothing to price, so the line is excluded by name rather than
    invented as a zero. That the loop tries at all is the one product-shaped habit
    left in the HTTP path, and it is recorded here rather than hidden: it belongs
    to the declarative price model of Etapa 2, not to this phase.
    """
    run_dir = _run(tmp_path)
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))

    assert book["included"] == []
    assert book["total"] == "0"
    assert {line["exclusion"] for line in book["excluded"]} == {"no_amount"}


def test_the_example_carries_no_trace_of_the_product(tmp_path: Path):
    """The ramp is what a stranger copies; a product word in it would be a leak.

    Only the files the harness *reads* are scanned — the descriptor, the contracts,
    the cases, the suites, the round and the mock. `README.md` and `gate.sh` are
    excluded on purpose: they name the product once each, in the lines that tell a
    reader the core must *not* be able to import it.
    """
    scanned = [
        EXAMPLE / "app.py",
        *EXAMPLE.glob("qa/**/*.yaml"),
        *EXAMPLE.glob("contracts/**/*.yaml"),
        *EXAMPLE.glob("cases/**/*.yaml"),
        *EXAMPLE.glob("suites/**/*.yaml"),
        *EXAMPLE.glob("rounds/**/*.yaml"),
        *EXAMPLE.glob("baselines/**/*.json"),
    ]
    # `nokr` is the reference provider's name: the one product word this repository
    # knows, and the one a sample copied from that product would carry.
    offenders = [
        str(path.relative_to(EXAMPLE))
        for path in scanned
        if "nokr" in path.read_text(encoding="utf-8").lower()
    ]

    assert offenders == []
