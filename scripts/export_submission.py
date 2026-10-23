#!/usr/bin/env python
"""Build the submission log package (T-1303) → ``submission/``.

Copies the decision / order / fill / equity / regime / latency / LLM logs, the live / shadow / backtest
reports and CHANGELOG.md, writes ``MANIFEST.md`` and ``SHA256SUMS``, and scans every file:

- exact secret values from ``.env`` (API keys, webhook) → masked;
- API-key-like tokens (≥ 24 chars mixing lower case, upper case and digits) → masked;
- Iranian mobile numbers and valid national codes → masked.

Text files are masked in the copy; a binary file (the LLM cache) that contains a secret fails the export.
``--strict`` fails instead of masking.

    python scripts/export_submission.py
    python scripts/export_submission.py --logs logs --out submission --strict

Defaults come from ``base.yaml > paths`` (``logs``, ``reports``, ``submission``), so with env
``PATHS_ROOT=/app/persist`` (Liara) it reads and writes the persistent disk.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MASK = "***MASKED***"
LOG_FILES = [
    "decisions.jsonl",
    "orders.jsonl",
    "fills.csv",
    "equity.csv",
    "regime.jsonl",
    "latency.jsonl",
    "llm/calls.jsonl",
    "llm/cache.sqlite",
]
REPORT_DIRS = ["live", "shadow", "backtest/walkforward"]  # under the reports directory
TEXT_SUFFIXES = {".jsonl", ".json", ".csv", ".md", ".txt", ".yaml", ".yml", ".log"}

TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_\-:])[A-Za-z0-9_\-]{24,}(?![A-Za-z0-9_\-])")
MOBILE_RE = re.compile(r"(?<!\d)(?:\+98|0098|0)9\d{9}(?!\d)")
NATIONAL_RE = re.compile(r"(?<![\d.\-:])\d{10}(?![\d.\-:])")

DESCRIPTIONS = {
    "logs/decisions.jsonl": "one DecisionRecord per line (schema: WALLEX_AGENT_PLAN.md appendix C / core.models.DecisionRecord)",
    "logs/orders.jsonl": "every order request / state change sent to the broker (client_order_id, decision_id)",
    "logs/fills.csv": "every fill: ts, symbol, side, price, qty, notional_tmn, fee, fee_asset, position_id, strategy",
    "logs/equity.csv": "equity snapshots (equity_tmn, cash, exposure) used for return and drawdown",
    "logs/regime.jsonl": "market regime assessments (rule + LLM analyst)",
    "logs/latency.jsonl": "decision / order latency measurements (ms)",
    "logs/llm/calls.jsonl": "every real LLM call: model, prompt_version, input_hash, tokens, latency, status (no keys)",
    "logs/llm/cache.sqlite": "LLM answers keyed by hash(model + prompt_version + input) — enables exact replay",
    "reports/live": "judge report (REPORT.md, metrics.json, charts, trades)",
    "reports/shadow": "shadow-run metrics",
    "reports/backtest": "final backtest / walk-forward reports",
    "CHANGELOG.md": "versions deployed during the challenge (tag, sha, config hash, model)",
}


@dataclass
class ScanResult:
    masked: dict[str, int] = field(default_factory=dict)
    binary_hits: list[str] = field(default_factory=list)


def national_code_valid(code: str) -> bool:
    if len(set(code)) == 1:
        return False
    s = sum(int(code[i]) * (10 - i) for i in range(9)) % 11
    check = int(code[9])
    return (s < 2 and check == s) or (s >= 2 and check == 11 - s)


def key_like(token: str) -> bool:
    return (
        any(c.islower() for c in token)
        and any(c.isupper() for c in token)
        and any(c.isdigit() for c in token)
    )


def mask_text(text: str, secrets: list[str]) -> tuple[str, int]:
    n = 0
    for s in secrets:
        if s and s in text:
            n += text.count(s)
            text = text.replace(s, MASK)

    def sub_token(m: re.Match[str]) -> str:
        nonlocal n
        if key_like(m.group(0)):
            n += 1
            return MASK
        return m.group(0)

    def sub_mobile(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return MASK

    def sub_national(m: re.Match[str]) -> str:
        nonlocal n
        if national_code_valid(m.group(0)):
            n += 1
            return MASK
        return m.group(0)

    text = TOKEN_RE.sub(sub_token, text)
    text = MOBILE_RE.sub(sub_mobile, text)
    text = NATIONAL_RE.sub(sub_national, text)
    return text, n


def copy_scanned(src: Path, dst: Path, secrets: list[str], res: ScanResult, rel: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix in TEXT_SUFFIXES:
        text, n = mask_text(src.read_text(encoding="utf-8", errors="replace"), secrets)
        dst.write_text(text, encoding="utf-8")
        if n:
            res.masked[rel] = n
    else:
        data = src.read_bytes()
        if any(s and s.encode() in data for s in secrets):
            res.binary_hits.append(rel)
        shutil.copy2(src, dst)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cfg(env_file: Path) -> Any:
    from siavash.config import load_config

    return load_config(ROOT / "config", env_file=env_file if env_file.exists() else None)


def env_secrets(env_file: Path) -> list[str]:
    return [s for s in load_cfg(env_file).secrets.raw_values() if len(s) >= 8]


def export(
    logs: Path,
    out: Path,
    secrets: list[str],
    strict: bool = False,
    root: Path = ROOT,
    reports: Path | None = None,
) -> ScanResult:
    reports = reports if reports is not None else root / "reports"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    res = ScanResult()
    for name in LOG_FILES:
        if (logs / name).exists():
            copy_scanned(logs / name, out / "logs" / name, secrets, res, f"logs/{name}")
    for d in REPORT_DIRS:
        for f in sorted((reports / d).rglob("*")) if (reports / d).exists() else []:
            if f.is_file() and "runs" not in f.relative_to(reports / d).parts:
                rel = (Path("reports") / f.relative_to(reports)).as_posix()
                copy_scanned(f, out / rel, secrets, res, rel)
    if (root / "CHANGELOG.md").exists():
        copy_scanned(root / "CHANGELOG.md", out / "CHANGELOG.md", secrets, res, "CHANGELOG.md")
    if res.binary_hits or (strict and res.masked):
        shutil.rmtree(out)
        return res
    files = sorted(p for p in out.rglob("*") if p.is_file())
    lines = ["# Submission package", "", "| file | description |", "|---|---|"]
    for p in files:
        rel = str(p.relative_to(out))
        key = next((k for k in DESCRIPTIONS if rel == k or rel.startswith(k + "/")), None)
        lines.append(f"| `{rel}` | {DESCRIPTIONS.get(key, '') if key else ''} |")
    if res.masked:
        lines += [
            "",
            "Masked during export (secrets / personal data): "
            + ", ".join(f"`{k}` ({v})" for k, v in res.masked.items()),
        ]
    (out / "MANIFEST.md").write_text("\n".join([*lines, ""]), encoding="utf-8")
    sums = [f"{sha256(p)}  {p.relative_to(out)}" for p in sorted(out.rglob("*")) if p.is_file()]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default=None, help="default: paths.logs")
    ap.add_argument("--reports", default=None, help="default: paths.reports")
    ap.add_argument("--out", default=None, help="default: paths.submission")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--strict", action="store_true", help="fail instead of masking")
    args = ap.parse_args(argv)
    cfg = load_cfg(ROOT / args.env_file)
    paths = cfg.base.paths
    out = ROOT / (args.out or paths.submission)  # an absolute path (PATHS_ROOT) stays absolute
    secrets = [s for s in cfg.secrets.raw_values() if len(s) >= 8]
    res = export(
        ROOT / (args.logs or paths.logs),
        out,
        secrets,
        args.strict,
        reports=ROOT / (args.reports or paths.reports),
    )
    if res.binary_hits:
        print(f"FAILED: secret found in binary file(s) {res.binary_hits}; nothing exported", file=sys.stderr)
        return 1
    if args.strict and res.masked:
        print(f"FAILED (--strict): sensitive strings in {res.masked}; nothing exported", file=sys.stderr)
        return 1
    print(f"exported -> {out}/ (masked: {res.masked or 'nothing'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
