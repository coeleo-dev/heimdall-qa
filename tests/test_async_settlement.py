"""Which declaration decides whether a consumer is owed a line.

`routes[]` matches by **prefix**, so one entry answers for every endpoint under it:
`async: true` on `/api/ingest` covers the read that returns a transaction, the dry run
that settles nothing, and the ingest that queues the work. The contract is the only
place that can tell those three apart, and its `async` is tri-valued for exactly that —
`worker`, `sync`, or absent to inherit the prefix.

These pin the precedence, because getting it backwards is silent in the direction that
matters: the specific `sync` was unreachable, so every synchronous endpoint under an
async prefix was reported as a product failure about a consumer nobody had asked to do
anything. The status clause is pinned too — an endpoint that refuses the request queued
no work, and asking for the consumer's line would fail every negative case.
"""

from pathlib import Path

from heimdall_qa.runner import _settles_asynchronously
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.schema.models import Contract
from heimdall_qa.testing import project_at

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The fixture descriptor declares `/api/ingest` with `async: true`, which is the
#: prefix the real ingest family answers under.
PROJECT = project_at(FIXTURES / "qa" / "project.yaml")

_ASYNC_PATH = "/api/ingest/evt-1"
_SYNC_PATH = "/platform/things"


def _case(**overrides) -> CaseFile:
    payload = {
        "id": "c",
        "contract": "contracts/x.yaml",
        "kind": "H01",
        "expect": {"status": 200},
    }
    return CaseFile(**{**payload, **overrides})


def _contract(async_mode: str | None) -> Contract:
    return Contract(
        endpoint="POST /api/ingest",
        async_mode=async_mode,
        baseline="baselines/empty.json",
        fields={},
    )


def test_a_contract_that_says_sync_outranks_its_async_prefix():
    """The read and the dry run under `/api/ingest`, which settle inline."""
    assert not _settles_asynchronously(
        _case(), _contract("sync"), PROJECT, _ASYNC_PATH, 200
    )


def test_a_contract_that_says_worker_is_async():
    assert _settles_asynchronously(
        _case(), _contract("worker"), PROJECT, _ASYNC_PATH, 200
    )


def test_a_contract_that_says_nothing_inherits_the_prefix():
    """Absent is the third value, and it means "ask the route", not "say no"."""
    assert _settles_asynchronously(_case(), _contract(None), PROJECT, _ASYNC_PATH, 200)
    assert not _settles_asynchronously(
        _case(), _contract(None), PROJECT, _SYNC_PATH, 200
    )


def test_a_refused_request_owes_a_consumer_nothing():
    """A 4xx queued no work, so the trace never reached the consumer to log."""
    assert not _settles_asynchronously(
        _case(), _contract("worker"), PROJECT, _ASYNC_PATH, 400
    )


def test_a_case_that_asks_for_the_wait_gets_it():
    """`wait_logs_ms` is the author saying the effect is late, not absent."""
    assert _settles_asynchronously(
        _case(wait_logs_ms=500), _contract(None), PROJECT, _SYNC_PATH, 200
    )
