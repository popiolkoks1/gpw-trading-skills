import json

import pytest
from conftest import FIXTURES

import gpw_ownership as cli
from gpwlib import http
from test_ownership_funds import NN_LINES, make_pdf


def _common(tmp_path):
    return ["--cache-dir", str(tmp_path / "cache"), "--state-dir", str(tmp_path / "state"), "--output-dir", str(tmp_path / "out")]


@pytest.mark.parametrize("argv", [
    [], ["unknown"], ["insiders", "--days", "0"], ["funds"], ["shorts", "--as-of", "2026-13-01"],
    ["shorts", "--sessions", "3"], ["shorts", "--lang", "de"], ["insiders", "--max-details", "-1"]])
def test_argparse_errors_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2


def test_shorts_offline_without_cache_exits_1(tmp_path, capsys):
    assert cli.main(["shorts", "--offline"] + _common(tmp_path)) == 1
    assert "KNF register not in cache" in capsys.readouterr().err


def test_parse_missing_file_exits_1(tmp_path, capsys):
    assert cli.main(["parse", "--file", str(tmp_path / "nope.txt"), "--offline"] + _common(tmp_path)) == 1
    assert "file not found" in capsys.readouterr().err


def test_funds_missing_file_exits_1(tmp_path):
    assert cli.main(["funds", "--file", str(tmp_path / "x.pdf"), "--offline"] + _common(tmp_path)) == 1


def test_funds_bad_fetch_code_exits_1(tmp_path):
    assert cli.main(["funds", "--fetch-code", "bad code!", "--offline"] + _common(tmp_path)) == 1


def test_parse_stores_and_dedupes(tmp_path, capsys):
    argv = ["parse", "--file", str(FIXTURES / "notif_pl_buy.txt"), "--ticker", "PRZYK", "--offline", "--json"] + _common(tmp_path)
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    rep = json.loads(out[:out.rindex("}") + 1])
    assert rep["skill"] == "gpw-ownership" and rep["mode"] == "parse" and rep["as_of"] == "2026-11-05"
    assert rep["parse"]["store"]["new"] == 1
    assert cli.main(argv) == 0
    store = json.loads((tmp_path / "state" / "insiders" / "transactions.json").read_text(encoding="utf-8"))
    assert len(store["transactions"]) == 1 and store["transactions"][0]["ticker"] == "PRZYK"
    assert list((tmp_path / "out").glob("gpw_ownership_parse_*.json"))


def test_funds_pdf_end_to_end_with_store(tmp_path, capsys):
    f25 = tmp_path / "nn25.pdf"
    f25.write_bytes(make_pdf(NN_LINES))
    prev = [ln.replace("31/12/2025", "31/12/2024").replace("7 824 616 433,13", "5 895 908 731,12") for ln in NN_LINES]
    f24 = tmp_path / "nn24.pdf"
    f24.write_bytes(make_pdf(prev))
    assert cli.main(["funds", "--file", str(f25), "--file", str(f24), "--offline", "--no-files", "--json", "--lang", "en"]
                    + _common(tmp_path)) == 0
    rep = json.loads(capsys.readouterr().out)
    fund = rep["funds"]["funds"][0]
    assert fund["valuation_date"] == "2025-12-31" and fund["previous"] == "2024-12-31" and fund["holdings"] == 3
    pko = next(r for r in rep["funds"]["by_ticker"] if r["ticker"] == "PKOBP")
    assert pko["value_pln"] == pytest.approx(7824616433.13) and pko["approximate"] is True
    assert pko["approx_shares"] is None  # no quotes cached -> no share estimate, never invented
    assert rep["data_provenance"]["freshness"]["is_stale"] is False
    stored = list((tmp_path / "state" / "ownership" / "funds").glob("*.json"))
    assert len(stored) == 2


class _Resp:
    def __init__(self, payload):
        self.text = json.dumps(payload)
        self.retrieved_at = "2026-11-16T12:00:00+00:00"
        self.from_cache = True

    def json(self):
        return json.loads(self.text)


def test_shorts_with_fixture_register(tmp_path, monkeypatch, capsys):
    cur = json.loads((FIXTURES / "knf_current.json").read_text(encoding="utf-8"))
    hist = json.loads((FIXTURES / "knf_history.json").read_text(encoding="utf-8"))

    def fake_post(url, form, **kw):
        return _Resp(cur if json.loads(form["request"])["method"] == "Default" else hist)
    monkeypatch.setattr(http, "post", fake_post)
    assert cli.main(["shorts", "--offline", "--no-files", "--json", "--no-shares"] + _common(tmp_path)) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["mode"] == "shorts" and "data_provenance" in rep
    by = rep["shorts"]["by_issuer"]
    assert {"ticker", "isin", "total_pct", "holders", "change_20s_pp", "crowded"} <= set(by[0])
    modivo = next(r for r in by if r["isin"] == "PLCCC0000016")
    assert modivo["crowded"] and modivo["holders"] == 3
    # empty cache -> no ISIN->ticker map -> no ticker, so no signal is emitted for it
    assert modivo["ticker"] is None and not rep["signals"]
    md = cli.render_markdown(rep, "pl")
    assert "DOLNA granica" in md and "Źródła" in md and md.rstrip().endswith("_")


