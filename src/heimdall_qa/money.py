"""Decimal arithmetic the core may do without knowing anyone's price model.

Only the two operations that are true of any amount: build a `Decimal` from
whatever the wire said, and compare two of them as numbers rather than as strings.
Rounding is deliberately absent — how a product rounds its money is declared by
that product's oracle, and a core that guessed would be wrong somewhere expensive.

`Decimal(...)` via `str()` and never via the value itself: `Decimal(0.003)` is
`0.003000000000000000062...`, and that is how a comparison between two equal
amounts fails.
"""

from decimal import Decimal
from typing import Any

from heimdall_qa.errors import HarnessError


def to_decimal(value: Any) -> Decimal:
    """`value` as a decimal, refusing to invent one for nothing."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise HarnessError(
            code="MONEY_AMOUNT_MISSING",
            message="an amount was needed and none was read",
            hint="the surface jsonpath found no value; check the case contract",
        )
    return Decimal(str(value))


def money_equal(left: Any, right: Any) -> bool:
    """Two amounts are the same money, not the same string."""
    return to_decimal(left).compare(to_decimal(right)) == 0
