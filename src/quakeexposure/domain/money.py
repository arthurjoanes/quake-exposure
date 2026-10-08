"""Dinheiro em inteiro na unidade menor. Soma só dentro da mesma moeda; nunca float."""

import re
from collections.abc import Iterable
from enum import StrEnum


class Currency(StrEnum):
    BRL = "BRL"
    USD = "USD"


MINOR_DIGITS = {Currency.BRL: 2, Currency.USD: 2}
_MINOR = re.compile(r"(0|[1-9][0-9]*)")


def totals_by_currency(values: Iterable[tuple[Currency, int]]) -> dict[Currency, int]:
    totals = dict.fromkeys(Currency, 0)
    for currency, minor in values:
        totals[currency] += check_minor(minor)
    return totals


def check_minor(value: object) -> int:
    # bool é int em Python; float nunca entra
    if type(value) is not int or value < 0:
        raise ValueError(f"valor em unidade menor inválido: {value!r}")
    return value


def minor_to_json(value: int) -> str:
    return str(check_minor(value))


def minor_from_json(text: str) -> int:
    if not isinstance(text, str) or not _MINOR.fullmatch(text):
        raise ValueError(f"valor em unidade menor inválido: {text!r}")
    return int(text)


def format_major(value: int, currency: Currency) -> str:
    """1234567 BRL -> '12345.67'. Para leitura; o JSON leva a unidade menor."""
    digits = MINOR_DIGITS[currency]
    whole, frac = divmod(check_minor(value), 10**digits)
    return f"{whole}.{frac:0{digits}d}"
