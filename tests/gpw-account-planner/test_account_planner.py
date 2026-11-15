import json

import pytest

import account_planner_engine as ape
import gpw_account_planner as cli
from gpwlib import tax


def cfg(**kw):
    base = dict(monthly=1000.0, years=25, age=30, start_year=2027, ter_pct=0.0, inflation_pct=0.0)
    base.update(kw)
    return ape.default_config(**base)


# --- limits and rates -------------------------------------------------------

def test_known_limits_exact_and_projection():
    l27 = ape.limits_for_year(2027)
    assert l27["ike"] == 30_099.00 and l27["ikze"] == 12_039.60 and l27["source"] == "known"
    assert ape.limits_for_year(2027, self_employed=True)["ikze"] == 18_059.40
    l28 = ape.limits_for_year(2028, wage_growth_pct=5.0)
    assert l28["source"] == "projected"
    assert l28["ike"] == pytest.approx(30_099.00 * 1.05, abs=0.01)
    assert ape.limits_for_year(2030, wage_growth_pct=0.0)["ike"] == 30_099.00


def test_oki_rate_by_year():
    assert ape.oki_rate_for_year(2026, 0.85) is None
    assert ape.oki_rate_for_year(2027, 2.0) == 0.85          # fixed by art. 43
    assert ape.oki_rate_for_year(2028, 0.7) == 0.7           # assumption
    assert ape.oki_rate_for_year(2028, 0.7, nbp_rate_pct=5.75) == 1.09  # 19% x 5.75 = 1.0925 -> floor 1.09
    assert ape.oki_rate_for_year(2029, 0.7, nbp_rate_pct=0.25) == 0.1   # minimum


# --- regular / IKE -----------------------------------------------------------

def test_zero_return_no_costs_returns_contributions():
    c = cfg(years=2, oki_qualified_pct=100)  # qualified OKI under the exemption: no asset tax
    for order in ([], ["ike"], ["oki"]):
        r = ape.simulate(order, c, 0.0)
        assert r["net_value"] == pytest.approx(24_000.0, abs=0.01)
        assert r["taxes_total"] == 0


def test_regular_pays_belka_on_gain_only():
    c = cfg(years=5)
    r = ape.simulate([], c, 6.0)
    pot = r["pots"]["regular"]
    gain = pot["gross_value"] - pot["contributed"]
    assert pot["tax_exit"] == pytest.approx(0.19 * gain, abs=0.02)
    assert r["net_value"] == pytest.approx(pot["gross_value"] - pot["tax_exit"], abs=0.02)


def test_ike_before_60_taxed_like_regular_after_60_tax_free():
    young = cfg(age=30, years=25)
    assert ape.simulate(["ike"], young, 6.0)["net_value"] == ape.simulate([], young, 6.0)["net_value"]
    assert ape.simulate(["ike"], young, 6.0)["pots"]["ike"]["conditions"]["age_ok"] is False
    old = cfg(age=40, years=25)
    r = ape.simulate(["ike"], old, 6.0)
    assert r["pots"]["ike"]["tax_exit"] == 0 and r["pots"]["ike"]["conditions"]["met"]
    assert r["net_value"] > ape.simulate([], old, 6.0)["net_value"]


def test_ike_five_calendar_years_condition():
    r = ape.simulate(["ike"], cfg(age=62, years=3), 6.0)
    c = r["pots"]["ike"]["conditions"]
    assert c["age_ok"] and not c["years_ok"] and not c["met"]
    assert r["pots"]["ike"]["tax_exit"] > 0


def test_limit_overflow_goes_to_regular():
    r = ape.simulate(["ike"], cfg(monthly=5000, years=1), 0.0)
    assert r["pots"]["ike"]["contributed"] == pytest.approx(30_099.00, abs=0.05)
    assert r["pots"]["regular"]["contributed"] == pytest.approx(60_000 - 30_099.00, abs=0.05)
    assert r["first_month_split"] == {"ike": pytest.approx(2508.25, abs=0.01), "regular": pytest.approx(2491.75, abs=0.01)}


# --- IKZE --------------------------------------------------------------------

