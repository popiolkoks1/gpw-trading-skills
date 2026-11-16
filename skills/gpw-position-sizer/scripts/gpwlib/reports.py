"""Find and load another skill's newest JSON report (skills talk through files).

One implementation of "find the newest JSON of skill X in ``--input-dir``",
read its ``as_of``, count sessions behind and warn when it is too old
(CONVENTIONS rule 11).

    rep = reports.find_report("gpw-market-regime", args.input_dir,
                              explicit_path=args.regime_json, lang=args.lang,
                              reference=as_of_or_None)
    if rep is None: ...            # missing -> work with lower confidence
    elif rep["stale"]: ...         # rep["warning"] holds the PL/EN text

Discovery rules
---------------
1. ``explicit_path`` given -> load exactly that file. A missing, unreadable or
   corrupt file, or one whose ``skill`` field names another skill, raises
   :class:`ReportError` (the user asked for that file, so silence would hide
   a mistake).
2. Otherwise every ``*.json`` directly in ``input_dir`` is read; files whose
   top-level ``"skill"`` equals ``skill`` are candidates. Corrupt files are
   skipped.
3. If there is none and ``pattern`` is given (e.g. ``"gpw_breadth_*.json"``),
   files matching it that carry no ``skill`` field are used (reports written
   before the field existed).
4. The newest candidate wins: highest ``as_of`` first, then file mtime.
   Reports dated after ``reference`` (when given) are ignored: a historical
   run must not peek at later reports.

Missing directory / no candidate -> ``None`` (never raises).

:func:`find_reports` returns the newest report of several skills at once;
:func:`iter_reports` returns *every* report (of one skill or of all skills)
in a folder, oldest first - e.g. for a postmortem that ingests history.

Report variants
---------------
Some skills write two different report *types* under one ``skill`` name,
told apart by the top-level ``"mode"``: gpw-sector-trend writes the classic
uptrend-ratio report (``gpw_sector_trend_*.json``, no ``mode``) and the RRG
report (``gpw_sector_rrg_*.json``, ``"mode": "rrg"``, ``rrg.items[]``). These
are listed in :data:`REPORT_VARIANTS`. Unless the caller asks for a ``mode``,
:func:`find_report`, :func:`find_reports` and :func:`iter_reports` (with a
``skill``) skip the variants, so a consumer of the classic report can never
read an RRG report by accident; pass ``mode="rrg"`` to get the RRG report.
``exclude_modes`` overrides the default exclusion (``()`` = accept all).

``as_of`` is read from the top-level ``as_of``, then ``data_provenance.as_of``,
then ``regime.as_of``. Sessions behind are counted with
``provenance.sessions_behind`` from ``as_of`` to ``reference`` (default:
today in Warsaw); ``stale`` = more than ``max_sessions_behind`` (default 3,
the cross-skill rule in docs/CONVENTIONS.md) or no ``as_of`` at all.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from . import plcal, provenance

DEFAULT_MAX_SESSIONS_BEHIND = 3
MAX_FILE_BYTES = 50 * 1024 * 1024  # skip giant files instead of loading them

PathLike = Union[str, Path]


# skill -> modes that are a different report type (see "Report variants")
REPORT_VARIANTS: Dict[str, tuple] = {"gpw-sector-trend": ("rrg",)}


def report_mode(data: Dict) -> Optional[str]:
    m = data.get("mode") if isinstance(data, dict) else None
    return m if isinstance(m, str) else None


def is_variant(data: Dict, skill: Optional[str] = None) -> bool:
    """True when ``data`` is a variant report type of its skill (e.g. the RRG
    report of gpw-sector-trend)."""
    sk = skill or (data.get("skill") if isinstance(data, dict) else None)
    return report_mode(data) in REPORT_VARIANTS.get(sk or "", ())


def _mode_filter(skill: str, mode: Optional[str], exclude_modes: Optional[Iterable[str]]):
    if mode is not None:
        return lambda d: report_mode(d) == mode
    excl = tuple(REPORT_VARIANTS.get(skill, ())) if exclude_modes is None else tuple(exclude_modes)
    if not excl:
        return lambda d: True
    return lambda d: report_mode(d) not in excl


class ReportError(ValueError):
    """An explicitly requested report cannot be used."""


def report_as_of(data: Dict) -> Optional[dt.date]:
    """Business date of a report (see module docstring for the lookup order)."""
    if not isinstance(data, dict):
        return None
    for val in (data.get("as_of"),
                (data.get("data_provenance") or {}).get("as_of") if isinstance(data.get("data_provenance"), dict) else None,
                (data.get("regime") or {}).get("as_of") if isinstance(data.get("regime"), dict) else None):
        if isinstance(val, str) and len(val) >= 10:
            try:
                return dt.date.fromisoformat(val[:10])
            except ValueError:
                continue
    return None


def load_report(path: PathLike) -> Dict:
    """Read a JSON report; raises :class:`ReportError` when missing or corrupt."""
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReportError("Report file not found: {}".format(p))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ReportError("Cannot read report {}: {}".format(p, exc))
    if not isinstance(data, dict):
        raise ReportError("Report {} is not a JSON object".format(p))
    return data


_WARN = {
    "stale": {
        "pl": "Raport {skill} ({file}) ma dane na {as_of} - {n} sesji wstecz (limit {max}). Traktuj go ostrożnie.",
        "en": "The {skill} report ({file}) is as of {as_of} - {n} sessions old (limit {max}). Treat it with caution.",
    },
    "no_as_of": {
        "pl": "Raport {skill} ({file}) nie podaje daty danych (as_of) - nie wiadomo, czy jest aktualny.",
        "en": "The {skill} report ({file}) has no as_of date - its freshness is unknown.",
    },
}


def _warning(kind: str, **kw) -> Dict[str, str]:
    return {lang: txt.format(**kw) for lang, txt in _WARN[kind].items()}


def describe(path: Path, data: Dict, skill: str, reference: Optional[dt.date] = None,
             max_sessions_behind: int = DEFAULT_MAX_SESSIONS_BEHIND, lang: str = "pl") -> Dict:
    """Build the result dict for a loaded report."""
    as_of = report_as_of(data)
    ref = reference or plcal.today_warsaw()
    behind = provenance.sessions_behind(as_of, ref) if as_of else None
    stale = behind is None or behind > max_sessions_behind
    warn = None
    if as_of is None:
        warn = _warning("no_as_of", skill=skill, file=path.name)
    elif stale:
        warn = _warning("stale", skill=skill, file=path.name, as_of=as_of.isoformat(), n=behind,
                        max=max_sessions_behind)
    return {
        "skill": skill,
        "path": str(path),
        "file": path.name,
        "data": data,
        "as_of": as_of.isoformat() if as_of else None,
        "reference_date": ref.isoformat(),
        "sessions_behind": behind,
        "max_sessions_behind": max_sessions_behind,
        "stale": stale,
        "warning": warn,  # {"pl": ..., "en": ...} or None
        "warning_text": warn.get(lang, warn.get("en")) if warn else None,
    }


def _candidates(skill: str, folder: Path, pattern: Optional[str], keep=None) -> List[tuple]:
    out, untagged = [], []
    try:
        files = [p for p in folder.glob("*.json") if p.is_file()]
    except OSError:
        return []
    pattern_files = set(folder.glob(pattern)) if pattern else set()
    for p in files:
        try:
            st = p.stat()
            if st.st_size > MAX_FILE_BYTES:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if keep is not None and not keep(data):
            continue
        if data.get("skill") == skill:
            out.append((p, data, st.st_mtime))
        elif "skill" not in data and p in pattern_files:
            untagged.append((p, data, st.st_mtime))
    return out or untagged


def find_report(skill: str, input_dir: Optional[PathLike] = ".", explicit_path: Optional[PathLike] = None,
                pattern: Optional[str] = None, reference: Optional[dt.date] = None,
                max_sessions_behind: int = DEFAULT_MAX_SESSIONS_BEHIND, lang: str = "pl",
                mode: Optional[str] = None, exclude_modes: Optional[Iterable[str]] = None) -> Optional[Dict]:
    """Newest report of ``skill`` -> dict (see :func:`describe`) or None.

    Keys: ``skill, path, file, data, as_of, reference_date, sessions_behind,
    max_sessions_behind, stale, warning {pl, en} | None, warning_text``.
    ``mode`` / ``exclude_modes``: see "Report variants" in the module
    docstring (an explicit file of the wrong variant raises ReportError).
    """
    keep = _mode_filter(skill, mode, exclude_modes)
    if explicit_path:
        p = Path(explicit_path).expanduser()
        data = load_report(p)
        if "skill" in data and data.get("skill") != skill:
            raise ReportError("{} is a {!r} report, expected {!r}".format(p, data.get("skill"), skill))
        if not keep(data):
            raise ReportError("{} is a {!r} report of mode {!r}, expected {}".format(
                p, skill, report_mode(data), "mode {!r}".format(mode) if mode else "the main report"))
        return describe(p, data, skill, reference, max_sessions_behind, lang)
    if input_dir is None:
        return None
    folder = Path(input_dir).expanduser()
    if not folder.is_dir():
        return None
    cands = _candidates(skill, folder, pattern, keep)
    if reference is not None:
        cands = [c for c in cands if (report_as_of(c[1]) or dt.date.min) <= reference]
    if not cands:
        return None

    def key(c):
        a = report_as_of(c[1])
        return (a or dt.date.min, c[2])

    p, data, _ = max(cands, key=key)
    return describe(p, data, skill, reference, max_sessions_behind, lang)


def find_reports(skills: Union[Iterable[str], Dict[str, Optional[PathLike]]], input_dir: Optional[PathLike] = ".",
                 patterns: Optional[Dict[str, str]] = None, reference: Optional[dt.date] = None,
                 max_sessions_behind: int = DEFAULT_MAX_SESSIONS_BEHIND, lang: str = "pl") -> Dict[str, Optional[Dict]]:
    """Several skills at once: ``{skill: result | None}``.

    ``skills`` is a list of skill names or a dict ``{skill: explicit_path_or_None}``.
    Variant reports (:data:`REPORT_VARIANTS`) are skipped, as in :func:`find_report`.
    """
    explicit = dict(skills) if isinstance(skills, dict) else {s: None for s in skills}
    patterns = patterns or {}
    return {s: find_report(s, input_dir, explicit_path=explicit[s], pattern=patterns.get(s), reference=reference,
                           max_sessions_behind=max_sessions_behind, lang=lang)
            for s in explicit}


def iter_reports(input_dir: Optional[PathLike], skill: Optional[str] = None, pattern: Optional[str] = None,
                 reference: Optional[dt.date] = None, newest_first: bool = False,
                 mode: Optional[str] = None, exclude_modes: Optional[Iterable[str]] = None) -> List[Dict]:
    """Every report in ``input_dir`` (not only the newest), e.g. to ingest a
    skill's whole history.

    Returns ``[{skill, path, file, data, as_of, mtime}]`` sorted by ``as_of``
    then file mtime (oldest first unless ``newest_first``). With ``skill``
    only files whose top-level ``"skill"`` equals it are returned (plus, when
    ``pattern`` is given and nothing is tagged, untagged files matching it -
    the same rules as :func:`find_report`, including the variant filter -
    ``mode`` / ``exclude_modes``); without ``skill`` every JSON object that
    carries a ``"skill"`` field is returned, variants included (each item has
    ``mode`` and ``variant`` so the caller can tell them apart). Reports dated after
    ``reference`` are left out. Corrupt or oversized files are skipped; a
    missing directory gives ``[]``.
    """
    if input_dir is None:
        return []
    folder = Path(input_dir).expanduser()
    if not folder.is_dir():
        return []
    if skill is not None:
        cands = _candidates(skill, folder, pattern, _mode_filter(skill, mode, exclude_modes))
    else:
        cands = []
        try:
            files = [p for p in folder.glob("*.json") if p.is_file()]
        except OSError:
            files = []
        for p in files:
            try:
                st = p.stat()
                if st.st_size > MAX_FILE_BYTES:
                    continue
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get("skill"), str):
                cands.append((p, data, st.st_mtime))
    out = []
    for p, data, mtime in cands:
        a = report_as_of(data)
        if reference is not None and (a or dt.date.min) > reference:
            continue
        sk = data.get("skill", skill)
        out.append({"skill": sk, "path": str(p), "file": p.name, "data": data,
                    "as_of": a.isoformat() if a else None, "mtime": mtime,
                    "mode": report_mode(data), "variant": is_variant(data, sk)})
    out.sort(key=lambda r: (r["as_of"] or "", r["mtime"], r["file"]), reverse=newest_first)
    return out
