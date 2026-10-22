"""T-1303: a fake key in a log is masked (or fails the export); package has MANIFEST + SHA256SUMS."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("export_submission", ROOT / "scripts" / "export_submission.py")
assert spec and spec.loader
ex = importlib.util.module_from_spec(spec)
sys.modules["export_submission"] = ex  # dataclasses resolve the defining module via sys.modules
spec.loader.exec_module(ex)

FAKE_KEY = "wlx_9fK2mQ7xLp3RtZ8vB1nC4dE6"  # fake, mixed case + digits


def make_logs(tmp: Path) -> Path:
    logs = tmp / "logs"
    (logs / "llm").mkdir(parents=True)
    rec = {
        "decision_id": "01JBXYZABCDEFGHJKMNPQRSTVW",
        "final_reason": f"debug header x-api-key={FAKE_KEY}",
        "client_order_ids": ["SIAV-01JBXYZABCDEFGHJKMNPQRSTVW-E1"],
        "config_hash": "sha256:" + "ab" * 32,
    }
    (logs / "decisions.jsonl").write_text(json.dumps(rec) + "\n")
    (logs / "orders.jsonl").write_text('{"note": "call me 09121234567, code 0012345679"}\n')
    (logs / "fills.csv").write_text(
        "ts,symbol,side,price,qty\n2026-11-05T09:15:00Z,BTC/TMN,buy,7349000000,0.0001\n"
    )
    (logs / "llm" / "cache.sqlite").write_bytes(b"SQLite format 3\x00 harmless")
    return logs


def test_fake_key_and_pii_are_masked(tmp_path: Path) -> None:
    logs = make_logs(tmp_path)
    out = tmp_path / "submission"
    res = ex.export(logs, out, [FAKE_KEY], root=tmp_path)
    text = (out / "logs" / "decisions.jsonl").read_text()
    assert FAKE_KEY not in text and ex.MASK in text
    assert "01JBXYZABCDEFGHJKMNPQRSTVW" in text  # ULIDs / client ids are not key-like
    assert "sha256:" + "ab" * 32 in text  # hashes stay
    orders = (out / "logs" / "orders.jsonl").read_text()
    assert "09121234567" not in orders and "0012345679" not in orders
    assert res.masked["logs/decisions.jsonl"] >= 1 and res.masked["logs/orders.jsonl"] == 2
    sums = (out / "SHA256SUMS").read_text().splitlines()
    for line in sums:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    manifest = (out / "MANIFEST.md").read_text()
    assert (
        "logs/decisions.jsonl" in manifest and "appendix C" in manifest and "Masked during export" in manifest
    )


def test_strict_and_binary_hits_fail(tmp_path: Path) -> None:
    logs = make_logs(tmp_path)
    res = ex.export(logs, tmp_path / "s1", [FAKE_KEY], strict=True, root=tmp_path)
    assert res.masked and not (tmp_path / "s1").exists()
    (logs / "llm" / "cache.sqlite").write_bytes(b"SQLite " + FAKE_KEY.encode())
    res2 = ex.export(logs, tmp_path / "s2", [FAKE_KEY], root=tmp_path)
    assert res2.binary_hits == ["logs/llm/cache.sqlite"] and not (tmp_path / "s2").exists()


def test_national_code_checksum() -> None:
    assert ex.national_code_valid("0012345679")
    assert not ex.national_code_valid("0012345678")
    assert not ex.national_code_valid("1111111111")
