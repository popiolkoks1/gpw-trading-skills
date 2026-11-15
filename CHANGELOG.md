# Changelog

## 1.1.0 — 2026-11-15

Eight new skills (13 in total), for beginners and advanced users.

- **Beginners:** `gpw-start` (readiness check, equity range, plan, glossary,
  learning path), `gpw-account-planner` (IKE / IKZE / OKI / regular account over
  many years), `gpw-etf-planner` (GPW ETF catalog with archive stats, whole-unit
  monthly buy plan with commissions, DCA test).
- **Everyone:** `gpw-portfolio-check` (concentration, correlation, VaR/ES,
  drawdown, liquidity, vs WIG).
- **Advanced:** `gpw-trend-screener` (Minervini Trend Template, IBD-style RS,
  VCP, execution states, history mode), `gpw-market-regime` (distribution days,
  follow-through day, top-risk score), `gpw-exposure-coach` (posture, exposure
  ceiling, risk per trade, circuit breaker), `gpw-pre-trade-gate` (fail-closed
  GO / NO-GO checklist).
- `gpw-trade-journal`: paper mode (`--paper`); `gpw-trade-review` reviews paper
  trades separately and never puts them into PIT-38.
- `gpw-position-sizer`: uses the shared commission table, warns when it is older
  than 180 days; OKI note corrected to the act (exemption covers qualified assets
  only).
- `shared/gpwlib` 1.1.0: `brokers` (dated commission presets), `tax` (Belka,
  IKE/IKZE limits 2026–2027, OKI rules verified against Dz.U. 2026 poz. 1098),
  `stats` (returns, volatility, drawdown, correlation, beta, VaR/ES, HHI),
  `journal_io` (read-only journal access), ETF archive type 241.
- Parser test on a trimmed real GPW archive page (detects layout changes).

Known issue: `gpw-market-breadth` and `gpw-sector-trend` mark deliberate
`--as-of` historical runs as stale.

## 1.0.0 — 2026-11-15

First public release.

- `gpw-market-breadth`, `gpw-sector-trend`, `gpw-position-sizer`,
  `gpw-trade-journal`, `gpw-trade-review` (PL/EN).
- `shared/gpwlib`: GPW archive client with cache, Polish exchange calendar,
  dividend/split adjustment from GPW reference prices, GPW Benchmark
  constituents, provenance block, PL/EN formatting.
- Packaging script for claude.ai `.skill` uploads, CI (Linux/macOS/Windows,
  Python 3.9 and 3.12), trigger/output evals per skill.
