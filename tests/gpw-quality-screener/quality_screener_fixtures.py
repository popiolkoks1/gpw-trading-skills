"""Synthetic statements and fixture loaders for gpw-quality-screener tests."""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def stmt(**kw):
    """A 9-month industrial statement (PLN) with sensible defaults."""
    base = {
        "source": "notoria", "label": "I-III kw. 2026", "months": 9, "year": 2026, "first_quarter": 1,
        "last_quarter": 3, "period_end": "2026-09-30", "consolidated": True,
        "revenue": 900.0, "gross_profit": None, "profit_on_sales": 135.0, "operating_profit": 120.0,
        "pretax_profit": 110.0, "net_income": 90.0, "net_income_basis": "parent",
        "depreciation_amortization": 30.0, "ebitda": 150.0, "cfo": 120.0, "capex": -45.0,
        "total_assets": 1000.0, "current_assets": 400.0, "current_liabilities": 250.0,
        "noncurrent_liabilities": 200.0, "total_liabilities": 450.0, "total_liabilities_basis": "x",
        "equity": 550.0, "equity_basis": "parent", "share_capital": 50_000_000.0, "shares": None,
        "cash": None, "borrowings": None, "lease_liabilities": None, "finance_costs": None,
        "rounding_pln": 1_000_000.0,
    }
    base.update(kw)
    return base


def prior_stmt(**kw):
    """Same 9-month window one year earlier, slightly weaker on every signal."""
    base = stmt(label="I-III kw. 2025", year=2025, period_end="2025-09-30", revenue=800.0, profit_on_sales=110.0,
                operating_profit=100.0, pretax_profit=90.0, net_income=70.0, cfo=90.0, total_assets=1000.0,
                current_assets=350.0, current_liabilities=250.0, noncurrent_liabilities=250.0,
                equity=500.0, share_capital=50_000_000.0)
    base.update(kw)
    return base


def snapshot(ticker, notoria_html=None, indicators=None, retrieved="2026-11-16T15:00:00+00:00"):
    import gpwlib.fundamentals as f
    noto = f.parse_notoria_html(notoria_html) if notoria_html else None
    return {"ticker": ticker, "isin": "PLTEST000000", "isin_source": "test", "retrieved_at": retrieved,
            "indicators_retrieved_at": retrieved, "notoria_retrieved_at": retrieved,
            "indicators": indicators or {}, "notoria": noto, "sources": ["fixture"], "warnings": [],
            "from_cache": True}


def notoria_from_values(label, values, consolidated=True):
    import gpwlib.fundamentals as f
    return {"period_label": label, "period": f.parse_period_label(label), "consolidated": consolidated,
            "unit_source": "thousand PLN", "multiplier": 1e3, "values": values, "raw": {}, "unmapped": [],
            "source_note": "synthetic"}
