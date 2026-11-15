# Skill conventions (for contributors)

These rules keep every skill in the set consistent, testable and safe.

## Layout

```
skills/<skill-name>/
  SKILL.md                 # < 500 lines, English body, PL+EN triggers in description
  references/*.md          # methodology, loaded on demand (one level deep)
  scripts/
    .vendor                # lists "gpwlib" if the skill uses shared code
    gpwlib/                # vendored copy - never edit here, edit shared/gpwlib
    <main>.py              # CLI entry point, argparse, --lang pl|en, --output-dir
  evals/evals.json         # trigger + output evals (should / should-not)
tests/<skill-name>/test_*.py
```

## Rules

1. **Scripts compute, Claude interprets.** Every number, score and threshold
   comes from a deterministic script. SKILL.md tells Claude to run it and how to
   explain the output; Claude never invents prices, dates or tax figures.
2. **Freshness is a gate.** JSON output carries a `data_provenance` block
   (`gpwlib.provenance`). If `freshness.is_stale` is true the report says so at
   the top and Claude must mention it first.
3. **Source and date on every figure.** Reports end with a *Źródła / Sources*
   section listing URLs and the business date.
4. **Bilingual.** `--lang pl` (default) or `--lang en`. Claude passes the
   language the user writes in. Strings live in a `STRINGS` dict per script
   using `gpwlib.fmt.T`. Numbers use `gpwlib.fmt` (Polish: `1 234,56 zł`).
5. **Stdlib only** unless unavoidable; Python 3.9+.
6. **No orders, no advice.** Skills never place orders or say "buy X". They
   produce context, risk numbers and checklists for a human decision. Every
   report ends with `gpwlib.fmt.DISCLAIMER[lang]`.
7. **Explain thresholds.** No unexplained constants; each one is justified in
   `references/` or a code comment.
8. **Polish realities first**: PLN, whole shares (lot = 1), broker minimum
   fees, 19% capital-gains tax (PIT-38, FIFO), IKE/IKZE/OKI, GPW session hours
   and price thresholds, thin liquidity outside WIG20/mWIG40.
9. **Exit codes:** 0 ok, 1 runtime/data error (message on stderr), 2 bad
   arguments (argparse).
10. **Outputs:** `<prefix>_YYYY-MM-DD_HHMMSS.{json,md}` in `--output-dir`
    (default: current dir); state files in `--state-dir` (default
    `~/.gpw-trading-skills/`).
