"""Shared helpers for exchange tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "wallex"
DENYLIST = Path(__file__).resolve().parents[3] / "config" / "denylist.txt"


def load(name: str) -> Any:
    with (FIXTURES / f"{name}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fx() -> Callable[[str], Any]:
    return load
