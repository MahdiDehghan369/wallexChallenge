#!/usr/bin/env python
"""Render docs/STRATEGY.md from docs/STRATEGY.template.md with numbers read from files (T-1302).

Placeholders (never type numbers by hand):
    {{cfg:strategy.s1.sl_atr}}                       value from config/*.yaml
    {{json:models/lgbm_v1/metadata.json:metrics.oof_auc|.3f}}   key path in a JSON file (optional format)
    {{md:reports/ml_walkforward.md:pooled OOF AUC: ([\\d.]+)}}  first regex group in a text file
    {{changelog}}                                    the "Versions" section of CHANGELOG.md
Values that come from ``reports/live`` or ``reports/shadow`` render as "TBD after challenge" until those
reports exist; any other unresolved placeholder is an error.

    python scripts/fill_strategy_numbers.py           # write docs/STRATEGY.md
    python scripts/fill_strategy_numbers.py --check   # fail if anything is unresolved
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"\{\{(cfg|json|md|changelog)(?::(.*?))?\}\}")
TBD = "TBD after challenge"
CHALLENGE_SOURCES = ("reports/live", "reports/shadow", "reports/consistency.md", "reports/replay.md")


class Unresolved(Exception):
    pass


def _fmt(v: Any, spec: str | None) -> str:
    if spec and isinstance(v, int | float) and not isinstance(v, bool):
        return format(v, spec)
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def resolve(kind: str, arg: str | None, cfg: Any, root: Path) -> str:
    if kind == "changelog":
        text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        return text.split("## Versions", 1)[1].strip() if "## Versions" in text else "_no versions yet_"
    assert arg is not None
    spec = None
    if kind in ("cfg", "json") and "|" in arg:
        arg, spec = arg.rsplit("|", 1)
    if kind == "cfg":
        obj: Any = cfg
        for part in arg.split("."):
            obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part)
        return _fmt(obj, spec)
    path_str, _, rest = arg.partition(":")
    path = root / path_str
    if not path.exists():
        raise Unresolved(f"{path_str} missing")
    if kind == "json":
        obj = json.loads(path.read_text(encoding="utf-8"))
        for part in rest.split("."):
            if not isinstance(obj, dict) or part not in obj:
                raise Unresolved(f"{path_str}: key {rest!r} missing")
            obj = obj[part]
        if obj is None:
            raise Unresolved(f"{path_str}: {rest!r} is null")
        return _fmt(obj, spec)
    m = re.search(rest, path.read_text(encoding="utf-8"))
    if not m:
        raise Unresolved(f"{path_str}: pattern {rest!r} not found")
    return m.group(1)


def render(template: str, cfg: Any, root: Path = ROOT) -> tuple[str, list[str]]:
    problems: list[str] = []

    def sub(m: re.Match[str]) -> str:
        kind, arg = m.group(1), m.group(2)
        try:
            return resolve(kind, arg, cfg, root)
        except (Unresolved, AttributeError, KeyError, ValueError) as e:
            if arg and arg.startswith(CHALLENGE_SOURCES):
                return TBD
            problems.append(f"{m.group(0)}: {e}")
            return m.group(0)

    return PLACEHOLDER.sub(sub, template), problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", default="docs/STRATEGY.template.md")
    ap.add_argument("--out", default="docs/STRATEGY.md")
    ap.add_argument("--check", action="store_true", help="only check; exit 1 on unresolved placeholders")
    args = ap.parse_args(argv)
    from siavash.config import load_config

    cfg = load_config(ROOT / "config", env_file=None)
    text, problems = render((ROOT / args.template).read_text(encoding="utf-8"), cfg)
    for p in problems:
        print(f"unresolved: {p}", file=sys.stderr)
    tbd = text.count(TBD)
    if args.check:
        print(f"{len(problems)} unresolved, {tbd} '{TBD}'")
        return 1 if problems else 0
    (ROOT / args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(problems)} unresolved, {tbd} '{TBD}')")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
