import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "gpw-sector-trend" / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "gpw-market-breadth"))

import sector_engine as se  # noqa: E402
from gpwlib import panel  # noqa: E402
from gpwlib.universe import DEFAULTS, build_views, session_dates, is_uptrend  # noqa: E402
from test_breadth_engine import synthetic_sessions  # noqa: E402


def _series(share_up):
    p = panel.build_panel(synthetic_sessions(n=380, n_stocks=56, drift_up_share=share_up))
    sectors = list(se.SECTOR_INDICES)
    sector_map = {"S{}".format(j): sectors[j % len(sectors)] for j in range(56)}
    return se.ratio_series(build_views(p), session_dates(p), DEFAULTS, sector_map)


def test_uptrend_definition_on_trending_stock():
    p = panel.build_panel(synthetic_sessions(n=300, n_stocks=2, drift_up_share=0.5))
    v = build_views(p)
    up, down = v["S0"], v["S1"]
    assert is_uptrend(up, len(up.s) - 1) is True
    assert is_uptrend(down, len(down.s) - 1) is False


def test_ratio_series_and_snapshot():
    series = _series(0.75)
    last = series["all"][-1]
    assert 0.5 < last["ratio"] <= 1.0
    snap = se.sector_snapshot(series)
    assert len(snap) == 14
    assert all(s["total"] == 4 for s in snap)


def test_composite_strong_beats_weak():
    def score(share):
        series = _series(share)
        rows = series["all"]
        snap = se.sector_snapshot(series)
        comps = {
            "market_breadth": se.c1_market_breadth(rows),
            "sector_participation": se.c2_participation(snap),
            "sector_rotation": se.c3_rotation(snap),
            "momentum": se.c4_momentum(rows, snap),
            "historical_context": se.c5_history(rows),
        }
        return se.composite(comps)

    assert score(0.9)["score"] > score(0.1)["score"]


def test_status_thresholds():
    assert se.status_of(0.40) == "overbought"
    assert se.status_of(0.05) == "oversold"
    assert se.status_of(0.2) == "normal"


def test_warning_penalty_capped():
    na = {"score": 50, "data_available": True, "signal_key": "x", "details": {}}
    comps = {k: dict(na) for k in se.WEIGHTS}
    comps["sector_rotation"] = {"score": 50, "data_available": True, "signal_key": "x",
                                "details": {"late_cycle": True, "intra_group_divergence": True}}
    comps["sector_participation"] = {"score": 50, "data_available": True, "signal_key": "x", "details": {"spread": 0.5}}
    c = se.composite(comps)
    assert c["penalty"] == 9  # 5+3+3=11 -> capped at 10 -> -1 multi-warning discount
    assert c["score"] == pytest.approx(c["raw_score"] - c["penalty"])
