import datetime as dt
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "gpw-market-breadth" / "scripts"))

import breadth_engine as be  # noqa: E402
from gpwlib import panel  # noqa: E402
from gpwlib.universe import DEFAULTS, build_views, session_dates  # noqa: E402


def _dates(n):
    d, out = dt.date(2024, 1, 1), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def synthetic_sessions(n=360, n_stocks=60, drift_up_share=0.8):
    """n_stocks synthetic shares; a share of them trend up, the rest down."""
    dates = _dates(n)
    sessions = []
    for k, d in enumerate(dates):
        rows = []
        for j in range(n_stocks):
            up = j < n_stocks * drift_up_share
            base = 50 + j
            trend = (1.0015 if up else 0.9985) ** k
            wiggle = 1 + 0.01 * math.sin(k / 3 + j)
            close = base * trend * wiggle
            rows.append({"name": "S{}".format(j), "currency": "PLN", "close": close, "high": close * 1.01,
                         "low": close * 0.99, "turnover_k": 500.0, "change_pct": None})
        sessions.append({"date": d, "rows": rows})
    return sessions


def _rows(share_up):
    p = panel.build_panel(synthetic_sessions(drift_up_share=share_up))
    v = build_views(p)
    return be.daily_breadth(v, session_dates(p), DEFAULTS, None, 120)


def test_daily_breadth_reflects_participation():
    rows = _rows(0.8)
    assert rows, "breadth rows expected after 200-session warm-up"
    assert rows[-1]["eligible"] == 60
    assert rows[-1]["pct_above_200"] == pytest.approx(0.8, abs=0.05)


def test_components_and_composite_strong_vs_weak():
    strong = _rows(0.9)
    weak = _rows(0.1)

    def score(rows):
        comps = {
            "breadth_level_trend": be.c1_level_trend(rows),
            "ma_crossover": be.c2_crossover(rows),
            "advance_decline": be.c3_advance_decline(rows),
            "new_highs_lows": be.c4_new_highs_lows(rows, 120),
            "size_participation": be.c5_size_participation(rows),
            "index_divergence": be.c6_divergence(None, rows),
        }
        return be.composite(comps)

    s, w = score(strong), score(weak)
    assert s["score"] > 60 > w["score"]
    assert s["data_quality"] == "complete" or s["components_available"] >= 4
    # unavailable components are re-weighted, weights sum to 1
    assert sum(s["effective_weights"].values()) == pytest.approx(1.0, abs=1e-3)


def test_zone_mapping():
    assert be.zone_for(85)[0] == "strong"
    assert be.zone_for(60)[0] == "healthy"
    assert be.zone_for(59.9)[0] == "neutral"
    assert be.zone_for(5)[0] == "critical"


def test_composite_all_missing_defaults_to_50():
    na = {"score": None, "data_available": False, "signal_key": "no_data", "details": {}}
    c = be.composite({k: dict(na) for k in be.WEIGHTS})
    assert c["score"] == 50.0 and c["data_quality"] == "limited"


def test_divergence_bearish_when_index_up_breadth_down():
    rows = [{"date": "d{:03d}".format(i), "breadth_fast": 0.7 - i * 0.002} for i in range(70)]
    idx = panel.Series("WIG")
    for i, r in enumerate(rows):
        idx.dates.append(r["date"])
        idx.close.append(1000 + i * 3)
    c6 = be.c6_divergence(idx, rows)
    assert c6["details"]["w60"]["pattern"] == "bearish_divergence"
    assert c6["score"] < 30
