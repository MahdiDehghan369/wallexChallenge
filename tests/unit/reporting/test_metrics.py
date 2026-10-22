"""T-903: metrics on hand-computed trades."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from siavash.reporting.metrics import build_trades, compute_metrics, load_decisions

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def fills() -> pd.DataFrame:
    rows = [
        # P1: buy 1000 + fee 1, sell 1100 + fee 1.1 -> pnl 97.9 (S1, BTC)
        (T0, "BTC/TMN", "buy", 100, 10, 1000, 1, "TMN", "P1", "S1_TREND_BREAKOUT"),
        (T0 + timedelta(hours=2), "BTC/TMN", "sell", 110, 10, 1100, 1.1, "TMN", "P1", "S1_TREND_BREAKOUT"),
        # P2: buy 2000 fee 2 (two fills), sell 1900 fee 1.9 -> pnl -103.9 (S3, ETH)
        (T0 + timedelta(hours=3), "ETH/TMN", "buy", 50, 20, 1000, 1, "TMN", "P2", "S3_IMPLIED_DEV"),
        (T0 + timedelta(hours=3), "ETH/TMN", "buy", 50, 20, 1000, 1, "TMN", "P2", "S3_IMPLIED_DEV"),
        (T0 + timedelta(hours=5), "ETH/TMN", "sell", 47.5, 40, 1900, 1.9, "TMN", "P2", "S3_IMPLIED_DEV"),
        # P3: still open (no sell)
        (T0 + timedelta(hours=6), "BTC/TMN", "buy", 100, 5, 500, 0.5, "TMN", "P3", "S1_TREND_BREAKOUT"),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "ts",
            "symbol",
            "side",
            "price",
            "qty",
            "notional_tmn",
            "fee",
            "fee_asset",
            "position_id",
            "strategy",
        ],
    )


def equity() -> pd.DataFrame:
    vals = [5000, 5100, 5050, 4950, 5000, 5200, 5150]
    return pd.DataFrame(
        {
            "ts": [T0 + timedelta(hours=i) for i in range(len(vals))],
            "equity_tmn": vals,
            "exposure_tmn": [0, 1000, 1000, 2000, 0, 500, 500],
        }
    )


def decisions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "decision_id": "d1",
                "ts": T0,
                "type": "ENTRY_CANDIDATE",
                "outcome_action": "ORDER_PLACED",
                "position_id": "P1",
                "regime": "TREND_UP",
                "rejected_at": None,
                "reject_code": None,
                "llm_verdict": "APPROVE",
                "llm_status": "ok",
                "exit_reason": None,
            },
            {
                "decision_id": "d2",
                "ts": T0,
                "type": "ENTRY_CANDIDATE",
                "outcome_action": "ORDER_PLACED",
                "position_id": "P2",
                "regime": "RANGE",
                "rejected_at": None,
                "reject_code": None,
                "llm_verdict": "APPROVE",
                "llm_status": "fallback",
                "exit_reason": None,
            },
            {
                "decision_id": "d3",
                "ts": T0,
                "type": "ENTRY_CANDIDATE",
                "outcome_action": "REJECTED",
                "position_id": None,
                "regime": "RANGE",
                "rejected_at": "ML",
                "reject_code": "ML_BELOW_THRESHOLD",
                "llm_verdict": None,
                "llm_status": None,
                "exit_reason": None,
            },
            {
                "decision_id": "d4",
                "ts": T0,
                "type": "ENTRY_CANDIDATE",
                "outcome_action": "REJECTED",
                "position_id": None,
                "regime": "RANGE",
                "rejected_at": "LLM",
                "reject_code": "LLM_REJECT",
                "llm_verdict": "REJECT",
                "llm_status": "ok",
                "exit_reason": None,
            },
            {
                "decision_id": "d5",
                "ts": T0,
                "type": "EXIT",
                "outcome_action": "EXITED",
                "position_id": "P1",
                "regime": None,
                "rejected_at": None,
                "reject_code": None,
                "llm_verdict": None,
                "llm_status": None,
                "exit_reason": "TAKE_PROFIT",
            },
        ]
    )


def test_trades() -> None:
    t = build_trades(fills(), decisions())
    assert list(t["position_id"]) == ["P1", "P2"]
    assert t.loc[0, "pnl_tmn"] == pytest.approx(97.9) and t.loc[1, "pnl_tmn"] == pytest.approx(-103.9)
    assert t.loc[0, "regime"] == "TREND_UP" and t.loc[0, "exit_reason"] == "TAKE_PROFIT"
    assert t.loc[0, "ret_pct"] == pytest.approx(9.79) and t.loc[0, "hold_hours"] == 2


def test_metrics() -> None:
    m = compute_metrics(fills(), equity(), decisions(), capital_base=5000)
    assert m["net_return_pct"] == pytest.approx(3.0) and m["pnl_tmn"] == pytest.approx(150)
    assert m["max_drawdown_pct"] == pytest.approx((5100 - 4950) / 5100 * 100, rel=1e-4)
    assert m["max_drawdown_duration_h"] == pytest.approx(3.0)  # 5100 at h1 -> recovered at h5
    assert m["n_trades"] == 2 and m["win_rate"] == 0.5
    assert m["profit_factor"] == pytest.approx(97.9 / 103.9, rel=1e-4)
    assert m["expectancy_tmn"] == pytest.approx(-3.0) and m["avg_win_tmn"] == pytest.approx(97.9)
    assert m["total_volume_tmn"] == pytest.approx(6500) and m["total_fees_tmn"] == pytest.approx(6.5)
    assert m["avg_trade_volume_tmn"] == pytest.approx(3250) and m["n_fills"] == 6
    assert m["trades_per_day"] == pytest.approx(2 / 0.25) and m["sharpe_annual"] is not None
    assert m["by_strategy"]["S1_TREND_BREAKOUT"]["pnl_tmn"] == pytest.approx(97.9)
    assert set(m["by_symbol"]) == {"BTC/TMN", "ETH/TMN"} and set(m["by_regime"]) == {"TREND_UP", "RANGE"}
    p = m["pipeline"]
    assert p["candidates"] == 4 and p["placed"] == 2 and p["rejected_by_stage"] == {"ML": 1, "LLM": 1}
    assert p["llm_approve_pct"] == pytest.approx(66.67) and p["llm_fallbacks"] == 1


def test_empty_inputs() -> None:
    m = compute_metrics(
        pd.DataFrame(columns=fills().columns),
        pd.DataFrame(columns=["ts", "equity_tmn"]),
        load_decisions("/nonexistent"),
        5000,
    )
    assert m["net_return_pct"] == 0 and m["n_trades"] == 0 and m["pipeline"]["candidates"] == 0