def test_ikze_refund_and_10pct_exit_after_65():
    c = cfg(age=40, years=25, pit="scale32", monthly=500)
    r = ape.simulate(["ikze"], c, 0.0)
    assert r["ikze_refunds_total"] == pytest.approx(0.32 * 500 * 12 * 25, abs=0.5)
    pot = r["pots"]["ikze"]
    assert pot["conditions"]["met"] and pot["tax_exit_kind"] == "ikze_10"
    assert pot["tax_exit"] == pytest.approx(0.10 * pot["gross_value"], abs=0.01)
    # last refund arrives after the horizon -> cash
    assert r["ikze_refunds_cash"] == pytest.approx(0.32 * 6000, abs=0.01)


def test_ikze_no_reinvest_counts_refund_as_cash():
    c = cfg(age=40, years=25, pit="scale12", reinvest_refund=False)
    r = ape.simulate(["ikze"], c, 5.0)
    assert "regular" not in r["pots"]
    assert r["ikze_refunds_cash"] == r["ikze_refunds_total"]


def test_ikze_no_pit_gives_no_refund():
    r = ape.simulate(["ikze"], cfg(age=40, years=25, pit="none"), 5.0)
    assert r["ikze_refunds_total"] == 0


def test_early_ikze_tax_piecewise_scale():
    assert ape.early_ikze_tax(100_000, "scale12") == pytest.approx(12_000)
    assert ape.early_ikze_tax(200_000, "scale12") == pytest.approx(120_000 * 0.12 + 80_000 * 0.32)
    assert ape.early_ikze_tax(200_000, "scale32") == pytest.approx(64_000)
    r = ape.simulate(["ikze"], cfg(age=30, years=10), 0.0)
    assert r["pots"]["ikze"]["tax_exit_kind"] == "ikze_scale"


# --- OKI ---------------------------------------------------------------------

def test_oki_tax_on_average_of_month_ends_global_etf():
    r = ape.simulate(["oki"], cfg(years=1, oki_qualified_pct=0), 0.0)
    avg = sum(1000 * m for m in range(1, 13)) / 12.0   # 6 500
    expected = tax.oki_tax(0.0, 0.0, avg, 0.85)["tax"]
    assert expected == pytest.approx(55.25)
    assert r["pots"]["oki"]["tax_oki_annual"] == pytest.approx(expected)
    assert r["net_value"] == pytest.approx(12_000 - expected, abs=0.01)


def test_oki_qualified_under_exemption_is_tax_free():
    r = ape.simulate(["oki"], cfg(years=3, oki_qualified_pct=100), 0.0)
    assert r["pots"]["oki"]["tax_oki_annual"] == 0


def test_oki_qualified_above_exemption_taxed_on_excess():
    r = ape.simulate(["oki"], cfg(monthly=20_000, years=1, oki_qualified_pct=100), 0.0)
    avg = sum(20_000 * m for m in range(1, 13)) / 12.0  # 130 000
    assert r["pots"]["oki"]["tax_oki_annual"] == pytest.approx((avg - 100_000) * 0.0085, abs=0.01)


def test_oki_not_available_in_2026():
    r = ape.simulate(["oki"], cfg(years=2, start_year=2026), 0.0)
    assert r["pots"]["oki"]["contributed"] == pytest.approx(12_000)
    assert r["pots"]["regular"]["contributed"] == pytest.approx(12_000)


# --- fees --------------------------------------------------------------------

def test_min_commission_per_order_and_ter():
    c = cfg(years=1, commission_pct=0.39, commission_min=5.0)
    r = ape.simulate([], c, 0.0)
    # 12 buys x 5 zł + one sale of ~11 940 zł at 0.39%
    sale = round((12_000 - 60) * 0.0039, 2)
    assert r["fees_total"] == pytest.approx(60 + sale, abs=0.02)
    r2 = ape.simulate([], cfg(years=1, ter_pct=1.0), 0.0)
    assert 0 < r2["fees_total"] < 12_000 * 0.01


# --- waterfall ---------------------------------------------------------------

def test_candidate_orders_count():
    orders = ape.candidate_orders()
    assert len(orders) == 16 and [] in orders and ["ikze", "ike", "oki"] in orders


