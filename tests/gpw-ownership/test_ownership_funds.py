import io
import zipfile
import zlib

import pytest

import ownership_funds as of
import ownership_pdftext as pt


def _hex16(s):
    # glyph ids = ord(c) - 29 (like an Arial subset), mapped back by ToUnicode
    return "<" + "".join("{:04X}".format(ord(c) - 29) for c in s) + ">"


def make_pdf(lines_literal, lines_type0=(), compress=True, objstm=False):
    """Tiny PDF: literal-string lines in font F1 and Identity-H lines in font C2."""
    ops = ["BT"]
    y = 800
    type0 = list(lines_type0)
    for ln in lines_literal:
        try:
            ln.encode("cp1252")
        except UnicodeEncodeError:  # Polish letters: real generators use a Type0 font
            ops += ["/C2 9 Tf", "1 0 0 1 50 {} Tm".format(y), "{} Tj".format(_hex16(ln))]
            type0.append(ln)
            y -= 12
            continue
        ops += ["/F1 9 Tf", "1 0 0 1 50 {} Tm".format(y)]
        esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        # split into two runs on one line, like real generators do
        half = len(esc) // 2
        ops.append("[({})-10 ({})]TJ".format(esc[:half], esc[half:]))
        y -= 12
    for ln in lines_type0:
        ops += ["/C2 9 Tf", "1 0 0 1 50 {} Tm".format(y), "{} Tj".format(_hex16(ln))]
        y -= 12
    ops.append("ET")
    content = "\n".join(ops).encode("cp1252")
    chars = sorted({c for ln in type0 for c in ln})
    cmap = "/CIDInit /ProcSet findresource begin\nbegincmap\n{} beginbfchar\n{}\nendbfchar\nendcmap\n".format(
        len(chars), "\n".join("<{:04X}> <{:04X}>".format(ord(c) - 29, ord(c)) for c in chars)).encode()
    stream = zlib.compress(content) if compress else content
    filt = b"/Filter/FlateDecode" if compress else b""
    objs = {
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 5 0 R/C2 6 0 R>>>>/Contents 4 0 R>>",
        5: b"<</Type/Font/Subtype/TrueType/BaseFont/Arial/Encoding/WinAnsiEncoding>>",
        6: b"<</Type/Font/Subtype/Type0/BaseFont/ArialMT/Encoding/Identity-H/ToUnicode 7 0 R>>",
    }
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    if objstm:
        body = b""
        header = []
        for n in (5, 6):
            header.append("{} {}".format(n, len(body)))
            body += objs.pop(n) + b" "
        hdr = (" ".join(header) + " ").encode()
        data = zlib.compress(hdr + body)
        out.write(b"8 0 obj\n<</Type/ObjStm/N 2/First %d/Filter/FlateDecode/Length %d>>stream\n" % (len(hdr), len(data)))
        out.write(data + b"\nendstream\nendobj\n")
    for n, d in objs.items():
        out.write(b"%d 0 obj\n" % n + d + b"\nendobj\n")
    out.write(b"4 0 obj\n<</Length %d%s>>stream\n" % (len(stream), filt) + stream + b"\nendstream\nendobj\n")
    zc = zlib.compress(cmap)
    out.write(b"7 0 obj\n<</Length %d/Filter/FlateDecode>>stream\n" % len(zc) + zc + b"\nendstream\nendobj\n")
    out.write(b"trailer\n<</Root 1 0 R>>\n%%EOF\n")
    return out.getvalue()


NN_LINES = [
    "Nationale-Nederlanden Otwarty Fundusz Emerytalny Data sporządzenia: 2026-01-02",
    "Roczna struktura aktywów",
    "Data wyceny: 31/12/2025",
    "5 Depozyty bankowe w walucie polskiej 1,38 1 118 613 564,40",
    "Bank Handlowy w Warszawie S.A. 0,23 183 613 564,40",
    "Akcje spółek notowanych na rynku regulowanym na terytorium Rzeczypospolitej Polskiej oraz obligacje zamienne na akcje",
    "tych spółek, a także notowane na tym rynku prawa poboru i prawa do akcji",
    "11 Bit Studios S.A. 0,02 18 082 181,98",
    "AB S.A. 0,38 308 801 236,79",
    "PKO Bank Polski S.A. 9,68 7 824 616 433,13",
    "8 Akcje, prawa poboru i prawa do akcji, będące przedmiotem oferty publicznej 0,00 0,00",
    "MOL Plc 0,23 189 319 055,38",
]


def test_pdf_literal_and_type0_text():
    data = make_pdf(NN_LINES[:3], ["Dino Polska S.A. 1,49 1 204 346 358,29"])
    lines = [ln for p in pt.extract_lines(data) for ln in p]
    assert lines[0].startswith("Nationale-Nederlanden Otwarty Fundusz Emerytalny")
    assert "Data wyceny: 31/12/2025" in lines
    assert "Dino Polska S.A. 1,49 1 204 346 358,29" in lines


