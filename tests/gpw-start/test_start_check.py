import json

import pytest

import start_check as sc


def run(**kw):
    base = dict(income=6500, expenses=4800, savings=9000, debts=[], horizon_years=25, age=29,
                tolerance_pct=30, unstable_income=False, experience="none", year=2027)
    base.update(kw)
    return sc.assess(**base)


def test_stage0_negative_budget():
    r = run(income=4000, expenses=4500)
    assert r["stage"] == 0
    assert r["numbers"]["investable_monthly"] == 0
    assert any(w["key"] == "w_negative" for w in r["warnings"])
    assert not any(s["key"] == "s_ef" for s in r["steps"])


def test_stage1_emergency_fund_gap_and_months():
    r = run()
    n = r["numbers"]
    assert r["stage"] == 1
    assert n["emergency_target"] == 14400 and n["emergency_gap"] == 5400
    assert n["emergency_months_to_fill"] == 4  # 5400 / 1700 -> 3.2 -> 4
    assert n["investable_monthly"] == 0


def test_unstable_income_doubles_reserve():
    r = run(unstable_income=True, savings=20000)
    assert r["numbers"]["emergency_months"] == 6
    assert r["numbers"]["emergency_target"] == 28800


def test_stage2_expensive_debt_sorted_highest_first():
    debts = sc.parse_debts(["auto:20000:7.5", "karta:4000:19,9", "raty:3000:12"])
    assert [d["name"] for d in debts] == ["karta", "raty", "auto"]
    r = run(savings=50000, debts=debts)
    assert r["stage"] == 2
    assert r["numbers"]["expensive_debt_total"] == 7000  # 7.5% loan is not "expensive"
    keys = [s["key"] for s in r["steps"]]
    assert keys.index("s_debt") < keys.index("s_etf")


def test_stage3_investable_is_surplus():
    r = run(savings=50000)
    assert r["stage"] == 3 and r["numbers"]["investable_monthly"] == 1700


@pytest.mark.parametrize("tol,horizon,lo,hi", [
    (30, 25, 25, 50),    # 30/0.55 = 54.5 -> 50
    (50, 25, 45, 90),    # 90.9 -> 90
    (10, 25, 5, 15),     # 18.2 -> 15
    (60, 25, 50, 100),   # capped at 100
    (50, 5, 25, 50),     # medium horizon caps at 50
    (50, 2, 0, 0),       # short horizon: no equities
])
def test_equity_range(tol, horizon, lo, hi):
    eq = sc.equity_range(tol, horizon)
    assert (eq["low"], eq["high"]) == (lo, hi)


def test_short_horizon_safe_step_and_stage_key():
    r = run(savings=50000, horizon_years=2)
    keys = [s["key"] for s in r["steps"]]
    assert "s_safe" in keys and "s_etf" not in keys
    assert r["stage_key"] == "stage_3_short"


def test_age_warning_ike():
    assert any(w["key"] == "w_age_ike" for w in run(age=29, horizon_years=10)["warnings"])
    assert not any(w["key"] == "w_age_ike" for w in run(age=55, horizon_years=10)["warnings"])


def test_limits_from_tax_module():
    r = run(year=2026)
    assert r["limits"]["ike"] == 28260 and r["limits"]["ikze"] == 11304
    r = run(year=2031)  # unknown year falls back to the latest known
    assert r["limits"]["year"] == 2027


@pytest.mark.parametrize("bad", ["karta", "karta:abc:10", "karta:-1:10"])
def test_bad_debt_rejected(bad):
    with pytest.raises(sc.StartError):
        sc.parse_debts([bad])


def test_invalid_inputs():
    with pytest.raises(sc.StartError):
        run(horizon_years=0)
    with pytest.raises(sc.StartError):
        run(tolerance_pct=0)


def test_markdown_bilingual_and_disclaimer():
    r = run(savings=50000)
    pl = sc.render_markdown(r, "pl")
    en = sc.render_markdown(r, "en")
    assert "Etap 3" in pl and "Stage 3" in en
    assert "Materiał edukacyjny" in pl and "Educational material" in en
    assert sc.fmt.pln(1700, 0, "pl") in pl


def test_cli_writes_files(tmp_path, capsys):
    rc = sc.main(["--income", "6500", "--expenses", "4800", "--horizon", "20", "--tolerance", "30",
                  "--output-dir", str(tmp_path), "--lang", "en"])
    assert rc == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert len(files) == 2 and files[0].startswith("gpw_start_")
    data = json.loads((tmp_path / files[0]).read_text(encoding="utf-8"))
    assert data["skill"] == "gpw-start" and data["lang"] == "en"


def test_cli_error_exit_code(capsys):
    assert sc.main(["--income", "1", "--expenses", "1", "--horizon", "5", "--tolerance", "20",
                    "--debt", "zle"]) == 1
    with pytest.raises(SystemExit) as e:
        sc.main(["--income", "1"])
    assert e.value.code == 2
