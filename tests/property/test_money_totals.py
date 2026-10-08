from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from quakeexposure.domain.money import (
    Currency,
    format_major,
    minor_from_json,
    minor_to_json,
    totals_by_currency,
)

VALUES = st.integers(0, 2**80)


@given(st.lists(st.tuples(st.sampled_from(Currency), VALUES)))
def test_f26_totals_are_exact_and_per_currency(items: list[tuple[Currency, int]]) -> None:
    totals = totals_by_currency(items)
    assert set(totals) == set(Currency)
    for currency in Currency:
        expected = sum((Decimal(v) for c, v in items if c == currency), Decimal(0))
        assert Decimal(totals[currency]) == expected
        assert minor_from_json(minor_to_json(totals[currency])) == totals[currency]


@given(VALUES)
def test_f26_format_major(minor: int) -> None:
    assert Decimal(format_major(minor, Currency.USD)) == Decimal(minor) / 100
