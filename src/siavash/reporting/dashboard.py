"""Static demo dashboard (T-1204): ``reports/live/dashboard.html``, rebuilt every 60 s with ``--watch``.

Equity / drawdown (inline SVG), open positions and breaker state (``state/state.sqlite``), the current
regime (``logs/regime.jsonl``) and the last 20 decisions with their ``final_reason``. No server, no
external assets — it only exists for the demo video.

    python -m siavash.reporting.dashboard [--logs logs] [--state state] [--out reports/live/dashboard.html] [--watch]
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from siavash.reporting.metrics import load_equity

REFRESH_S = 60


def _svg_line(values: list[float], width: int = 640, height: int = 160, color: str = "#2563eb") -> str:
    if len(values) < 2:
        return f'<svg width="{width}" height="{height}"><text x="10" y="20">no data yet</text></svg>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = " ".join(
        f"{i * (width - 10) / (len(values) - 1) + 5:.1f},{height - 5 - (v - lo) / span * (height - 10):.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{pts}"/>'
        f'<text x="6" y="14" font-size="11">{hi:,.0f}</text><text x="6" y="{height - 6}" font-size="11">{lo:,.0f}</text></svg>'
    )


def _state(state_db: Path) -> tuple[list[dict[str, Any]], str]:
    if not state_db.exists():
        return [], "unknown (no state)"
    with sqlite3.connect(state_db) as con:
        rows = con.execute("SELECT data FROM positions WHERE status != 'CLOSED'").fetchall()
        br = con.execute("SELECT state, reason FROM breaker WHERE id = 1").fetchone()
    positions = [json.loads(r[0]) for r in rows]
    return positions, (f"{br[0]} — {br[1] or ''}" if br else "NORMAL")


def _tail_jsonl(path: Path, n: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()][-n:]
    out = []
    for x in lines:
        try:
            out.append(json.loads(x))
        except json.JSONDecodeError:
            continue
    return out


def build_dashboard(
    logs: str | Path = "logs", state: str | Path = "state", out: str | Path = "reports/live/dashboard.html"
) -> Path:
    logs, state, out = Path(logs), Path(state), Path(out)
    eq = load_equity(logs / "equity.csv")
    values = eq["equity_tmn"].astype(float).tolist() if not eq.empty else []
    dd = (
        ((pd.Series(values).cummax() - pd.Series(values)) / pd.Series(values).cummax() * 100).tolist()
        if values
        else []
    )
    positions, breaker = _state(state / "state.sqlite")
    regime = (_tail_jsonl(logs / "regime.jsonl", 1) or [{}])[-1]
    decisions = list(reversed(_tail_jsonl(logs / "decisions.jsonl", 20)))
    e = html.escape
    pos_rows = (
        "".join(
            f"<tr><td>{e(str(p.get('symbol')))}</td><td>{e(str(p.get('strategy')))}</td><td>{e(str(p.get('qty')))}</td>"
            f"<td>{e(str(p.get('entry_price')))}</td><td>{e(str(p.get('stop_loss')))}</td><td>{e(str(p.get('status')))}</td></tr>"
            for p in positions
        )
        or '<tr><td colspan="6">no open position</td></tr>'
    )
    dec_rows = (
        "".join(
            f"<tr><td>{e(str(d.get('ts', ''))[:19])}</td><td>{e(str(d.get('type')))}</td><td>{e(str(d.get('symbol') or ''))}</td>"
            f"<td>{e(str(d.get('outcome_action') or ''))}</td><td>{e(str(d.get('final_reason') or ''))}</td></tr>"
            for d in decisions
        )
        or '<tr><td colspan="5">no decision yet</td></tr>'
    )
    last = f"{values[-1]:,.0f} TMN" if values else "—"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_S}"><title>Siavash dashboard</title>
<style>
 :root {{ --bg:#fff; --fg:#111; --muted:#666; --line:#ddd; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#999; --line:#333; }} }}
 body {{ background:var(--bg); color:var(--fg); font:14px system-ui,sans-serif; padding:8px 16px; max-width:1100px; }}
 table {{ border-collapse:collapse; width:100%; }} td,th {{ border-bottom:1px solid var(--line); padding:4px 6px; text-align:left; }}
 .kpi {{ display:inline-block; padding-right:24px; }} .kpi b {{ font-size:20px; }} small {{ color:var(--muted); }} svg {{ max-width:100%; }}
</style></head><body>
<h1>Siavash — live dashboard</h1>
<small>generated {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC · refreshes every {REFRESH_S} s</small>
<p><span class="kpi">equity<br><b>{last}</b></span><span class="kpi">max drawdown<br><b>{(max(dd) if dd else 0):.2f} %</b></span>
<span class="kpi">breaker<br><b>{e(breaker)}</b></span><span class="kpi">regime<br><b>{e(str(regime.get("regime", "—")))}</b></span></p>
<h2>Equity</h2>{_svg_line(values)}
<h2>Drawdown %</h2>{_svg_line([-x for x in dd], color="#dc2626")}
<h2>Open positions</h2><table><tr><th>symbol</th><th>strategy</th><th>qty</th><th>entry</th><th>stop</th><th>status</th></tr>{pos_rows}</table>
<h2>Last 20 decisions</h2><table><tr><th>ts (UTC)</th><th>type</th><th>symbol</th><th>outcome</th><th>reason</th></tr>{dec_rows}</table>
</body></html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--state", default="state")
    ap.add_argument("--out", default="reports/live/dashboard.html")
    ap.add_argument("--watch", action="store_true", help=f"rebuild every {REFRESH_S} s")
    args = ap.parse_args(argv)
    while True:
        print(f"dashboard: {build_dashboard(args.logs, args.state, args.out)}")
        if not args.watch:
            return 0
        time.sleep(REFRESH_S)


if __name__ == "__main__":
    raise SystemExit(main())
