from decimal import Decimal
from decimal import ROUND_HALF_EVEN
from decimal import ROUND_HALF_UP
from typing import Any

from heimdall_qa.errors import HarnessError

DEBIT_SCALE = Decimal("0.00000")


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def metering_amount(body_amount: Any) -> Decimal:
    return to_decimal(body_amount).quantize(DEBIT_SCALE, rounding=ROUND_HALF_UP)


def ingest_flat(quantity: Any, unit_amount: Any, flat_amount: Any = 0) -> Decimal:
    raw = to_decimal(quantity) * to_decimal(unit_amount) + to_decimal(flat_amount)
    return raw.quantize(DEBIT_SCALE, rounding=ROUND_HALF_EVEN)


def money_equal(left: Any, right: Any) -> bool:
    return to_decimal(left).compare(to_decimal(right)) == 0


def require_flat_model(pricing_model: str) -> None:
    if pricing_model.upper() == "FLAT":
        return
    raise HarnessError(
        code="CONFIG_INVALID",
        message=f"oracle v1 only prices FLAT; got {pricing_model}",
        hint="record TIERED/VOLUME as a P-GAP; do not guess the amount",
    )
