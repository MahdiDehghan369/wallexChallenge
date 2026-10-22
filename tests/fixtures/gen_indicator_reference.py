"""Independent loop-based reference implementation of the indicators (T-301).

Writes ``tests/fixtures/indicators_reference.csv`` (input OHLCV + reference values). Uses only
plain Python lists/loops, no pandas rolling/ewm, so it is independent from
``siavash.features.indicators``. Regenerate with:

    python tests/fixtures/gen_indicator_reference.py
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

NAN = float("nan")
OUT = Path(__file__).with_name("indicators_reference.csv")


def _isnan(x: float) -> bool:
    return x != x


def ema(xs: list[float], n: int) -> list[float]:
    a = 2.0 / (n + 1)
    out, prev = [], NAN
    for i, x in enumerate(xs):
        prev = x if i == 0 else a * x + (1 - a) * prev
        out.append(prev if i >= n - 1 else NAN)
    return out


def ema_nan_aware(xs: list[float], n: int) -> list[float]:
    """EMA over a series with leading NaNs; seeded at the first valid value, NaN for n-1 valid bars."""
    a = 2.0 / (n + 1)
    out, prev, count = [], NAN, 0
    for x in xs:
        if _isnan(x) and count == 0:
            out.append(NAN)
            continue
        prev = x if count == 0 else a * x + (1 - a) * prev
        count += 1
        out.append(prev if count >= n else NAN)
    return out


def wilder(xs: list[float], n: int) -> list[float]:
    out = [NAN] * len(xs)
    first = next((i for i, x in enumerate(xs) if not _isnan(x)), None)
    if first is None or first + n > len(xs):
        return out
    seed = sum(xs[first : first + n]) / n
    out[first + n - 1] = seed
    prev = seed
    for i in range(first + n, len(xs)):
        prev = prev + (xs[i] - prev) / n
        out[i] = prev
    return out


def rolling(xs: list[float], n: int, fn) -> list[float]:  # type: ignore[no-untyped-def]
    out = []
    for i in range(len(xs)):
        win = xs[i - n + 1 : i + 1] if i >= n - 1 else None
        out.append(fn(win) if win is not None and not any(_isnan(v) for v in win) else NAN)
    return out


def mean(w: list[float]) -> float:
    return sum(w) / len(w)


def pstd(w: list[float]) -> float:
    m = mean(w)
    return math.sqrt(sum((v - m) ** 2 for v in w) / len(w))


def true_range(h: list[float], lo: list[float], c: list[float]) -> list[float]:
    out = [h[0] - lo[0]]
    for i in range(1, len(c)):
        out.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    return out


def compute(rows: list[dict[str, float]]) -> list[dict[str, float]]:
    o = [r["open"] for r in rows]
    h = [r["high"] for r in rows]
    lo = [r["low"] for r in rows]
    c = [r["close"] for r in rows]
    v = [r["volume"] for r in rows]
    N = len(c)
    res: dict[str, list[float]] = {}
    res["ema20"] = ema(c, 20)
    res["sma20"] = rolling(c, 20, mean)
    # RSI
    gains = [NAN] + [max(c[i] - c[i - 1], 0.0) for i in range(1, N)]
    losses = [NAN] + [max(c[i - 1] - c[i], 0.0) for i in range(1, N)]
    ag, al = wilder(gains, 14), wilder(losses, 14)
    rsi = []
    for g, lo_ in zip(ag, al, strict=True):
        if _isnan(g):
            rsi.append(NAN)
        elif g == 0 and lo_ == 0:
            rsi.append(50.0)
        elif lo_ == 0:
            rsi.append(100.0)
        else:
            rsi.append(100.0 - 100.0 / (1.0 + g / lo_))
    res["rsi14"] = rsi
    tr = true_range(h, lo, c)
    res["atr14"] = wilder(tr, 14)
    mid = rolling(c, 20, mean)
    sd = rolling(c, 20, pstd)
    res["bb_mid"] = mid
    res["bb_upper"] = [m + 2 * s for m, s in zip(mid, sd, strict=True)]
    res["bb_lower"] = [m - 2 * s for m, s in zip(mid, sd, strict=True)]
    res["bb_width_pct"] = [(4 * s) / m * 100 if not _isnan(m) else NAN for m, s in zip(mid, sd, strict=True)]
    # ADX
    pdm, mdm, tr2 = [NAN], [NAN], [NAN]
    for i in range(1, N):
        up, dn = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        tr2.append(tr[i])
    satr, spdm, smdm = wilder(tr2, 14), wilder(pdm, 14), wilder(mdm, 14)
    dx = []
    for a, p, m in zip(satr, spdm, smdm, strict=True):
        if _isnan(a):
            dx.append(NAN)
            continue
        pdi, mdi = 100 * p / a, 100 * m / a
        dx.append(0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi))
    res["adx14"] = wilder(dx, 14)
    # MACD
    e12, e26 = ema(c, 12), ema(c, 26)
    line = [a - b if not (_isnan(a) or _isnan(b)) else NAN for a, b in zip(e12, e26, strict=True)]
    sig = ema_nan_aware(line, 9)
    res["macd"] = line
    res["macd_signal"] = sig
    res["macd_hist"] = [
        a - b if not (_isnan(a) or _isnan(b)) else NAN for a, b in zip(line, sig, strict=True)
    ]
    # Donchian (previous 20 bars)
    res["donchian_high"] = [max(h[i - 20 : i]) if i >= 20 else NAN for i in range(N)]
    res["donchian_low"] = [min(lo[i - 20 : i]) if i >= 20 else NAN for i in range(N)]
    # Stochastic
    k = []
    for i in range(N):
        if i < 13:
            k.append(NAN)
            continue
        ll, hh = min(lo[i - 13 : i + 1]), max(h[i - 13 : i + 1])
        k.append(50.0 if hh == ll else 100 * (c[i] - ll) / (hh - ll))
    res["stoch_k"] = k
    res["stoch_d"] = rolling(k, 3, mean)
    # OBV
    ob, acc = [], 0.0
    for i in range(N):
        if i > 0:
            acc += v[i] * (1 if c[i] > c[i - 1] else -1 if c[i] < c[i - 1] else 0)
        ob.append(acc)
    res["obv"] = ob
    # z-score of close over 20
    z = []
    for m, s, x in zip(mid, sd, c, strict=True):
        z.append(NAN if _isnan(m) else (0.0 if s == 0 else (x - m) / s))
    res["zscore20"] = z
    lr = [NAN] + [math.log(c[i]) - math.log(c[i - 1]) for i in range(1, N)]
    res["rv20"] = rolling(lr, 20, pstd)

    out = []
    for i in range(N):
        row = {"open": o[i], "high": h[i], "low": lo[i], "close": c[i], "volume": v[i]}
        row.update({k_: vals[i] for k_, vals in res.items()})
        out.append(row)
    return out


def main() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from siavash.data.synthetic import make_synthetic_market

    df = make_synthetic_market(seed=11, days=3)["BTC/USDT"]
    rows = [
        {"open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
        for r in df.itertuples(index=False)
    ]
    out = compute(rows)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        for r in out:
            w.writerow({k: repr(v) for k, v in r.items()})
    print(f"wrote {len(out)} rows to {OUT}")


if __name__ == "__main__":
    main()
