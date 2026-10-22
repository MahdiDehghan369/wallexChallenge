"""Spot only: no futures/margin/leverage/withdraw/transfer/deposit code paths anywhere in src/ (rule R1)."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PATTERN = re.compile(r"futures|margin|leverage|withdraw|transfer|deposit", re.IGNORECASE)


def test_no_forbidden_paths() -> None:
    hits = []
    for path in SRC.rglob("*.py"):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not PATTERN.search(line):
                continue
            # guard.py may document the ban in comment lines only
            if path.name == "guard.py" and line.strip().startswith("#"):
                continue
            hits.append(f"{path.relative_to(SRC)}:{no}: {line.strip()}")
    assert not hits, "forbidden spot-only keywords found:\n" + "\n".join(hits)
