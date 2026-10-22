#!/usr/bin/env python
"""Validate PROGRESS.md and list the next available tasks (T-010).

Checks: every DONE task has all hard dependencies DONE; prints the next AGENT / AGENT+HUMAN
tasks (in table 9.4 row order) whose hard dependencies are DONE. Soft dependencies
("(نرم)") do not block. Exit code 1 if the table is inconsistent.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DONE_STATES = {"DONE", "DONE (UNVERIFIED)"}
AGENT_TYPES = {"AGENT", "AGENT+HUMAN"}
SOFT_MARK = "(نرم)"


@dataclass
class Row:
    num: int
    tid: str
    title: str
    owner: str
    typ: str
    hard_deps: list[str] = field(default_factory=list)
    soft_deps: list[str] = field(default_factory=list)
    status: str = "TODO"
    note: str = ""


def parse_deps(cell: str) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    for part in cell.split(","):
        part = part.strip()
        ids = re.findall(r"T-\d+", part)
        if not ids:
            continue
        (soft if SOFT_MARK in part else hard).extend(ids)
    return hard, soft


def parse_progress(path: Path) -> list[Row]:
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            raise ValueError(f"malformed row: {line}")
        hard, soft = parse_deps(cells[5])
        rows.append(
            Row(
                num=int(cells[0]),
                tid=cells[1],
                title=cells[2],
                owner=cells[3],
                typ=cells[4],
                hard_deps=hard,
                soft_deps=soft,
                status=cells[6],
                note=cells[8] if len(cells) > 8 else "",
            )
        )
    return rows


def check(rows: list[Row]) -> list[str]:
    status = {r.tid: r.status for r in rows}
    problems = []
    for r in rows:
        for d in r.hard_deps:
            if d not in status:
                problems.append(f"{r.tid}: unknown dependency {d}")
            elif r.status in DONE_STATES and status[d] not in DONE_STATES:
                problems.append(f"{r.tid} is {r.status} but dependency {d} is {status[d]}")
    return problems


def next_tasks(rows: list[Row], owner: str | None = None) -> list[Row]:
    status = {r.tid: r.status for r in rows}
    out = []
    for r in rows:
        if r.status != "TODO" or r.typ not in AGENT_TYPES:
            continue
        if owner and r.owner != owner:
            continue
        if all(status.get(d) in DONE_STATES for d in r.hard_deps):
            out.append(r)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default="PROGRESS.md")
    ap.add_argument("--owner", choices=["A", "B"], default=None)
    ap.add_argument("-n", type=int, default=5, help="how many next tasks to print")
    args = ap.parse_args(argv)

    rows = parse_progress(Path(args.file))
    if not rows:
        print(f"no task rows found in {args.file}", file=sys.stderr)
        return 1
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    problems = check(rows)
    for p in problems:
        print(f"INCONSISTENT: {p}")

    nxt = next_tasks(rows, args.owner)
    print("next available tasks:")
    for r in nxt[: args.n]:
        print(f"  {r.num:>3} {r.tid} {r.title} [{r.typ}]")
    if not nxt:
        print("  (none)")
    waiting = [r for r in rows if r.status in {"WAITING_HUMAN", "BLOCKED"}]
    for r in waiting:
        print(f"  {r.status}: {r.tid} {r.title} — {r.note}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
