"""CLI of gpw-pair-trading on a synthetic offline cache (no network)."""
import datetime as dt
import json
import math

import pytest

import gpw_pair_trading as cli
from pair_fixtures import cointegrated_prices, random_walk, session_days, write_cache

AS_OF = dt.date(2026, 11, 13)


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    root = tmp_path_factory.mktemp("gpwcache")
    n = 420
    days = session_days(n, AS_OF)
    pa, pb = cointegrated_prices(n, beta=1.1, phi=0.85, seed=21)
    pc = [math.exp(math.log(30) + v) for v in random_walk(n, 99, 0.02)]
    # note: "CCC" would be merged into MODIVO by gpwlib.panel.RENAMES
    write_cache(root, days, {"AAA": pa, "BBB": pb, "DDD": pc})
    return root


def _run(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr()


def _common(cache, out):
    return ["--offline", "--cache-dir", str(cache), "--as-of", AS_OF.isoformat(), "--sessions", "400",
            "--lookback", "380", "--output-dir", str(out)]


# ---------------------------------------------------------------- argument errors (exit 2)

@pytest.mark.parametrize("argv", [
    [],
    ["pair", "PKOBP"],
    ["pair", "PKOBP", "pkobp"],
    ["pair", "A", "B", "--lang", "de"],
    ["pair", "A", "B", "--entry-z", "4", "--stop-z", "3.5"],
    ["pair", "A", "B", "--exit-z", "2.5"],
    ["pair", "A", "B", "--sessions", "100"],
    ["pair", "A", "B", "--lookback", "100"],
    ["pair", "A", "B", "--z-lookback", "5"],
    ["pair", "A", "B", "--alpha", "0.9"],
    ["pair", "A", "B", "--position-value", "0"],
    ["pair", "A", "B", "--as-of", "13-11-2026"],
    ["pair", "A", "B", "--broker", "nobroker"],
    ["scan", "--sector", "spaceships"],
    ["scan", "--pair", "PKOBP"],
    ["scan", "--pair", "PKOBP,PKOBP"],
    ["scan", "--top", "0"],
    ["scan", "--min-corr", "1.5"],
])
def test_bad_arguments_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(argv)
    assert e.value.code == 2


def test_sector_aliases():
    assert cli.resolve_sector("banks") == "WIG-BANKI"
    assert cli.resolve_sector("Banki") == "WIG-BANKI"
    assert cli.resolve_sector("paliwa") == "WIG-PALIWA"
    assert cli.resolve_sector("Energetyka") == "WIG-ENERG"
    assert cli.resolve_sector("górnictwo") == "WIG-GORNIC"
    assert cli.resolve_sector("WIG-GRY") == "WIG-GRY"
    assert cli.resolve_sector("nope") is None
    a = cli.parse_args(["scan", "--sector", "banks,banki,fuels"])
    assert a.sectors == ["WIG-BANKI", "WIG-PALIWA"]


# ---------------------------------------------------------------- data errors (exit 1)

def test_unknown_ticker_exit_1(cache, tmp_path, capsys):
    code, cap = _run(capsys, "pair", "AAA", "ZZZ", *_common(cache, tmp_path))
    assert code == 1 and "ZZZ" in cap.err


def test_empty_cache_exit_1(tmp_path, capsys):
    code, cap = _run(capsys, "pair", "AAA", "BBB", "--offline", "--cache-dir", str(tmp_path),
                     "--as-of", AS_OF.isoformat())
    assert code == 1 and "ERROR" in cap.err


# ---------------------------------------------------------------- happy paths

def test_pair_mode_writes_contract_json(cache, tmp_path, capsys):
    code, cap = _run(capsys, "pair", "AAA", "BBB", "--lang", "en", *_common(cache, tmp_path))
    assert code == 0, cap.err
    files = sorted(tmp_path.glob("gpw_pair_trading_*.json"))
    assert len(files) == 1 and len(list(tmp_path.glob("gpw_pair_trading_*.md"))) == 1
    r = json.loads(files[0].read_text(encoding="utf-8"))
    assert r["skill"] == "gpw-pair-trading" and r["version"] == "1.0.0" and r["lang"] == "en"
    assert r["as_of"] == AS_OF.isoformat() == r["data_provenance"]["as_of"]
    assert r["data_provenance"]["freshness"]["is_stale"] is False  # --as-of is the reference date
    p = r["pairs"][0]
    for k in ("a", "b", "hedge_ratio", "adf_stat", "p_value", "coint_pass", "half_life_days", "z", "corr",
              "stability", "liquidity_ok"):
        assert k in p
    assert p["coint_pass"] is True
    assert isinstance(r["signals"], list)
    assert p["hedge"]["futures"]["AAA"]["available"] is None  # no type-35 cache offline
    md = cap.out
    assert "Educational material" in md and "Sources" in md
    assert "buy " not in md.lower().replace("buying", "")


def test_switch_mode_verdict(cache, capsys):
    code, cap = _run(capsys, "switch", "AAA", "BBB", "--no-files", *_common(cache, "."))
    assert code == 0, cap.err
    assert "Zamiana AAA → BBB" in cap.out
    assert "Belki" in cap.out  # tax note on switching in a regular account


def test_scan_with_explicit_pairs(cache, tmp_path, capsys):
    code, cap = _run(capsys, "scan", "--pair", "AAA,BBB", "--pair", "AAA,DDD", "--pair", "BBB,DDD",
                     *_common(cache, tmp_path))
    assert code == 0, cap.err
    r = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text(encoding="utf-8"))
    assert r["mode"] == "scan" and r["tests"] == 3
    by = {(p["a"], p["b"]): p for p in r["pairs"]}
    assert by[("AAA", "BBB")]["coint_pass"] is True
    assert by[("AAA", "DDD")]["coint_pass"] is False
    assert all(p["tests"] == 3 for p in r["pairs"])
    assert r["pairs"][0]["a"] == "AAA" and r["pairs"][0]["b"] == "BBB"  # passing pairs ranked first
    assert by[("AAA", "BBB")]["p_adj"] >= by[("AAA", "BBB")]["p_value"]


def test_scan_unknown_user_pair_exit_1(cache, tmp_path, capsys):
    code, cap = _run(capsys, "scan", "--pair", "AAA,QQQ", *_common(cache, tmp_path))
    assert code == 1 and "QQQ" in cap.err
