"""Bilingual (PL/EN) formatting helpers used by report generators."""

from __future__ import annotations

from typing import Dict, Optional

LANGS = ("pl", "en")


def num(x: Optional[float], digits: int = 2, lang: str = "pl") -> str:
    """1234.5 -> '1 234,50' (pl) / '1,234.50' (en)."""
    if x is None:
        return "—"
    x = round(x, digits) + 0.0  # avoid "-0,0"
    if x == 0:
        x = 0.0
    s = "{:,.{d}f}".format(x, d=digits)
    if lang == "pl":
        s = s.replace(",", " ").replace(".", ",")
    return s


def pct(x: Optional[float], digits: int = 1, lang: str = "pl", signed: bool = False) -> str:
    if x is None:
        return "—"
    s = num(x, digits, lang)
    if signed and x > 0:
        s = "+" + s
    return s + "%"


def pln(x: Optional[float], digits: int = 2, lang: str = "pl") -> str:
    if x is None:
        return "—"
    return num(x, digits, lang) + (" zł" if lang == "pl" else " PLN")


class T:
    """Tiny translation table: T(strings, lang)('key', **fmt)."""

    def __init__(self, strings: Dict[str, Dict[str, str]], lang: str = "pl"):
        if lang not in LANGS:
            raise ValueError("lang must be one of {}".format(LANGS))
        self.strings = strings
        self.lang = lang

    def __call__(self, key: str, **kw) -> str:
        entry = self.strings.get(key)
        if entry is None:
            return key
        text = entry.get(self.lang) or entry.get("en") or key
        return text.format(**kw) if kw else text


DISCLAIMER = {
    "pl": (
        "Materiał edukacyjny. Nie stanowi rekomendacji inwestycyjnej ani doradztwa "
        "inwestycyjnego, podatkowego czy prawnego. Decyzje inwestycyjne i ich skutki "
        "podejmujesz wyłącznie Ty. Inwestowanie wiąże się z ryzykiem utraty kapitału."
    ),
    "en": (
        "Educational material. Not investment, tax or legal advice and not an "
        "investment recommendation. All decisions and their consequences are yours. "
        "Investing involves the risk of losing capital."
    ),
}
