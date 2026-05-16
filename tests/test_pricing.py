"""Tests for tokenscope.pricing — model-family input-price lookup."""

from __future__ import annotations

import pytest

from tokenscope.pricing import (
    DEFAULT_INPUT_PRICE_USD_PER_MTOK,
    INPUT_PRICE_USD_PER_MTOK_BY_FAMILY,
    input_price_per_mtok,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("claude-opus-4-7", 15.0),
        ("claude-opus-4-6", 15.0),
        ("claude-sonnet-4-6", 3.0),
        ("claude-haiku-4-5-20251001", 1.0),
        ("claude-3-5-sonnet-20240620", 3.0),
    ],
)
def test_known_families(name: str, expected: float) -> None:
    assert input_price_per_mtok(name) == expected


def test_unknown_family_falls_back_to_default() -> None:
    assert input_price_per_mtok("claude-future-99") == DEFAULT_INPUT_PRICE_USD_PER_MTOK


def test_non_claude_model_falls_back_to_default() -> None:
    assert input_price_per_mtok("gpt-4o") == DEFAULT_INPUT_PRICE_USD_PER_MTOK


def test_pricing_table_is_finite_positive() -> None:
    for family, price in INPUT_PRICE_USD_PER_MTOK_BY_FAMILY.items():
        assert price > 0, family
