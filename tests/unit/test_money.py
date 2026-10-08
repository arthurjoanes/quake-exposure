import pytest

from quakeexposure.domain.money import (
    Currency,
    format_major,
    minor_from_json,
    minor_to_json,
    totals_by_currency,
)


def test_f26_totals_per_currency_above_2_pow_53() -> None:
    big = 2**53 + 1
    totals = totals_by_currency([(Currency.BRL, big), (Currency.BRL, 1), (Currency.USD, 7)])
    assert totals == {Currency.BRL: 2**53 + 2, Currency.USD: 7}
    assert minor_to_json(totals[Currency.BRL]) == "9007199254740994"
    assert float(big) == float(2**53)  # o motivo de não usar float


def test_both_currencies_always_present() -> None:
    assert totals_by_currency([]) == {Currency.BRL: 0, Currency.USD: 0}


@pytest.mark.parametrize("bad", [1.0, True, -1, "1"])
def test_rejects_non_integer_minor(bad: object) -> None:
    with pytest.raises(ValueError):
        totals_by_currency([(Currency.USD, bad)])  # type: ignore[list-item]


@pytest.mark.parametrize("text", ["", "01", "1.0", "-1", "1e3", " 1", "١٢"])
def test_minor_from_json_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        minor_from_json(text)


@pytest.mark.parametrize(
    ("minor", "text"),
    [(0, "0.00"), (5, "0.05"), (1234567, "12345.67"), (2**64, "184467440737095516.16")],
)
def test_format_major(minor: int, text: str) -> None:
    assert format_major(minor, Currency.BRL) == text