def test_pdf_objstm_and_uncompressed():
    data = make_pdf(["AB S.A. 0,38 308 801 236,79"], ["ĄĘ test"], compress=False, objstm=True)
    lines = [ln for p in pt.extract_lines(data) for ln in p]
    assert "AB S.A. 0,38 308 801 236,79" in lines and "ĄĘ test" in lines


def test_pdf_errors():
    with pytest.raises(pt.PDFTextError):
        pt.extract_lines(b"hello")
    assert pt.extract_lines(b"%PDF-1.4\n%%EOF") == []
    with pytest.raises(pt.PDFTextError):
        pt.PDFDoc(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj trailer<</Encrypt 9 0 R>>")


def test_parse_nn_layout_from_pdf():
    data = make_pdf(NN_LINES)
    res = of.parse_lines([ln for p in pt.extract_lines(data) for ln in p])
    assert res["fund"] == "Nationale-Nederlanden Otwarty Fundusz Emerytalny"
    assert res["valuation_date"] == "2025-12-31" and res["section_found"]
    names = [h["name"] for h in res["holdings"]]
    assert names == ["11 Bit Studios S.A.", "AB S.A.", "PKO Bank Polski S.A."]  # deposits and foreign shares excluded
    assert res["holdings"][2]["value_pln"] == pytest.approx(7824616433.13)
    assert res["holdings"][2]["pct_of_fund"] == pytest.approx(9.68)


def test_parse_ppk_layout_value_first():
    lines = ["Roczna struktura aktywów 31 grudnia 2025 r.", "aktywów PPK Pocztylion 2025 DFE na koniec miesiąca",
             "Data wyceny: 31/12/2025",
             "7. Akcje spółek notowane na rynku regulowanym na terytorium Rzeczypospolitej Polskiej oraz obligacjach",
             "Dino Polska S.A. 156 406,10 0,27%", "Grupa Kęty S.A. 179 017,49 0,31%",
             "Poznańska Korporacja Budowlana", "Pekabex S.A. 10 000,00 0,02%",
             "8. Akcje, prawa poboru i prawa do akcji, będące przedmiotem oferty publicznej 0,00 0,00%"]
    res = of.parse_lines(lines)
    assert res["fund"] == "PPK Pocztylion 2025 DFE"
    assert [h["name"] for h in res["holdings"]] == ["Dino Polska S.A.", "Grupa Kęty S.A.",
                                                     "Poznańska Korporacja Budowlana Pekabex S.A."]
    assert res["holdings"][0]["value_pln"] == pytest.approx(156406.10) and res["holdings"][0]["pct_of_fund"] == 0.27


def make_xlsx(rows):
    strings, idx = [], {}
    sheet_rows = []
    for r_i, row in enumerate(rows, 1):
        cells = []
        for c_i, v in enumerate(row):
            ref = "{}{}".format(chr(65 + c_i), r_i)
            if isinstance(v, (int, float)):
                cells.append('<c r="{}"><v>{}</v></c>'.format(ref, v))
            else:
                if v not in idx:
                    idx[v] = len(strings)
                    strings.append(v)
                cells.append('<c r="{}" t="s"><v>{}</v></c>'.format(ref, idx[v]))
        sheet_rows.append('<row r="{}">{}</row>'.format(r_i, "".join(cells)))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", '<sst {}>{}</sst>'.format(ns, "".join("<si><t>{}</t></si>".format(s) for s in strings)))
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet {}><sheetData>{}</sheetData></worksheet>'.format(ns, "".join(sheet_rows)))
    return buf.getvalue()


def test_xlsx_with_header():
    data = make_xlsx([["Data wyceny: 31/12/2025"], ["Nazwa", "Wartość PLN", "Udział %", "Ticker"],
                      ["PKO Bank Polski S.A.", 7824616433.13, 9.68, "PKOBP"], ["Dino Polska S.A.", 1204346358.29, 1.49, ""]])
    res = of.parse_table(of.xlsx_rows(data))
    assert res["valuation_date"] == "2025-12-31" and len(res["holdings"]) == 2
    assert res["holdings"][0]["ticker"] == "PKOBP" and "ticker" not in res["holdings"][1]
    assert res["holdings"][1]["value_pln"] == pytest.approx(1204346358.29)


def test_xlsx_bad_zip():
    with pytest.raises(of.FundsParseError):
        of.xlsx_rows(b"not a zip")


def test_csv_semicolon_polish_numbers():
    text = "Spółka;Wartość;Udział\nAB S.A.;308 801 236,79;0,38\nKruk S.A.;1 298 025 473,00;1,61\n"
    res = of.parse_table(of.csv_rows(text))
    assert [h["name"] for h in res["holdings"]] == ["AB S.A.", "Kruk S.A."]
    assert res["holdings"][1]["value_pln"] == pytest.approx(1298025473.0) and res["holdings"][1]["pct_of_fund"] == 1.61


TICKERS = {"PKOBP", "ABPL", "11BIT", "DINOPL", "KETY", "AUTOPARTN", "PEKABEX", "GTC", "ZEPAK", "ERSTEPL", "SANTANDER"}


@pytest.mark.parametrize("name,ticker", [
    ("PKO Bank Polski S.A.", "PKOBP"), ("AB S.A.", "ABPL"), ("11 Bit Studios S.A.", "11BIT"),
    ("Dino Polska S.A.", "DINOPL"), ("Grupa Kęty S.A.", "KETY"), ("Auto Partner S.A.", "AUTOPARTN"),
    ("Poznańska Korporacja Budowlana Pekabex S.A.", "PEKABEX"), ("Globe Trade Centre S.A.", "GTC"),
    ("ZE PAK S.A.", "ZEPAK"), ("Santander Bank Polska S.A.", "ERSTEPL"), ("Banco Santander S.A.", "SANTANDER"),
    ("Nieznana Spółka S.A.", None)])
def test_match_ticker(name, ticker):
    assert of.match_ticker(name, TICKERS)[0] == ticker


def test_match_ticker_user_map_and_espi_names_win():
    assert of.match_ticker("Foo Bar S.A.", TICKERS, extra={"Foo Bar S.A.": "kety"}) == ("KETY", "user_map")
    assert of.match_ticker("Foo Bar S.A.", TICKERS, espi_names={"FOO BAR SPÓŁKA AKCYJNA": "GTC"}) == ("GTC", "espi_names")


def _h(t, v, px):
    h = {"name": t, "ticker": t, "value_pln": v, "pct_of_fund": 1.0}
    of.enrich([h], {t: px})
    return h


def test_enrich_compare_with_split_and_new_exit():
    prev = [_h("AAA", 1000.0, 10.0), _h("SPL", 5000.0, 500.0), _h("OUT", 300.0, 3.0)]
    cur = [_h("AAA", 1500.0, 10.0), _h("SPL", 5500.0, 50.0), _h("NEW", 200.0, 2.0)]
    assert prev[0]["approx_shares"] == 100
    c = of.compare(cur, prev, {"SPL": 0.1})   # 1:10 split, price 500 -> 50
    assert c["AAA"]["implied_flow_pln"] == pytest.approx(500.0) and c["AAA"]["approx_shares_change"] == 50
    assert c["SPL"]["implied_flow_pln"] == pytest.approx(500.0)
    assert c["SPL"]["approx_shares_prev"] == 100 and c["SPL"]["approx_shares_change"] == 10
    assert c["NEW"]["status"] == "new" and c["NEW"]["implied_flow_pln"] == 200.0
    assert c["OUT"]["status"] == "exited" and c["OUT"]["implied_flow_pln"] == -300.0


def test_aggregate_accumulation_needs_two_funds():
    f1 = {"fund": "F1", "valuation_date": "2025-12-31", "holdings": [_h("AAA", 13e6, 10.0)]}
    f2 = {"fund": "F2", "valuation_date": "2025-12-31", "holdings": [_h("AAA", 13e6, 10.0)]}
    comp = {"F1": of.compare(f1["holdings"], [_h("AAA", 10e6, 10.0)], {}),
            "F2": of.compare(f2["holdings"], [_h("AAA", 10e6, 10.0)], {})}
    agg = of.aggregate([f1, f2], comp)
    (r,) = agg["by_ticker"]
    assert r["funds_holding"] == 2 and r["value_pln"] == 26e6 and r["approx_shares"] == 2_600_000
    assert r["implied_flow_pln"] == pytest.approx(6e6) and r["implied_flow_rel"] == pytest.approx(0.3)
    assert r["fund_accumulation"] and agg["top_buys"][0]["ticker"] == "AAA"
    one = of.aggregate([f1], {"F1": comp["F1"]})["by_ticker"][0]
    assert not one["fund_accumulation"]  # one fund alone is not enough


def test_aggregate_small_flows_not_in_top_lists():
    f = {"fund": "F", "valuation_date": "2025-12-31", "holdings": [_h("AAA", 10.2e6, 10.0)]}
    agg = of.aggregate([f], {"F": of.compare(f["holdings"], [_h("AAA", 10e6, 10.0)], {})})
    assert agg["top_buys"] == [] and agg["by_ticker"][0]["implied_flow_pln"] == pytest.approx(0.2e6)