def test_waterfall_scale32_puts_ikze_first():
    best = ape.best_order(cfg(monthly=2500, age=40, years=25, pit="scale32", ter_pct=0.5), 6.0)["best"]
    assert best["order"][:2] == ["ikze", "ike"]
    assert best["first_month_split"]["ikze"] == pytest.approx(12_039.60 / 12, abs=0.01)


def test_waterfall_beats_or_equals_every_standalone():
    c = cfg(monthly=3000, age=40, years=25, pit="scale12", ter_pct=0.5, oki_qualified_pct=50)
    best = ape.best_order(c, 6.0)["best"]
    for order in ([], ["ike"], ["ikze"], ["oki"]):
        assert best["net_value"] >= ape.simulate(order, c, 6.0)["net_value"] - 1e-6


def test_tie_prefers_ike_over_regular_when_age_not_met():
    best = ape.best_order(cfg(age=30, years=25, ter_pct=0.5), 6.0)["best"]
    assert best["order"] == ["ike"]


def test_plan_hold_to_65_and_warnings():
    p = ape.plan(cfg(age=30, years=25, ter_pct=0.5), [3, 6, 9])
    assert p["reference_scenario_pct"] == 6
    assert p["hold_to_65"]["hold_years"] == 10
    h = p["hold_to_65"]["standalone"]
    assert h["ike"]["pots"]["ike"]["conditions"]["met"]
    assert h["ike"]["net_value"] > h["regular"]["net_value"]
    assert {"ike_age", "ikze_age", "oki_global", "oki_rate_assumed"} <= set(p["warnings"])
    p2 = ape.plan(cfg(age=40, years=25), [6])
    assert p2["hold_to_65"] is None and "ike_age" not in p2["warnings"]


def test_real_value_deflated():
    r = ape.simulate([], ape.default_config(monthly=1000, years=10, age=40, ter_pct=0, inflation_pct=3.0), 0.0)
    assert r["net_value_real"] == pytest.approx(r["net_value"] / 1.03 ** 10, abs=0.05)


def test_config_validation():
    with pytest.raises(ValueError):
        ape.default_config(pit="flat")
    with pytest.raises(ValueError):
        ape.default_config(monthly=0)
    with pytest.raises(ValueError):
        ape.default_config(oki_qualified_pct=150)
    with pytest.raises(ValueError):
        ape.simulate(["xyz"], cfg(), 5.0)


# --- CLI ---------------------------------------------------------------------

def test_cli_writes_json_and_markdown(tmp_path, capsys):
    rc = cli.main(["--monthly", "1000", "--years", "25", "--age", "30", "--pit", "scale12", "--output-dir", str(tmp_path)])
    assert rc == 0
    js = list(tmp_path.glob("gpw_account_plan_*.json"))
    md = list(tmp_path.glob("gpw_account_plan_*.md"))
    assert len(js) == 1 and len(md) == 1
    data = json.loads(js[0].read_text(encoding="utf-8"))
    assert data["skill"] == "gpw-account-planner" and data["version"] == "1.0.0"
    assert data["data_provenance"]["market_data"] is False and data["data_provenance"]["legal_sources"]
    text = md[0].read_text(encoding="utf-8")
    assert text.index("Odpowiedź w skrócie") < text.index("Porównanie kont")
    assert "Słowniczek" in text and "Materiał edukacyjny" in text


def test_cli_english_yearly(capsys):
    assert cli.main(["--yearly", "12000", "--years", "20", "--age", "45", "--lang", "en", "--no-files"]) == 0
    out = capsys.readouterr().out
    assert "Short answer" in out and "Educational material" in out


@pytest.mark.parametrize("argv", [
    ["--years", "10", "--age", "30"],                                   # no amount
    ["--monthly", "100", "--yearly", "1200", "--years", "10", "--age", "30"],
    ["--monthly", "-1", "--years", "10", "--age", "30"],
    ["--monthly", "100", "--years", "0", "--age", "30"],
    ["--monthly", "100", "--years", "10", "--age", "30", "--pit", "flat"],
    ["--monthly", "100", "--years", "10", "--age", "30", "--returns", "a,b"],
    ["--monthly", "100", "--years", "10", "--age", "30", "--oki-qualified-pct", "120"],
])
def test_cli_bad_arguments_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv + ["--no-files"])
    assert e.value.code == 2
