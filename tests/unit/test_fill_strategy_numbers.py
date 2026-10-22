"""T-1302: placeholders are filled from config / JSON / text reports; challenge-time ones become TBD."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from siavash.config import load_config

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "fill_strategy_numbers", ROOT / "scripts" / "fill_strategy_numbers.py"
)
assert spec and spec.loader
fsn = importlib.util.module_from_spec(spec)
sys.modules["fill_strategy_numbers"] = fsn
spec.loader.exec_module(fsn)


def test_render(tmp_path: Path) -> None:
    (tmp_path / "m.json").write_text(json.dumps({"metrics": {"auc": 0.61234}, "ok": True}))
    (tmp_path / "r.md").write_text("- pooled OOF AUC: 0.5986 · x\n")
    (tmp_path / "CHANGELOG.md").write_text("# c\n\n## Versions\n\n- live-v1.0 first\n")
    cfg = load_config("config")
    tpl = (
        "sl={{cfg:strategy.s1.sl_atr}} quotes={{cfg:risk.allowed_quotes}} "
        "auc={{json:m.json:metrics.auc|.3f}} ok={{json:m.json:ok}} md={{md:r.md:AUC: ([\\d.]+)}}\n"
        "live={{json:reports/live/metrics.json:judge.Net Return %}}\n{{changelog}}\n"
        "bad={{json:m.json:missing.key}}"
    )
    text, problems = fsn.render(tpl, cfg, tmp_path)
    assert f"sl={cfg.strategy.s1.sl_atr}" in text and "quotes=TMN" in text
    assert "auc=0.612 ok=yes md=0.5986" in text
    assert "live=TBD after challenge" in text and "- live-v1.0 first" in text
    assert len(problems) == 1 and "missing.key" in problems[0]


def test_repo_template_has_only_allowed_gaps() -> None:
    """Every placeholder of the real template resolves, except the walk-forward report before it exists."""
    cfg = load_config("config")
    text, problems = fsn.render((ROOT / "docs" / "STRATEGY.template.md").read_text(encoding="utf-8"), cfg)
    wf_missing = not (ROOT / "reports" / "backtest" / "walkforward" / "REPORT.md").exists()
    allowed = [p for p in problems if wf_missing and "walkforward/REPORT.md" in p]
    assert problems == allowed, problems
    for title in (
        "۱) معماری و منطق اصلی الگوریتم",
        "۲) نحوه استفاده از هوش مصنوعی و ابزارهای والکس",
        "۳) شرایط تصمیم‌گیری برای خرید و فروش",
        "۴) روش مدیریت سرمایه و ریسک",
        "۵) امکان اجرای مجدد و ارزیابی",
    ):
        assert title in text
