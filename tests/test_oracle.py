from decimal import Decimal

import pytest

from nokr_qa.errors import HarnessError
from nokr_qa.oracle.money import ingest_flat
from nokr_qa.oracle.money import metering_amount
from nokr_qa.oracle.money import money_equal
from nokr_qa.oracle.money import require_flat_model


def test_ingest_flat_one_hundred_tokens_is_0_00300():
    got = ingest_flat(Decimal("100"), Decimal("0.00003"))
    assert money_equal(got, Decimal("0.00300"))
    assert str(got) == "0.00300"


def test_0_00300_is_not_equal_to_0_00301():
    assert not money_equal(Decimal("0.00300"), Decimal("0.00301"))


def test_metering_amount_half_up_scale_5():
    got = metering_amount(Decimal("0.010004"))
    assert money_equal(got, Decimal("0.01000"))
    got_up = metering_amount(Decimal("0.010005"))
    assert money_equal(got_up, Decimal("0.01001"))


def test_non_flat_pricing_is_config_invalid():
    with pytest.raises(HarnessError) as caught:
        require_flat_model("TIERED")
    assert caught.value.code == "CONFIG_INVALID"
