#!/usr/bin/env python
"""Check that every command in docs/RUNBOOK.md code blocks exists (T-1105).

- ``make <target>``: target defined in the Makefile and not a "not implemented" stub;
- ``siavash <command>``: registered in the Typer app (``siavash.cli``);
- ``python scripts/x.py`` / ``bash scripts/x.sh`` / ``scripts/x.sh``: file exists;
- anything else must start with a known system tool (docker, git, curl, ...).
Exit code 1 lists the offending lines.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_TOOLS = {
    "apt",
    "apt-get",
    "bash",
    "cat",
    "cd",
    "chmod",
    "cp",
    "crontab",
    "curl",
    "date",
    "df",
    "docker",
    "du",
    "echo",
    "export",
    "free",
    "git",
    "grep",
    "head",
    "less",
    "liara",
    "ls",
    "mkdir",
    "nano",
    "ping",
    "rsync",
    "scp",
    "sed",
    "ssh",
    "sudo",
    "systemctl",
    "tail",
    "tar",
    "time",
    "uptime",
    "vim",
    "watch",
    "wc",
    "find",
}
FENCE = re.compile(r"^```(\w*)\s*$")
ASSIGN = re.compile(r"^[A-Z_][A-Z0-9_]*=")


def make_targets(makefile: Path) -> dict[str, bool]:
    """``{target: implemented}``."""
    targets: dict[str, bool] = {}
    current = None
    for line in makefile.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
        if m and not line.startswith((".PHONY", "\t")):
            current = m.group(1)
            targets[current] = True
        elif current and line.startswith("\t") and "not_implemented" in line:
            targets[current] = False
    return targets


def cli_commands() -> set[str]:
    from typer.main import get_command

    from siavash.cli import app

    return set(get_command(app).commands)  # type: ignore[attr-defined]


def code_lines(md: Path) -> list[tuple[int, str]]:
    out, inside, lang = [], False, ""
    for no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
        m = FENCE.match(line.strip())
        if m:
            inside, lang = (not inside, m.group(1)) if not inside else (False, "")
            continue
        if inside and lang in ("", "bash", "sh", "shell", "console"):
            s = line.strip()
            if s and not s.startswith("#"):
                out.append((no, s.removeprefix("$ ")))
    return out


def check_command(cmd: str, targets: dict[str, bool], cli: set[str]) -> str | None:
    for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd):
        try:
            tokens = shlex.split(part, comments=True)
        except ValueError:
            tokens = part.split()
        while tokens and ASSIGN.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            continue
        head, rest = tokens[0], tokens[1:]
        if head == "sudo" and rest:
            head, rest = rest[0], rest[1:]
        if head == "make":
            for t in (x for x in rest if not ASSIGN.match(x) and not x.startswith("-")):
                if t not in targets:
                    return f"make target '{t}' does not exist"
                if not targets[t]:
                    return f"make target '{t}' is not implemented yet"
        elif head == "siavash":
            if not rest or rest[0] not in cli:
                return f"siavash command '{rest[0] if rest else ''}' does not exist"
        elif head in ("python", "python3", "bash") and rest and rest[0].startswith("scripts/"):
            if not (ROOT / rest[0]).exists():
                return f"{rest[0]} does not exist"
        elif head.startswith("scripts/"):
            if not (ROOT / head).exists():
                return f"{head} does not exist"
        elif head not in SYSTEM_TOOLS:
            return f"unknown command '{head}'"
    return None


def main(argv: list[str] | None = None) -> int:
    md = Path(argv[0]) if argv else ROOT / "docs" / "RUNBOOK.md"
    targets, cli = make_targets(ROOT / "Makefile"), cli_commands()
    lines = code_lines(md)
    errors = [(no, cmd, err) for no, cmd in lines if (err := check_command(cmd, targets, cli))]
    for no, cmd, err in errors:
        print(f"{md.name}:{no}: {err}: {cmd}")
    print(f"checked {len(lines)} command lines: {'OK' if not errors else f'{len(errors)} problem(s)'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
