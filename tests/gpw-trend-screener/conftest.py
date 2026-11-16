import math
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / "gpw-trend-screener" / "scripts"
for p in (str(SCRIPTS), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from trend_screener_fixtures import make_series, trading_dates, vcp_closes, vcp_turnover  # noqa: E402


@pytest.fixture
def vcp_series():
    c = vcp_closes()
    return make_series("VCPCO", c, vcp_turnover(len(c)))


@pytest.fixture
def universe():
    """VCP stock + 11 weaker stocks (flat, slow up, down) sharing the same dates."""
    c = vcp_closes()
    n = len(c)
    dates = trading_dates(n)
    out = {"VCPCO": make_series("VCPCO", c, vcp_turnover(n), dates)}
    for j in range(11):
        drift = (-0.001 + 0.0002 * j)
        closes = [40 * math.exp(drift * k) * (1 + 0.01 * math.sin(k / 3 + j)) for k in range(n)]
        out["W{}".format(j)] = make_series("W{}".format(j), closes, [800.0] * n, dates)
    return out
