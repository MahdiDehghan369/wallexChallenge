"""T-1105: every command in docs/RUNBOOK.md exists; the checker catches missing ones."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_runbook_commands", ROOT / "scripts" / "check_runbook_commands.py"
)
assert spec and spec.loader
crc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crc)


def test_runbook_commands_exist() -> None:
    assert crc.main([str(ROOT / "docs" / "RUNBOOK.md")]) == 0


def test_checker_flags_missing_commands(tmp_path: Path) -> None:
    md = tmp_path / "rb.md"
    md.write_text(
        "\n".join(
            [
                "```bash",
                "make status",
                'make backtest ARGS="--synthetic --days 3"',
                "make no-such-target",
                "siavash fly",
                "python scripts/missing.py",
                "rm -rf /",
                "```",
                "```text",
                "0 * * * * not checked",
                "```",
            ]
        ),
        encoding="utf-8",
    )
    targets, cli = crc.make_targets(ROOT / "Makefile"), crc.cli_commands()
    results = {cmd: crc.check_command(cmd, targets, cli) for _, cmd in crc.code_lines(md)}
    assert results["make status"] is None
    assert results['make backtest ARGS="--synthetic --days 3"'] is None
    assert "does not exist" in results["make no-such-target"]
    assert "does not exist" in results["siavash fly"]
    assert "does not exist" in results["python scripts/missing.py"]
    assert "unknown command" in results["rm -rf /"]
    assert "0 * * * * not checked" not in results
    assert crc.main([str(md)]) == 1


def test_not_implemented_targets_are_rejected() -> None:
    targets = crc.make_targets(ROOT / "Makefile")
    stubs = [t for t, ok in targets.items() if not ok]
    for t in stubs:
        assert "not implemented" in crc.check_command(f"make {t}", targets, set())
