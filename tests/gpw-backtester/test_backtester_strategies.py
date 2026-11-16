import pytest

import backtester_strategies as bs
from backtester_fixtures import bars_from_closes, market, sessions


def _ok_rule(**over):
    doc = {"entry": {"all": [{"left": {"ind": "close"}, "op": ">", "right": {"ind": "sma", "n": 50}},
                             {"left": {"ind": "rsi", "n": 14}, "op": "<", "right": 70}]},
           "exit": {"left": {"ind": "close"}, "op": "crosses_below", "right": {"ind": "sma", "n": 50}},
           "stop": {"atr_mult": 2}}
    doc.update(over)
    return doc


def test_valid_rules_parse():
    r = bs.parse_rules(_ok_rule())
    assert r["entry"]["all"][0]["right"] == {"ind": "sma", "n": 50, "of": "close", "shift": 0, "mult": 1.0}
    assert r["rank"]["ind"] == "roc"
    st = bs.make("rules", rules=r)
    assert st.params["stop_atr"] == 2.0


@pytest.mark.parametrize("doc, msg", [
    ({"exit": {}}, "'entry' is required"),
    (_ok_rule(foo=1), "unknown top-level"),
    (_ok_rule(entry={"left": {"ind": "macd", "n": 3}, "op": ">", "right": 1}), "unknown indicator"),
    (_ok_rule(entry={"left": {"ind": "sma"}, "op": ">", "right": 1}), "integer n"),
    (_ok_rule(entry={"left": {"ind": "sma", "n": 5000}, "op": ">", "right": 1}), "integer n"),
    (_ok_rule(entry={"left": {"ind": "close", "n": 5}, "op": ">", "right": 1}), "takes no n"),
    (_ok_rule(entry={"left": {"ind": "close"}, "op": "==", "right": 1}), "op must be"),
    (_ok_rule(entry={"left": {"ind": "close"}, "op": ">", "right": "__import__('os')"}), "operand must be"),
    (_ok_rule(entry={"left": {"ind": "close"}, "op": ">", "right": True}), "booleans"),
    (_ok_rule(entry={"left": {"ind": "close", "shift": -1}, "op": ">", "right": 1}), "shift"),
    (_ok_rule(entry={"left": {"ind": "sma", "n": 5, "of": "volume"}, "op": ">", "right": 1}), "'of' must be"),
    (_ok_rule(entry={"all": []}), "non-empty"),
    (_ok_rule(entry={"all": [{"any": [{"all": [{"any": [{"left": 1, "op": ">", "right": 0}]}]}]}]}), "nested deeper"),
    (_ok_rule(stop={"pct": 80}), "out of range"),
    (_ok_rule(params={"eval": 1}), "params may only"),
    ({"entry": {"left": {"ind": "close"}, "op": ">", "right": 1}}, "way out"),
])
def test_rule_validation_errors(doc, msg):
    with pytest.raises(bs.RuleError) as e:
        bs.parse_rules(doc)
    assert msg in str(e.value)


def test_rules_from_bad_json_text():
    with pytest.raises(bs.RuleError):
        bs.parse_rules("{not json")


def test_too_many_conditions():
    c = {"left": {"ind": "close"}, "op": ">", "right": 1}
    with pytest.raises(bs.RuleError):
        bs.parse_rules({"entry": {"all": [c] * 31}, "stop": {"pct": 5}})


def test_unknown_strategy_and_param():
    with pytest.raises(ValueError):
        bs.make("magic")
    with pytest.raises(ValueError):
        bs.make("sma_cross", {"bogus": 1})
    with pytest.raises(ValueError):
        bs.make("sma_cross", {"fast": 200, "slow": 50})
    with pytest.raises(ValueError):
        bs.make("trend_template", {"stop_pct": "abc"})
    assert bs.make("sma_cross", {"fast": "20"}).params["fast"] == 20


def test_indicators_basic():
    days = sessions(40)
    closes = [float(k + 1) for k in range(40)]
    md = market({"A": bars_from_closes(days, closes)}, days)
    b = md.stocks["A"]
    assert bs.sma_s(b, 5)[4] == 3.0
    assert bs.highest_s(b, 5, "close")[10] == 11.0 and bs.lowest_s(b, 5, "close")[10] == 7.0
    assert bs.roc_s(b, 10)[20] == pytest.approx((21 / 11 - 1) * 100)
    assert bs.rsi_s(b, 14)[20] == 100.0  # only gains
    assert bs.median_s(b, 3, "close")[5] == 5.0
    assert bs.atr_s(b, 14)[13] is None and bs.atr_s(b, 14)[14] is not None


def test_trend_template_signals_on_steady_uptrend_only():
    days = sessions(320)
    up = [10.0 * (1.003 ** k) for k in range(320)]
    flat = [10.0 + (0.1 if k % 2 else 0.0) for k in range(320)]
    md = market({"UP": bars_from_closes(days, up), "FLAT": bars_from_closes(days, flat),
                 "UP2": bars_from_closes(days, [10.0 * (1.001 ** k) for k in range(320)])}, days)
    st = bs.make("trend_template", {"min_turnover_k": 0})
    st.prepare(md)
    names = {n for d in days for _, n in st.entry_candidates(d)}
    assert "UP" in names and "FLAT" not in names
    assert all(d >= days[221] for d in st.entries_by_date)


def test_sma_cross_signal_on_cross_day():
    days = sessions(300)
    closes = [20.0 - 0.02 * k for k in range(200)] + [16.0 + 0.2 * k for k in range(100)]
    md = market({"A": bars_from_closes(days, closes)}, days)
    st = bs.make("sma_cross", {"fast": 20, "slow": 100, "min_turnover_k": 0})
    st.prepare(md)
    hits = [d for d in days if st.entry_candidates(d)]
    assert len(hits) == 1


def test_month_end_dates():
    assert bs.month_end_dates(["2021-01-28", "2021-01-29", "2021-02-01", "2021-02-26"]) == {"2021-01-29", "2021-02-26"}


def test_shipped_example_rules_are_valid():
    from pathlib import Path
    p = Path(bs.__file__).resolve().parents[1] / "references" / "example_rules.json"
    r = bs.parse_rules(p.read_text(encoding="utf-8"))
    assert r["max_hold"] == 30 and r["stop"] == {"atr_mult": 2.5}
