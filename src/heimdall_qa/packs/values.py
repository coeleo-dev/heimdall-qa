"""The two probe packs: was the oracle met, and did every surface hold still.

What is left here is reporting, not arithmetic. Whether a value *is* the one the
oracle expected was answered before these packs run, by `Oracle.matches`, because
the answer is a fact about one product's price model. A pack only says which
surfaces failed and how.
"""

from dataclasses import dataclass
from typing import Any

from heimdall_qa.packs import PackResult


@dataclass(frozen=True)
class SurfaceEval:
    surface_id: str
    jsonpath: str
    expect: str
    before: Any
    after: Any
    expected: Any
    matched: bool
    timed_out: bool


def values_packs(evals: list[SurfaceEval]) -> list[PackResult]:
    oracle_fail = [
        _detail(item)
        for item in evals
        if item.expect == "exact" and not item.matched
    ]
    consistency_fail = [
        _detail(item)
        for item in evals
        if item.expect != "exact" and (not item.matched or item.timed_out)
    ]
    return [
        _pack("values.oracle", oracle_fail),
        _pack("values.consistency", consistency_fail),
    ]


def _detail(item: SurfaceEval) -> str:
    timeout = " timeout" if item.timed_out else ""
    return f"{item.surface_id}: esperado {item.expected} lido {item.after}{timeout}"


def _pack(pack_id: str, failures: list[str]) -> PackResult:
    if failures:
        return PackResult(pack_id, "fail", "; ".join(failures))
    return PackResult(pack_id, "pass")
