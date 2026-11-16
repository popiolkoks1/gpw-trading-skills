"""Build a synthetic offline GPW cache (archive sessions + GPW Benchmark constituents).

Used by the gpw-portfolio-check and gpw-pre-trade-gate CLI tests so the CLIs
run end-to-end with --offline --cache-dir and no network.
"""

import datetime as dt
import json
import random
from pathlib import Path

from gpwlib import plcal
from gpwlib.constituents import SECTOR_INDICES, SIZE_INDICES

END = dt.date(2026, 11, 13)


def session_dates(n, end=END):
    out = []
    for d in plcal.previous_probable_sessions(end, n * 2 + 30):
        out.append(d)
        if len(out) == n:
            break
    return sorted(out)


def path_for(rets, start=100.0):
    closes = [start]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    return closes


def gen_returns(n, seed, vol=0.012, drift=0.0004):
    rnd = random.Random(seed)
    return [rnd.gauss(drift, vol) for _ in range(n)]


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_cache(root, instruments, kind="shares", n=300, end=END, turnover_k=None, start_offset=None):
    """instruments: {name: closes list (len n)}. turnover_k: {name: value}. start_offset: {name: first index}."""
    root = Path(root)
    dates = session_dates(n, end)
    turnover_k = turnover_k or {}
    start_offset = start_offset or {}
    for i, d in enumerate(dates):
        rows = []
        for name, closes in instruments.items():
            if i < start_offset.get(name, 0):
                continue
            c = round(closes[i], 4)
            prev = closes[i - 1] if i > 0 and i > start_offset.get(name, 0) else c
            chg = round((c / round(prev, 4) - 1) * 100, 2) if i > 0 else 0.0
            rows.append({"name": name, "currency": "PLN" if kind != "indices" else "", "open": c, "high": c * 1.005,
                         "low": c * 0.995, "close": c, "change_pct": chg, "turnover_k": turnover_k.get(name, 5000.0)})
        _write(root / "archive" / kind / "{}.json".format(d.isoformat()),
               {"schema": 1, "date": d.isoformat(), "type": kind, "session": True, "rows": rows,
                "source_url": "https://www.gpw.pl/archiwum-notowan?fetch=0&type=10&instrument=&date=x",
                "retrieved_at": "2026-11-14T10:00:00+00:00"})
    return dates


def build_constituents(root, sectors=None, sizes=None):
    """sectors: {'WIG-BANKI': [names]}, sizes: {'WIG20': [names]}. Every index gets a file (maybe empty)."""
    root = Path(root)
    sectors, sizes = sectors or {}, sizes or {}
    for key, (isin, *_r) in SECTOR_INDICES.items():
        _write(root / "constituents" / "{}.json".format(isin),
               {"isin": isin, "members": [{"name": m, "isin": "PL0000000000", "weight_pct": 1.0} for m in sectors.get(key, [])],
                "retrieved_on": "2026-11-14", "source_url": "https://gpwbenchmark.pl"})
    for key, isin in SIZE_INDICES.items():
        _write(root / "constituents" / "{}.json".format(isin),
               {"isin": isin, "members": [{"name": m, "isin": "PL0000000000", "weight_pct": 1.0} for m in sizes.get(key, [])],
                "retrieved_on": "2026-11-14", "source_url": "https://gpwbenchmark.pl"})


def standard_cache(root, n=300):
    """Five stocks + WIG + one ETF. BANKA/BANKB nearly identical (correlated), ILLIQ thin."""
    base = gen_returns(n - 1, 1)
    noise = gen_returns(n - 1, 2, vol=0.002, drift=0.0)
    shares = {
        "BANKA": path_for(base, 50.0),
        "BANKB": path_for([b + e for b, e in zip(base, noise)], 80.0),
        "GAMES": path_for(gen_returns(n - 1, 3, vol=0.02), 200.0),
        "ILLIQ": path_for(gen_returns(n - 1, 4), 10.0),
        "NEWCO": path_for(gen_returns(n - 1, 5), 30.0),
    }
    wig = path_for([0.6 * b + 0.4 * g for b, g in zip(base, gen_returns(n - 1, 6, vol=0.008))], 80000.0)
    build_cache(root, shares, "shares", n, turnover_k={"ILLIQ": 2.0, "BANKA": 50000.0, "BANKB": 30000.0},
                start_offset={"NEWCO": n - 30})
    build_cache(root, {"WIG": wig, "WIG20": wig}, "indices", n)
    build_cache(root, {"ETFW20": path_for(base, 90.0)}, "etf", n)
    build_constituents(root, sectors={"WIG-BANKI": ["BANKA", "BANKB"], "WIG-GRY": ["GAMES"]},
                       sizes={"WIG20": ["BANKA", "BANKB"], "MWIG40": ["GAMES"], "SWIG80": ["ILLIQ"]})
    return {"shares": shares, "wig": wig}
