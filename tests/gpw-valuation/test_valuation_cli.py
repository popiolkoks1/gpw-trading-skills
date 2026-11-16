"""CLI: argument errors, input validation and full offline runs on synthetic data."""

import datetime as dt
import json

import pytest

import gpw_valuation as gv
import valuation_data as vd


class S:
    def __init__(self, dates, close):
        self.dates, self.close = dates, close


def _series(beta=1.1, n=520, last=100.0):
    start = dt.date(2024, 11, 4)
    dates, mk, st = [], [], []
    m, s = 1000.0, 1.0
    for i in range(n * 7 // 5):
        day = start + dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        r = 0.004 * ((i * 7919) % 11 - 5) / 5 + 0.0003 * ((i * 104729) % 7 - 3)
        m *= 1 + r
        s *= 1 + beta * r
        dates.append(day.isoformat())
        mk.append(m)
        st.append(s)
    k = last / st[-1]
    return S(dates, [x * k for x in st]), S(dates, mk)


INDUSTRIAL = {
    "ticker": "TESTCO", "isin": "PLTEST000001", "retrieved_at": "2026-11-16T12:00:00+00:00", "warnings": [],
    "sources": ["https://www.gpw.pl/spolka?isin=PLTEST000001"],
    "indicators": {"isin": "PLTEST000001", "sector": "przemysł", "shares_issued": 10_000_000,
                   "market_cap_pln": 1.0e9, "book_value_pln": 4.0e8, "pbv": 2.5, "pe": 12.5,
                   "dividend_yield_pct": 4.0, "other": {}},
    "notoria": {"period_label": "I-III kw. 2026", "consolidated": True,
                "period": {"year": 2026, "first_quarter": 1, "last_quarter": 3, "months": 9, "cumulative": True,
                           "period_end": "2026-09-30"},
                "values": {"revenue": 6.0e8, "operating_profit": 9.0e7, "pretax_profit": 8.4e7,
                           "net_profit_parent": 6.0e7, "depreciation_amortization": 3.0e7, "ebitda": 1.2e8,
                           "equity_parent": 4.0e8, "current_assets": 2.5e8, "current_liabilities": 2.0e8,
                           "noncurrent_liabilities": 1.5e8, "cfo": 9.0e7, "capex": -3.6e7}},
}

BANK = {
    "ticker": "TESTBANK", "isin": "PLTEST000002", "retrieved_at": "2026-11-16T12:00:00+00:00", "warnings": [],
    "sources": ["https://www.gpw.pl/spolka?isin=PLTEST000002"],
    "indicators": {"isin": "PLTEST000002", "sector": "banki komercyjne", "shares_issued": 100_000_000,
                   "market_cap_pln": 6.0e9, "book_value_pln": 4.0e9, "pbv": 1.5, "pe": 10.0,
                   "dividend_yield_pct": 6.0, "other": {}},
    "notoria": {"period_label": "I-III kw. 2026", "consolidated": True,
                "period": {"year": 2026, "first_quarter": 1, "last_quarter": 3, "months": 9, "cumulative": True,
                           "period_end": "2026-09-30"},
                "values": {"revenue": 3.0e9, "operating_profit": 1.0e9, "pretax_profit": 9.0e8,
                           "net_profit_parent": 4.5e8, "equity_parent": 4.0e9}},
}


@pytest.fixture
def env(monkeypatch):
    """Patch every network/cache touch point; returns a dict to tweak the company."""
    state = {"snap": INDUSTRIAL, "price": 100.0, "beta": 1.1}

    def resolve(ticker, cache_dir=None, offline=False, explicit=None):
        return {"ticker": state["snap"]["ticker"], "isin": state["snap"]["isin"], "source": "explicit"}

    def price_series(args, warnings):
        s, w = _series(state["beta"], last=state["price"])
        return [None] * 504, {state["snap"]["ticker"]: s}, w

    monkeypatch.setattr(gv.fundamentals, "resolve_isin", resolve)
    monkeypatch.setattr(gv.fundamentals, "snapshot", lambda *a, **k: state["snap"])
    monkeypatch.setattr(gv.fundamentals, "save_snapshot", lambda *a, **k: {"status": "new"})
    monkeypatch.setattr(gv.fundamentals, "load_history", lambda *a, **k: None)
    monkeypatch.setattr(gv, "_price_series", price_series)
    monkeypatch.setattr(gv.reports, "find_report", lambda *a, **k: None)
    monkeypatch.setattr(gv.vd, "rf_live", lambda *a, **k: {"yield": 0.0628, "as_of": "2026-11-16",
                                                          "source": "BondSpot fixing", "url": "u", "detail": {}})
    monkeypatch.setattr(gv.vd, "damodaran_poland", lambda as_of, *a, **k: dict(vd.EMBEDDED_POLAND, age_days=315,
                                                                              stale=False))
    monkeypatch.setattr(gv.vd, "damodaran_ratings", lambda *a, **k: dict(vd.EMBEDDED_RATINGS))
    monkeypatch.setattr(gv.constituents, "sector_membership", lambda *a, **k: {})
    return state


# --------------------------------------------------------------- argparse (exit 2)

@pytest.mark.parametrize("argv", [
    [],
    ["KETY", "--wacc", "80"],
    ["KETY", "--terminal-growth", "abc"],
    ["KETY", "--lang", "de"],
    ["KETY", "--as-of", "2026-13-01"],
    ["KETY", "--as-of", "2999-01-01"],
    ["KETY", "--max-peers", "0"],
    ["KETY", "--isin", "XX123"],
    ["KETY", "--model", "ddm"],
])
def test_argparse_errors_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        gv.parse_args(argv)
    assert e.value.code == 2


# --------------------------------------------------------------- input errors (exit 1)

def test_terminal_growth_at_or_above_wacc_is_error(env, capsys):
    rc = gv.main(["TESTCO", "--no-files", "--wacc", "4", "--terminal-growth", "5"])
    assert rc == 1
    assert "terminal growth" in capsys.readouterr().err


def test_bank_g_above_coe_is_error(env, capsys):
    env["snap"] = BANK
    assert gv.main(["TESTBANK", "--no-files", "--wacc", "3", "--terminal-growth", "4"]) == 1
    assert "COE" in capsys.readouterr().err


def test_assumptions_json_validation(env, tmp_path, capsys):
    bad = tmp_path / "a.json"
    bad.write_text(json.dumps({"growth": 5}))
    assert gv.main(["TESTCO", "--no-files", "--assumptions", str(bad)]) == 1
    assert "unknown keys" in capsys.readouterr().err
    bad.write_text(json.dumps({"wacc": 90}))
    assert gv.main(["TESTCO", "--no-files", "--assumptions", str(bad)]) == 1
    bad.write_text("{not json")
    assert gv.main(["TESTCO", "--no-files", "--assumptions", str(bad)]) == 1
    bad.write_text(json.dumps({"scenarios": {"worst": {}}}))
    assert gv.main(["TESTCO", "--no-files", "--assumptions", str(bad)]) == 1


def test_unknown_ticker_exit_1(monkeypatch, capsys):
    def boom(*a, **k):
        raise gv.fundamentals.FundamentalsError("Unknown ticker 'NOPE'")
    monkeypatch.setattr(gv.fundamentals, "resolve_isin", boom)
    monkeypatch.setattr(gv.fundamentals, "ticker_for_company_name", lambda *a, **k: None)
    assert gv.main(["NOPE", "--no-files", "--offline"]) == 1
    assert "Unknown ticker" in capsys.readouterr().err


# --------------------------------------------------------------- full runs

def _run(env, argv, tmp_path):
    rc = gv.main(argv + ["--output-dir", str(tmp_path)])
    assert rc == 0
    js = list(tmp_path.glob("gpw_valuation_*.json"))
    md = list(tmp_path.glob("gpw_valuation_*.md"))
    assert len(js) == 1 and len(md) == 1
    return json.loads(js[0].read_text()), md[0].read_text()


def test_fcff_run_contract_and_numbers(env, tmp_path):
    r, md = _run(env, ["TESTCO"], tmp_path)
    for k in ("skill", "version", "lang", "as_of", "ticker", "implied_growth_pct", "wacc_pct", "fair_value_range",
              "sensitivity", "comps", "assumptions", "price", "warnings", "data_provenance", "range_position"):
        assert k in r
    assert r["skill"] == "gpw-valuation" and r["model"] == "fcff"
    assert set(r["fair_value_range"]) == {"low", "base", "high"}
    lo, base, hi = (r["fair_value_range"][k] for k in ("low", "base", "high"))
    assert lo <= base <= hi
    assert len(r["sensitivity"]) == 5 and all(len(row) == 5 for row in r["sensitivity"])
    # annualised: revenue 600m * 12/9
    assert r["fundamentals"]["annual_values"]["revenue"] == pytest.approx(8.0e8)
    # FCF0 = (CFO - capex) * 12/9 = (90 - 36) * 4/3 = 72m
    assert r["reverse_dcf"]["fcf0"] == pytest.approx(7.2e7)
    # the solved growth reproduces the market EV
    rd = r["reverse_dcf"]
    from valuation_engine import reverse_dcf_ev
    ev = reverse_dcf_ev(rd["fcf0"], rd["implied_growth_pct"] / 100, rd["wacc_pct"] / 100, rd["terminal_growth_pct"] / 100)
    assert ev == pytest.approx(rd["enterprise_value_market"], rel=1e-6)
    # WACC pieces: rf = 6.28 - 0.72; ERP 5.33
    dr = r["discount_rate"]
    assert dr["rf_pct"] == pytest.approx(5.56)
    assert dr["cost_of_equity_pct"] == pytest.approx(5.56 + dr["beta"] * 5.33)
    assert r["assumptions"]["terminal_growth"]["value"] == pytest.approx(0.025)
    assert r["assumptions"]["tax_rate"]["value"] == pytest.approx(0.19)
    assert all("source" in a for a in r["assumptions"].values())
    # markdown: answer first, sensitivity, sources, disclaimer
    assert md.index("## Odpowiedź") < md.index("## Stopa dyskontowa")
    assert "zakłada wzrost wolnych przepływów" in md
    assert "Wrażliwość" in md and "Źródła / Sources" in md
    assert md.rstrip().endswith("_")
    assert "kup" not in md.lower().replace("kupi", "")  # no buy wording


def test_user_overrides_are_used_and_labelled(env, tmp_path):
    a = tmp_path / "assume.json"
    a.write_text(json.dumps({"margin": 20, "net_debt": 100, "scenarios": {"bear": {"growth_pp": -5}}}))
    r, _ = _run(env, ["TESTCO", "--revenue-growth", "8", "--wacc", "9", "--terminal-growth", "2",
                      "--assumptions", str(a), "--lang", "en"], tmp_path)
    A = r["assumptions"]
    assert A["revenue_growth"]["value"] == pytest.approx(0.08) and "CLI" in A["revenue_growth"]["source"]
    assert A["margin"]["value"] == pytest.approx(0.20) and "JSON" in A["margin"]["source"]
    assert A["net_debt"]["value"] == pytest.approx(1.0e8)
    assert r["wacc_pct"] == pytest.approx(9.0)
    assert r["scenarios"]["bear"]["revenue_growth_pct"] == pytest.approx(3.0)
    # sensitivity centre row at WACC 9% and g=2% equals the base DCF value
    assert r["sensitivity_axes"]["row_values"][2] == pytest.approx(9.0)
    assert r["sensitivity"][2][2] == pytest.approx(r["dcf"]["value_per_share"])
    assert r["lang"] == "en"


def test_negative_fcf_reverse_not_solvable(env, tmp_path):
    snap = json.loads(json.dumps(INDUSTRIAL))
    snap["notoria"]["values"]["cfo"] = 1.0e7
    snap["notoria"]["values"]["capex"] = -2.0e8
    snap["notoria"]["values"]["operating_profit"] = 1.0e7
    snap["notoria"]["values"]["pretax_profit"] = 0.5e7
    env["snap"] = snap
    r, md = _run(env, ["TESTCO", "--no-comps"], tmp_path)
    # cash FCF negative -> normalised FCFF used; also negative here -> not solvable
    assert r["reverse_dcf"]["status"] == "not_solvable" and r["implied_growth_pct"] is None
    assert "nie da się policzyć" in md


def test_bank_run(env, tmp_path):
    env["snap"] = BANK
    env["price"] = 60.0
    r, md = _run(env, ["TESTBANK", "--beta", "1.0"], tmp_path)
    assert r["model"] == "bank"
    b = r["bank_model"]
    roe = 4.5e8 * 12 / 9 / 4.0e9          # 15%
    coe = 0.0556 + 1.0 * 0.0533
    assert b["roe_pct"] == pytest.approx(roe * 100)
    assert b["coe_pct"] == pytest.approx(coe * 100)
    bvps = 40.0
    assert b["value_per_share"] == pytest.approx((roe - 0.025) / (coe - 0.025) * bvps)
    rd = r["reverse_dcf"]
    assert rd["market_pbv"] == pytest.approx(1.5)
    assert rd["implied_roe_pct"] == pytest.approx((0.025 + 1.5 * (coe - 0.025)) * 100)
    assert r["wacc_pct"] == pytest.approx(coe * 100)
    assert r["sensitivity_axes"]["rows"] == "cost_of_equity_pct"
    assert r["ddm"]["applicable"] and r["ddm"]["dps0"] == pytest.approx(3.6)
    assert "C/WK" in md or "P/BV" in md


def test_forced_fcff_model_and_no_notoria(env, tmp_path, capsys):
    snap = json.loads(json.dumps(INDUSTRIAL))
    snap["notoria"] = None
    env["snap"] = snap
    assert gv.main(["TESTCO", "--no-files"]) == 1
    assert "Notoria" in capsys.readouterr().err


def test_comps_with_user_peers(env, tmp_path, monkeypatch):
    peers = {}
    for i, (pe, pbv) in enumerate([(10, 1.5), (12, 2.0), (14, 2.5), (16, 3.0), (18, 3.5)]):
        p = json.loads(json.dumps(INDUSTRIAL))
        p["ticker"], p["isin"] = "PEER{}".format(i), "PLPEER00000{}".format(i)
        p["indicators"].update(pe=pe, pbv=pbv)
        peers[p["ticker"]] = p

    def snapshot(ticker, *a, **k):
        return peers.get(ticker, INDUSTRIAL)

    monkeypatch.setattr(gv.fundamentals, "snapshot", snapshot)
    r, md = _run(env, ["TESTCO", "--peers", ",".join(peers)], tmp_path)
    c = r["comps"]
    assert not c["skipped"] and c["n_peers"] == 5
    assert c["stats"]["pe"]["median"] == pytest.approx(14.0)
    assert c["stats"]["pe"]["q1"] == pytest.approx(12.0) and c["stats"]["pe"]["q3"] == pytest.approx(16.0)
    # target earnings = market cap 1.0e9 / P/E 12.5 = 80m -> 14 x 80m / 10m shares = 112
    assert c["implied_per_share"]["pe"]["base"] == pytest.approx(112.0)
    assert not any("spółek porównawczych" in w for w in r["warnings"])


def test_few_peers_warning(env, tmp_path, monkeypatch):
    r, _ = _run(env, ["TESTCO", "--peers", "PEERX,PEERY"], tmp_path)
    assert any("Tylko 2 spółek" in w for w in r["warnings"])


def test_no_sector_index_skips_comps(env, tmp_path):
    r, _ = _run(env, ["TESTCO"], tmp_path)
    assert r["comps"]["skipped"] and r["comps"]["reason"] == "no_sector_index"
