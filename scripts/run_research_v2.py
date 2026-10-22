#!/usr/bin/env python
"""Research v2 (pre-registered: reports/research_v2/PREREGISTRATION.md). Research only.

    python scripts/run_research_v2.py universe   # resolve DEV / HOLD-OUT markets (volume, coverage, spread)
    python scripts/run_research_v2.py dev        # DEV walk-forward → reports/research_v2/dev.json, dev.md
    python scripts/run_research_v2.py holdout    # ONCE: hold-out → holdout.json
    python scripts/run_research_v2.py tables     # per-hypothesis tables → results_tables.md

No orders, no config changes; reads config/research_v2.yaml.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from siavash.backtest.market_data import processed_frames
from siavash.config import load_config
from siavash.core.types import Timeframe
from siavash.logging import setup_logging
from siavash.research import v2

OUT = Path("reports/research_v2")
GUARD = OUT / "holdout_evaluated.json"


def _period(rc: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    end = pd.Timestamp(rc["data_end_utc"])
    start = end - pd.Timedelta(days=120)
    return start, start + pd.Timedelta(days=float(rc["warmup_days"])), end


def _coverage(df: pd.DataFrame, s: pd.Timestamp, e: pd.Timestamp) -> float:
    d = df.dropna(subset=["close"])
    d = d[(d["ts_open"] >= s) & (d["ts_open"] < e)]
    return len(d["ts_open"].unique()) / ((e - s) / v2.BAR)


def universe(rc: dict[str, Any], dev_dir: str, ho_dir: str) -> dict[str, Any]:
    u = rc["universe"]
    start, ev, end = _period(rc)
    spreads = json.loads((OUT / "spreads.json").read_text(encoding="utf-8"))["median_spread_pct"]
    flags = json.loads(
        Path("docs/vendor/wallex/samples/hector_markets_flags.json").read_text(encoding="utf-8")
    )
    vol = {
        f"{m['base_asset']}/{m['quote_asset']}": m.get("quote_volume_24h") or 0
        for m in flags["result"]["markets"]
    }
    deny = {
        line.strip()
        for line in Path("config/denylist.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    dev_f, ho_f = processed_frames(dev_dir), processed_frames(ho_dir)
    exclude, max_sp = set(u["exclude"]), float(u["max_median_spread_pct"])
    rows = []

    def check(sym: str, frames: dict[str, Any], split: str) -> None:
        why = []
        if sym in exclude:
            why.append("excluded (H4 list / stablecoin)")
        if sym in deny:
            why.append("denylist")
        sp = spreads.get(sym)
        if sp is None:
            why.append("no spread measurement")
        elif sp > max_sp:
            why.append(f"median spread {sp:.3f} % > {max_sp}")
        cov = _coverage(frames[sym][Timeframe.M15], ev, end) if sym in frames else 0.0
        pair = sym.replace("/TMN", "/USDT")
        if split == "holdout":
            if cov < float(u["holdout_min_bar_coverage"]):
                why.append(f"coverage {cov:.2f} < {u['holdout_min_bar_coverage']}")
            if pair not in frames:
                why.append("no X/USDT data")
            if vol.get(sym, 0) < float(u["holdout_min_quote_volume_24h"]):
                why.append("24h volume below threshold")
        rows.append(
            {
                "symbol": sym,
                "split": split,
                "spread": sp,
                "coverage": round(cov, 3),
                "vol24h": vol.get(sym, 0),
                "usdt_pair": pair in frames,
                "ok": not why,
                "why": "; ".join(why),
            }
        )

    for s in u["dev"]:
        check(s, dev_f, "dev")
    for s in sorted(k for k in ho_f if k.endswith("/TMN")):
        check(s, ho_f, "holdout")
    res = {
        "period": [str(start), str(ev), str(end)],
        "dev": [r["symbol"] for r in rows if r["split"] == "dev" and r["ok"]],
        "holdout": [r["symbol"] for r in rows if r["split"] == "holdout" and r["ok"]],
        "rows": rows,
    }
    (OUT / "universe.json").write_text(v2.to_json(res), encoding="utf-8")
    lines = [
        "# Research v2: resolved universe (addendum to the pre-registration)",
        "",
        f"Resolved {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/run_research_v2.py universe`, **before** "
        "any hypothesis was run. It uses only the pre-registered rules: volume, bar coverage, measured spread "
        "and the H4 list. Spreads come from `spreads.json`, i.e. `GET /depth/all` snapshots.",
        "",
        f"- period: {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC; setups count from {ev:%Y-%m-%d %H:%M} (warm-up)",
        f"- **DEV traded ({len(res['dev'])}):** {', '.join(res['dev']) or '—'}",
        f"- **HOLD-OUT traded ({len(res['holdout'])}):** {', '.join(res['holdout']) or '—'}",
        "",
        "| market | split | median spread % | 15m coverage | 24h quote vol (1e9 TMN) | X/USDT | used | reason |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for r in rows:
        sp = "—" if r["spread"] is None else f"{r['spread']:.3f}"
        lines.append(
            f"| {r['symbol']} | {r['split']} | {sp} | {r['coverage']:.2f} | {r['vol24h'] / 1e9:.2f} | "
            f"{'yes' if r['usdt_pair'] else 'no'} | {'**yes**' if r['ok'] else 'no'} | {r['why'] or '—'} |"
        )
    (OUT / "UNIVERSE.md").write_text("\n".join([*lines, ""]), encoding="utf-8")
    return res


def _trades(
    rc: dict[str, Any], cfg: Any, frames_dir: str, symbols: list[str], dev_dir: str
) -> tuple[pd.DataFrame, dict]:
    start, ev, end = _period(rc)
    frames = processed_frames(frames_dir)
    usdt = {**frames, **processed_frames(dev_dir, [rc["universe"]["usdt_tmn"]])}
    t, dense = v2.build_trades(frames, symbols, usdt, rc, cfg, start, end)
    t = v2.add_returns(t, dense, [float(x) for x in rc["fee_rt_pct"]], ev)
    return t, dense


def _f(x: Any, nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def dev_md(d: dict[str, Any], rc: dict[str, Any], uni: dict[str, Any]) -> str:
    fees = [f"{float(x):g}" for x in rc["fee_rt_pct"]]
    lines = [
        "# Research v2: DEV walk-forward",
        "",
        f"Markets ({len(uni['dev'])}): {', '.join(uni['dev'])}. {len(d['edges']) - 1} windows of "
        f"{rc['window_days']} days; OOS = windows 1… ({d['oos_days']:.0f} days). Live fee filter applied per fee. "
        "Net = after fees (rt / 2 per leg) and `base.bt` slippage on taker legs.",
        "",
        "## Walk-forward OOS per hypothesis",
        "",
        "| hyp | RT % | OOS n | trades/day | win % | mean gross % | drift-adj. % | idx-adj. % | mean net % | windows > 0 | picks |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for h, res in d["hyp"].items():
        for r in fees:
            x = res[r]
            picks = ", ".join((p or "none").split("[")[-1].rstrip("]") for p in x["picks"])
            lines.append(
                f"| {h} | {r} | {x['n']} | {x['trades_per_day']:.2f} | {_f(x['win'], 1)} | {_f(x['mean_gross'])} | "
                f"{_f(x['excess'])} | {_f(x['excess_idx'])} | {_f(x['mean_net'])} | {x['windows_pos']}/{x['windows_oos']} | {picks} |"
            )
    lines += [
        "",
        "## Every variant, whole DEV period (in-sample, reference only)",
        "",
        "| variant | n | gross % | drift-adj. % | idx-adj. % | hold h | "
        + " | ".join(f"net @{r} % [n]" for r in fees)
        + " |",
        "|---|---:|---:|---:|---:|---:|" + "---:|" * len(fees),
    ]
    for k, x in d["variants"].items():
        lines.append(
            f"| {k} | {x['n']} | {_f(x['gross'])} | {_f(x['excess'])} | {_f(x['excess_idx'])} | {_f(x['hold_h'], 1)} | "
            + " | ".join(f"{_f(x[f'net_{r}'])} [{x[f'n_{r}']}]" for r in fees)
            + " |"
        )
    return "\n".join([*lines, ""])


CHECK_LABEL = {
    "oos_mean_net": "DEV OOS mean net > {oos_mean_net_pct} %",
    "windows_positive": "OOS windows positive ≥ {oos_windows_positive:.0%}",
    "drift_adj_gross": "OOS drift-adj. gross > {drift_adj_gross_pct} %",
    "trades_per_day": "DEV OOS trades/day ≥ {min_trades_per_day}",
    "holdout_mean_net": "HOLD-OUT mean net > {holdout_mean_net_pct} %",
    "holdout_equity_dd": "HOLD-OUT equity DD < {dd} %",
}


def summary_tables(d: dict[str, Any], ho: dict[str, Any], ver: dict[str, Any], rc: dict[str, Any]) -> str:
    """Per-hypothesis tables for SUMMARY.md (DEV OOS + hold-out per fee, then the six checks)."""
    fees = [f"{float(x):g}" for x in rc["fee_rt_pct"]]
    labels = {
        k: v.format(**rc["pass"], dd=rc["portfolio"]["max_drawdown_pct"]) for k, v in CHECK_LABEL.items()
    }
    out = []
    for h in v2.HYPOTHESES:
        out += [
            f"### {h}: {'**PASS**' if ver[h]['pass'] else 'FAIL'}",
            "",
            "| RT % | DEV OOS n | trades/day | mean gross % | drift-adj. % | idx-adj. % | mean net % | windows > 0 "
            "| HOLD-OUT variant | HO n | HO gross % | HO drift-adj. % | HO mean net % | HO equity DD % |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|",
        ]
        for r in fees:
            x, y = d["hyp"][h][r], ho[h][r]
            var = (y["variant"] or "none").split("[")[-1].rstrip("]")
            out.append(
                f"| {r} | {x['n']} | {x['trades_per_day']:.2f} | {_f(x['mean_gross'])} | {_f(x['excess'])} | "
                f"{_f(x['excess_idx'])} | {_f(x['mean_net'])} | {x['windows_pos']}/{x['windows_oos']} | {var} | "
                f"{y['n']} | {_f(y['mean_gross'])} | {_f(y['excess'])} | {_f(y['mean_net'])} | {_f(y['equity_dd'], 2)} |"
            )
        out += ["", f"Checks at RT {float(rc['pass_fee_rt_pct']):g} %:", ""]
        for k, (v, ok) in ver[h]["checks"].items():
            out.append(f"- {'✅' if ok else '❌'} {labels[k]}: {_f(v)}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["universe", "dev", "holdout", "tables"])
    ap.add_argument("--config", default="config")
    ap.add_argument("--research-config", default="config/research_v2.yaml")
    ap.add_argument("--dev-data", default="data/processed")
    ap.add_argument("--holdout-data", default="data/processed_v2")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    rc = v2.load_v2(args.research_config)
    setup_logging("WARNING", cfg.base.paths.logs)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.step == "universe":
        u = universe(rc, args.dev_data, args.holdout_data)
        print(f"DEV {len(u['dev'])}: {u['dev']}\nHOLD-OUT {len(u['holdout'])}: {u['holdout']}")
        return 0
    if args.step == "tables":  # re-renders from saved JSON; never re-runs the hold-out
        d = json.loads((OUT / "dev.json").read_text(encoding="utf-8"))
        h = json.loads((OUT / "holdout.json").read_text(encoding="utf-8"))
        (OUT / "results_tables.md").write_text(
            summary_tables(d, h["holdout"], h["verdict"], rc), encoding="utf-8"
        )
        print(f"wrote {OUT / 'results_tables.md'}")
        return 0
    uni = json.loads((OUT / "universe.json").read_text(encoding="utf-8"))
    _, ev, end = _period(rc)
    if args.step == "dev":
        t, _ = _trades(rc, cfg, args.dev_data, uni["dev"], args.dev_data)
        Path("data/research_v2").mkdir(parents=True, exist_ok=True)
        t.to_parquet("data/research_v2/dev_trades.parquet")
        d = v2.dev_evaluation(t, rc, cfg, ev, end)
        d["picks_for_holdout"] = v2.dev_picks(t, rc, cfg)
        d["verdict_dev_only"] = v2.verdict(d, None, rc)
        (OUT / "dev.json").write_text(v2.to_json(d), encoding="utf-8")
        (OUT / "dev.md").write_text(dev_md(d, rc, uni), encoding="utf-8")
        print(f"dev: {len(t)} trades → {OUT / 'dev.md'}")
        return 0
    if GUARD.exists():
        print(f"hold-out already evaluated ({GUARD}); refusing a second run", file=sys.stderr)
        return 3
    d = json.loads((OUT / "dev.json").read_text(encoding="utf-8"))
    t, _ = _trades(rc, cfg, args.holdout_data, uni["holdout"], args.dev_data)
    t.to_parquet("data/research_v2/holdout_trades.parquet")
    days = (end - ev).total_seconds() / 86400
    ho = v2.holdout_evaluation(t, d["picks_for_holdout"], rc, cfg, days)
    ver = v2.verdict(d, ho, rc)
    GUARD.write_text(
        json.dumps(
            {"evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"), "markets": uni["holdout"]}
        ),
        encoding="utf-8",
    )
    (OUT / "holdout.json").write_text(v2.to_json({"holdout": ho, "verdict": ver}), encoding="utf-8")
    print(v2.to_json(ver))
    return 0


if __name__ == "__main__":
    sys.exit(main())
