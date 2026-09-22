from dataclasses import dataclass
from typing import Any

from heimdall_qa.jsonpath import MISSING
from heimdall_qa.oracle.money import DEBIT_SCALE
from heimdall_qa.oracle.money import money_equal
from heimdall_qa.oracle.money import to_decimal
from heimdall_qa.packs import PackResult
from heimdall_qa.schema.models import SurfaceSpec


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


def expected_after(surface: SurfaceSpec, before: Any, oracle_total: Any) -> Any:
    if before is MISSING or before is None:
        return None
    if surface.expect == "unchanged":
        return before
    if surface.expect == "increase":
        return before
    if _is_wallet(surface):
        return _quantize(to_decimal(before) - to_decimal(oracle_total))
    return _quantize(to_decimal(before) + to_decimal(oracle_total))


def surface_matches(
    surface: SurfaceSpec,
    before: Any,
    after: Any,
    oracle_total: Any,
) -> bool:
    if after is MISSING or after is None:
        return False
    if before is MISSING or before is None:
        return False
    if surface.expect == "unchanged":
        return _same_number(before, after)
    if surface.expect == "increase":
        return _increased(before, after, surface.min_delta)
    expected = expected_after(surface, before, oracle_total)
    return money_equal(after, expected)


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


def _is_wallet(surface: SurfaceSpec) -> bool:
    path = surface.jsonpath.rsplit(".", 1)[-1]
    return surface.id == "wallet" or path == "balance"


def _increased(before: Any, after: Any, min_delta: Any) -> bool:
    delta = to_decimal(after) - to_decimal(before)
    if delta.compare(to_decimal(0)) <= 0:
        return False
    if min_delta is None:
        return True
    return delta.compare(to_decimal(min_delta)) >= 0


def _same_number(left: Any, right: Any) -> bool:
    return money_equal(left, right)


def _quantize(value: Any) -> Any:
    return to_decimal(value).quantize(DEBIT_SCALE)


def _detail(item: SurfaceEval) -> str:
    timeout = " timeout" if item.timed_out else ""
    return (
        f"{item.surface_id}: esperado {item.expected} lido {item.after}{timeout}"
    )


def _pack(pack_id: str, failures: list[str]) -> PackResult:
    if failures:
        return PackResult(pack_id, "fail", "; ".join(failures))
    return PackResult(pack_id, "pass")
