"""Synthetic data builders for the gpw-pair-trading tests (no network)."""
import datetime as dt
import json
import math
import random


def random_walk(n, seed, sigma=1.0, start=0.0):
    rng = random.Random(seed)
    x = [start]
    for _ in range(n - 1):
        x.append(x[-1] + rng.gauss(0, sigma))
    return x


def ar1(n, phi, seed, sigma=1.0):
    rng = random.Random(seed)
    x = [0.0]
    for _ in range(n - 1):
        x.append(phi * x[-1] + rng.gauss(0, sigma))
    return x


def cointegrated_prices(n, beta=1.2, phi=0.9, seed=7, noise=0.01, vol=0.02):
    """ln A = 0.3 + beta ln B + AR(1) noise; both legs priced around 50-100 PLN."""
    lb = [math.log(50.0) + v for v in random_walk(n, seed, vol)]
    u = ar1(n, phi, seed + 1, noise)
    la = [0.3 + beta * x + e for x, e in zip(lb, u)]
    return [math.exp(v) for v in la], [math.exp(v) for v in lb]


def session_days(n, end):
    """n probable GPW sessions ending at ``end`` (weekdays, Polish holidays skipped)."""
    import gpwlib.plcal as plcal
    out = []
    for d in plcal.previous_probable_sessions(end, n * 3):
        out.append(d)
        if len(out) == n:
            break
    return out[::-1]


def write_cache(root, days, prices, turnover_k=50000.0):
    """Write synthetic archive sessions {name: [closes]} into a gpwlib cache dir."""
    folder = root / "archive" / "shares"
    folder.mkdir(parents=True, exist_ok=True)
    for i, d in enumerate(days):
        rows = []
        for name, closes in prices.items():
            c = round(closes[i], 4)
            prev = closes[i - 1] if i else closes[i]
            rows.append({"name": name, "currency": "PLN", "open": c, "high": c, "low": c, "close": c,
                         "change_pct": round((c / prev - 1) * 100, 2), "turnover_k": turnover_k})
        payload = {"schema": 1, "date": d.isoformat(), "type": "shares", "session": True, "rows": rows,
                   "source_url": "synthetic", "retrieved_at": "2026-11-16T00:00:00+00:00"}
        (folder / "{}.json".format(d.isoformat())).write_text(json.dumps(payload), encoding="utf-8")
