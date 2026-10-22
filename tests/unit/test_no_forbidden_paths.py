"""Spot only: no futures/margin/leverage/withdraw/transfer/deposit (or bank / wallet account) code paths
anywhere in src/ (rule R1)."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PATTERN = re.compile(
    r"futures|margin|leverage|withdraw|transfer|deposit|iban|card-numbers|wallets", re.IGNORECASE
)
# market-list field names that may only be read to *reject* non-spot markets (plan T-105)
ENDPOINT_FIELDS = re.compile(r"\b(is_margin|leverage_step|max_leverage)\b")


def test_no_forbidden_paths() -> None:
    hits = []
    for path in SRC.rglob("*.py"):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not PATTERN.search(line):
                continue
            # guard.py may document the ban in comment lines only
            if path.name == "guard.py" and line.strip().startswith("#"):
                continue
            if path.name == "endpoints.py" and not PATTERN.search(ENDPOINT_FIELDS.sub("", line)):
                continue
            hits.append(f"{path.relative_to(SRC)}:{no}: {line.strip()}")
    assert not hits, "forbidden spot-only keywords found:\n" + "\n".join(hits)