def test_build_signals_contract():
    res = {"as_of": "2026-11-13",
           "shorts": {"by_issuer": [{"ticker": "AAA", "crowded": True, "total_pct": 2.4}],
                      "events": [{"type": "new_position", "ticker": "BBB", "date": "2026-11-10", "pct": 0.6}]},
           "insiders": {"by_ticker": [{"ticker": "CCC", "cluster_buy": True, "cluster_sell": False, "last_date": "2026-11-09",
                                       "max_distinct_buyers_30d": 3, "max_distinct_sellers_30d": 0}]},
           "funds": {"funds": [{"valuation_date": "2025-12-31"}],
                     "by_ticker": [{"ticker": "DDD", "fund_accumulation": True, "implied_flow_rel": 0.3}]}}
    sig = cli.build_signals(res)
    kinds = {(s["ticker"], s["kind"], s["direction"]) for s in sig}
    assert kinds == {("AAA", "short_crowded", "avoid"), ("BBB", "short_new", "avoid"),
                     ("CCC", "insider_cluster_buy", "long"), ("DDD", "fund_accumulation", "long")}
    assert all({"ticker", "date", "kind", "direction"} <= set(s) for s in sig)
    assert next(s for s in sig if s["kind"] == "short_crowded")["score"] == 60.0


# --- integration fix 1.2: every emitted ticker is the current listing ------------------------

def test_shorts_emit_current_ticker_not_pre_rename_alias(tmp_path, monkeypatch, capsys):
    from gpwlib import fundamentals
    cur = json.loads((FIXTURES / "knf_current.json").read_text(encoding="utf-8"))
    hist = json.loads((FIXTURES / "knf_history.json").read_text(encoding="utf-8"))

    def fake_post(url, form, **kw):
        return _Resp(cur if json.loads(form["request"])["method"] == "Default" else hist)
    monkeypatch.setattr(http, "post", fake_post)
    # the alias CCC comes last: a naive {isin: ticker} inversion picks it
    monkeypatch.setattr(fundamentals, "isin_map", lambda *a, **k: {"MODIVO": "PLCCC0000016", "CCC": "PLCCC0000016"})
    monkeypatch.setattr(fundamentals, "isin_to_ticker_map", lambda *a, **k: {"PLCCC0000016": "MODIVO"})
    assert cli.main(["shorts", "--offline", "--no-files", "--json", "--no-shares", "--ticker", "CCC"]
                    + _common(tmp_path)) == 0
    rep = json.loads(capsys.readouterr().out)
    modivo = next(r for r in rep["shorts"]["by_issuer"] if r["isin"] == "PLCCC0000016")
    assert modivo["ticker"] == "MODIVO" and modivo["register_name"] == modivo["issuer"]
    assert all(s["ticker"] != "CCC" for s in rep["signals"])
    assert any(s["ticker"] == "MODIVO" and s["kind"] == "short_crowded" for s in rep["signals"])
    assert all(e.get("ticker") in (None, "MODIVO") or e["isin"] != "PLCCC0000016" for e in rep["shorts"]["events"])


def test_canon_prefers_isin_then_renames_same_company_only():
    m = {"CCC": "PLCCC0000016", "MODIVO": "PLCCC0000016", "LESS": "PLINTGR00013", "LUNAGLBAL": "PLINTGR00013",
         "PKOBP": "PLPKO0000016"}
    rev = {"PLCCC0000016": "CCC", "PLINTGR00013": "LESS", "PLPKO0000016": "PKOBP"}  # aliases won (old bug)
    rev = {i: cli._newest_alias(t, i, m) for i, t in rev.items()}
    canon = cli.make_canon(m, rev, "2026-11-13")
    assert canon(isin="PLCCC0000016") == "MODIVO"
    assert canon("ccc") == "MODIVO" and canon("CCC", date="2025-06-30") == "MODIVO"
    assert canon("LESS") == "LUNAGLBAL" and canon("PKOBP") == "PKOBP"
    assert canon("UNKNOWN1") == "UNKNOWN1" and canon() is None
    # an old name with no ISIN evidence follows panel.RENAMES only up to the record date
    canon2 = cli.make_canon({}, {}, "2026-11-13")
    assert canon2("CCC", date="2025-01-02") == "MODIVO" and canon2("CCC", date="2026-11-13") == "CCC"
