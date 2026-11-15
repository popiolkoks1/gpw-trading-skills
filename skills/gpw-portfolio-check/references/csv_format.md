# Holdings CSV format

## Columns

| Column | Required | Aliases accepted (case-insensitive) | Notes |
|---|---|---|---|
| `ticker` | yes | symbol, walor, instrument, nazwa, papier, spółka, kod, skrót | GPW archive short name (PKOBP, PKNORLEN, CDPROJEKT, DINOPL, ETFBW20TR...) |
| `shares` | yes | ilość, ilosc, liczba, liczba_akcji, quantity, qty, sztuk | whole number > 0 (GPW lot = 1 share) |
| `avg_price` | yes | cena, średnia cena, cena średnia, kurs średni, cena zakupu, cena nabycia, price | PLN per share, > 0 |
| `account_type` | no | rachunek, konto, account, typ rachunku | regular (zwykły, standard, maklerski, rm), ike, ikze, oki; default regular |
| `stop` | no | stop_loss, sl | if the column exists, positions without a value are flagged "no stop" and portfolio heat is computed |

A header row is required. Spaces, dashes and dots in headers are normalised
(`Średnia cena` → `średnia_cena`).

## Number formats

`58,20` · `58.20` · `1 234,56` · `1.234,56` · `1,234.56` · `12,50 zł` all work.
With a **comma** separator a decimal comma must be quoted (`"58,20"`), otherwise
the row has too many fields and the script stops with a hint. Polish brokers
usually export with `;`, which needs no quoting.

## Other tolerances

- UTF-8 with or without BOM; Windows-1250 files (older Polish exports) are decoded as a fallback.
- `;`, tab or `,` separator (detected from the header line).
- Lines starting with `#` and blank lines are ignored.
- The same ticker on several rows (several lots or accounts) is merged:
  shares summed, average price weighted, shares per account kept.
- Common 3-letter tickers are mapped when the target exists in the archive:
  PKO→PKOBP, PKN/ORLEN→PKNORLEN, KGH→KGHM, CDR→CDPROJEKT, PEO→PEKAO,
  DNP/DINO→DINOPL, ALE→ALLEGRO, MBK→MBANK, KTY→KETY, ALR→ALIOR, PCO→PEPCO,
  BDX→BUDIMEX, TPE→TAURONPE, KRU→KRUK, OPL→ORANGEPL, CPS→CYFRPLSAT,
  ACP→ASSECOPOL, MIL→MILLENNIUM, ING→INGBSK, BHW→HANDLOWY, ENA→ENEA,
  ATT→GRUPAAZOTY. The mapping is shown in the report. ISINs are not supported.

## Examples

```csv
Walor;Ilość;Średnia cena;Rachunek
PKOBP;150;58,20;zwykły
PZU;200;52,10;IKE
PKN;120;71,30;IKE
ETFBW20TR;50;80,50;IKZE
```

```csv
ticker,shares,avg_price,account_type,stop
KGHM,40,"160,50",regular,240
CDPROJEKT,25,240,regular,
```

## Errors (exit code 1)

| Message | Fix |
|---|---|
| header must contain ticker, shares, avg_price | rename columns (aliases above) |
| more fields than columns | quote decimal commas or use `;` |
| shares must be a whole number | GPW has no fractional shares |
| 'XYZ' not found ... close matches: ... | use one of the suggested archive names |
