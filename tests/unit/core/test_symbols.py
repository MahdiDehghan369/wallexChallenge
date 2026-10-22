"""T-007: symbol helpers."""

from __future__ import annotations

import pytest

from siavash.core.symbols import split, to_canonical, to_exchange


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BTCTMN", "BTC/TMN"),
        ("USDTTMN", "USDT/TMN"),
        ("BTCUSDT", "BTC/USDT"),
        ("btctmn", "BTC/TMN"),
        ("BTC/TMN", "BTC/TMN"),
        ("1BBABYDOGETMN", "1BBABYDOGE/TMN"),
        ("eth-usdt", "ETH/USDT"),
    ],
)
def test_to_canonical(raw: str, expected: str) -> None:
    assert to_canonical(raw) == expected


def test_to_exchange_and_split() -> None:
    assert to_exchange("BTC/TMN") == "BTCTMN"
    assert to_exchange("USDT/TMN") == "USDTTMN"
    assert split("ETH/USDT") == ("ETH", "USDT")
    assert split("ETHUSDT") == ("ETH", "USDT")


@pytest.mark.parametrize("bad", ["TMN", "BTCEUR", "/TMN", ""])
def test_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        to_canonical(bad)
