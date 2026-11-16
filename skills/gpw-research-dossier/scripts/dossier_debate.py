"""Bull/bear debate on a dossier: template, deterministic validation, thesis command.

Claude writes the arguments (references/debate_protocol.md); this module only
checks structure and citations, so the guardrail is reproducible:

* ``make_template``  - fill-in skeleton with every available fact id;
* ``validate``       - every bull/bear claim cites >= 1 existing fact id, each
                       side has 3-5 points, kill criteria and a pre-mortem
                       exist, unknowns are listed when the dossier has gaps,
                       no placeholder is left;
* ``thesis_command`` - turns a valid debate into a ready
                       ``gpw-trade-journal add ...`` command (never executed).
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional, Set, Tuple

PLACEHOLDER = "<<"
MIN_POINTS, MAX_POINTS = 3, 5          # TradingAgents-style rounds, kept short (protocol §2)
MIN_CLAIM_CHARS = 12                   # shorter than this is a label, not a claim
MAX_PILLARS = 5                        # gpw-trade-journal accepts 1-5 pillars
MAX_RISKS_FROM_BEAR = 3
STRENGTHS = ("high", "medium", "low")
BALANCES = ("bear_heavier", "balanced", "bull_heavier", "insufficient_evidence")

# gpw-trade-journal enums (skills/gpw-trade-journal/references/journal_schema.md)
SETUPS = ("breakout", "trend", "pullback", "value", "dividend", "growth", "earnings", "mean_reversion", "other")
HORIZONS = ("swing", "position", "long_term")
CONVICTION = ("H", "M", "L")
ACCOUNT_TYPES = ("regular", "ike", "ikze", "oki")

# Words that turn "verdict inputs" into a call to action (warning, not error:
# "insider sells" is a legitimate phrase).
_CALL_TO_ACTION = re.compile(r"\b(kupuj|kupić|kup|sprzedaj|sprzedać|buy|sell|strong buy|overweight)\b", re.I)


def _ph(text: str) -> str:
    return "{} {} >>".format(PLACEHOLDER, text)


def make_template(dossier: Dict, dossier_file: Optional[str], lang: str = "pl") -> Dict:
    facts = dossier.get("facts") or []
    pl = lang == "pl"
    unknowns = []
    for m in dossier.get("missing") or []:
        unknowns.append({"text": ("Brak sekcji {}: {}" if pl else "Missing section {}: {}").format(
            m["skill"], m.get("reason")), "section": m["skill"]})
    for s in dossier.get("stale_sections") or []:
        unknowns.append({"text": ("Sekcja {} jest nieaktualna" if pl else "Section {} is stale").format(s), "section": s})
    trend = (dossier.get("sections") or {}).get("gpw-trend-screener", {}).get("key_fields", {})
    sizer = (dossier.get("next_steps") or {}).get("position_sizer") or {}

    def point():
        return {"claim": _ph("twierdzenie oparte na faktach" if pl else "claim grounded in facts"),
                "fact_ids": [], "strength": "medium"}

    return {
        "skill": "gpw-research-dossier", "kind": "debate", "version": "1.0.0", "lang": lang,
        "ticker": dossier.get("ticker"), "as_of": dossier.get("as_of"), "dossier_file": dossier_file,
        "protocol": "references/debate_protocol.md",
        "instructions": ([
            "Najpierw BEAR (3-5 punktów), potem BULL (3-5). Każdy punkt cytuje >=1 fact_id z available_fact_ids.",
            "Steelman: każda strona w najmocniejszej uczciwej wersji. Brak faktu = wpis w unknowns, nie zgadywanie.",
            "Risk manager: wielkość pozycji z komendy gpw-position-sizer, kryteria unieważnienia, co zmieni zdanie.",
            "Pre-mortem: 'jest za rok, pozycja straciła 30% - dlaczego?'. verdict_inputs to NIE jest rekomendacja.",
        ] if pl else [
            "BEAR first (3-5 points), then BULL (3-5). Each point cites >=1 fact_id from available_fact_ids.",
            "Steelman: each side in its strongest honest form. A missing fact goes to unknowns, never a guess.",
            "Risk manager: size from the gpw-position-sizer command, kill criteria, what would change the view.",
            "Pre-mortem: 'a year from now the position lost 30% - why?'. verdict_inputs is NOT a recommendation.",
        ]),
        "available_fact_ids": [f["id"] for f in facts],
        "facts_digest": {f["id"]: f["value"] for f in facts},
        "bear": [point() for _ in range(MIN_POINTS)],
        "bull": [point() for _ in range(MIN_POINTS)],
        "rebuttals": [],
        "risk_manager": {
            "position_sizing": {"command": sizer.get("command"), "risk_pct": sizer.get("risk_pct"),
                                "risk_pct_source": sizer.get("risk_pct_source"),
                                "entry": sizer.get("entry"), "stop": sizer.get("stop"),
                                "fact_ids": sizer.get("fact_ids") or []},
            "kill_criteria": [{"text": _ph("obserwowalny warunek, np. zamknięcie poniżej X" if pl
                                           else "observable condition, e.g. close below X"), "fact_ids": []}],
            "change_view": [{"text": _ph("co zmieniłoby ocenę" if pl else "what would change the view"),
                             "fact_ids": []}],
        },
        "pre_mortem": [{"text": _ph("najbardziej prawdopodobna przyczyna porażki" if pl
                                    else "most likely cause of failure"), "fact_ids": []}],
        "unknowns": unknowns,
        "verdict_inputs": {
            "summary": _ph("bilans dowodów, bez rekomendacji" if pl else "balance of evidence, no recommendation"),
            "evidence_balance": _ph("|".join(BALANCES)),
            "strongest_bear_fact_ids": [], "strongest_bull_fact_ids": [],
            "conditions_to_revisit": [],
            "not_a_recommendation": True,
        },
        "thesis": {
            "thesis": _ph("jedno-dwa zdania, dlaczego cena miałaby rosnąć" if pl else "one or two sentences"),
            "pillars": [], "setup": "other", "horizon": "swing", "conviction": "M", "account_type": "regular",
            "plan": {"entry": trend.get("pivot"), "stop": trend.get("stop_structure"), "target": None},
        },
    }


def _strings(obj: Any, path: str = "") -> List[Tuple[str, str]]:
    out = []
    if isinstance(obj, str):
        out.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += _strings(v, "{}.{}".format(path, k) if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _strings(v, "{}[{}]".format(path, i))
    return out


def validate(debate: Dict, fact_ids: Set[str], stale_ids: Optional[Set[str]] = None,
             dossier_missing: Optional[List[Dict]] = None) -> Dict:
    """Deterministic guardrail. Returns {valid, errors[], warnings[], stats{}}."""
    errors: List[Dict] = []
    warnings: List[Dict] = []
    stale_ids = stale_ids or set()

    def err(code, path, **kw):
        errors.append(dict(code=code, path=path, **kw))

    def warn(code, path, **kw):
        warnings.append(dict(code=code, path=path, **kw))

    if not isinstance(debate, dict):
        return {"valid": False, "errors": [{"code": "not_an_object", "path": ""}], "warnings": [], "stats": {}}
    cited: List[str] = []
    sections_by_side: Dict[str, Set[str]] = {}
    for side in ("bear", "bull"):
        pts = debate.get(side)
        if not isinstance(pts, list):
            err("side_missing", side)
            continue
        if len(pts) < MIN_POINTS:
            err("too_few_points", side, n=len(pts), min=MIN_POINTS)
        if len(pts) > MAX_POINTS:
            err("too_many_points", side, n=len(pts), max=MAX_POINTS)
        seen = set()
        sections_by_side[side] = set()
        for i, p in enumerate(pts):
            path = "{}[{}]".format(side, i)
            if not isinstance(p, dict):
                err("point_not_object", path)
                continue
            claim = (p.get("claim") or "").strip()
            if len(claim) < MIN_CLAIM_CHARS:
                err("claim_too_short", path + ".claim", min=MIN_CLAIM_CHARS)
            if claim.lower() in seen:
                warn("duplicate_claim", path + ".claim")
            seen.add(claim.lower())
            ids = p.get("fact_ids")
            if not isinstance(ids, list) or not ids:
                err("no_citation", path + ".fact_ids")
                ids = []
            for fid in ids:
                if fid not in fact_ids:
                    err("unknown_fact_id", path + ".fact_ids", fact_id=fid)
                else:
                    cited.append(fid)
                    sections_by_side[side].add(fid.split(".")[0])
                    if fid in stale_ids:
                        warn("cites_stale_fact", path + ".fact_ids", fact_id=fid)
            if p.get("strength") is not None and p.get("strength") not in STRENGTHS:
                err("bad_strength", path + ".strength", allowed=list(STRENGTHS))
        if len(sections_by_side[side]) == 1 and len(pts) >= MIN_POINTS:
            warn("single_section_side", side, section=next(iter(sections_by_side[side])))

    def check_items(key_path: str, items, min_n: int, require_ids: bool = False):
        if not isinstance(items, list) or len(items) < min_n:
            err("too_few_items", key_path, min=min_n, n=len(items) if isinstance(items, list) else 0)
            return
        for i, it in enumerate(items):
            p = "{}[{}]".format(key_path, i)
            text = it.get("text") if isinstance(it, dict) else it
            if not isinstance(text, str) or len(text.strip()) < MIN_CLAIM_CHARS:
                err("text_too_short", p, min=MIN_CLAIM_CHARS)
            ids = it.get("fact_ids") if isinstance(it, dict) else []
            if require_ids and not ids:
                err("no_citation", p + ".fact_ids")
            for fid in ids or []:
                if fid not in fact_ids:
                    err("unknown_fact_id", p + ".fact_ids", fact_id=fid)
                else:
                    cited.append(fid)

    rm = debate.get("risk_manager")
    if not isinstance(rm, dict):
        err("risk_manager_missing", "risk_manager")
    else:
        check_items("risk_manager.kill_criteria", rm.get("kill_criteria"), 1)
        check_items("risk_manager.change_view", rm.get("change_view"), 1)
        ps = rm.get("position_sizing") or {}
        if not ps.get("command"):
            warn("no_sizer_command", "risk_manager.position_sizing.command")
        for fid in ps.get("fact_ids") or []:
            if fid not in fact_ids:
                err("unknown_fact_id", "risk_manager.position_sizing.fact_ids", fact_id=fid)
    check_items("pre_mortem", debate.get("pre_mortem"), 1)

    unknowns = debate.get("unknowns")
    if not isinstance(unknowns, list):
        err("unknowns_missing", "unknowns")
    elif dossier_missing and not unknowns:
        err("unknowns_empty_with_gaps", "unknowns", missing=[m.get("skill") for m in dossier_missing])

    vi = debate.get("verdict_inputs")
    if not isinstance(vi, dict):
        err("verdict_inputs_missing", "verdict_inputs")
    else:
        if len((vi.get("summary") or "").strip()) < MIN_CLAIM_CHARS:
            err("verdict_summary_missing", "verdict_inputs.summary")
        if vi.get("evidence_balance") not in BALANCES:
            err("bad_evidence_balance", "verdict_inputs.evidence_balance", allowed=list(BALANCES))
        if vi.get("not_a_recommendation") is not True:
            err("must_not_be_recommendation", "verdict_inputs.not_a_recommendation")
        for key in ("strongest_bear_fact_ids", "strongest_bull_fact_ids"):
            for fid in vi.get(key) or []:
                if fid not in fact_ids:
                    err("unknown_fact_id", "verdict_inputs." + key, fact_id=fid)
        if _CALL_TO_ACTION.search(vi.get("summary") or ""):
            warn("call_to_action_language", "verdict_inputs.summary")

    for path, s in _strings(debate):
        if PLACEHOLDER in s:
            # the thesis block is optional for the debate (required only by to-thesis)
            if path.startswith("thesis"):
                warn("thesis_not_filled", path)
            else:
                err("placeholder_left", path)

    n_bear = len(debate.get("bear") or []) if isinstance(debate.get("bear"), list) else 0
    n_bull = len(debate.get("bull") or []) if isinstance(debate.get("bull"), list) else 0
    stats = {"bear_points": n_bear, "bull_points": n_bull, "citations": len(cited),
             "distinct_facts_cited": len(set(cited)), "facts_available": len(fact_ids),
             "sections_cited": sorted({c.split(".")[0] for c in cited}),
             "unknowns": len(unknowns) if isinstance(unknowns, list) else 0}
    return {"valid": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def _cite(text: str, ids: List[str]) -> str:
    return "{} [{}]".format(text.strip(), ", ".join(ids)) if ids else text.strip()


def thesis_command(debate: Dict, dossier: Optional[Dict], journal_dir: str, *, lang: str = "pl",
                   status: str = "IDEA", python: str = "python3") -> Dict:
    """Build the gpw-trade-journal ``add`` command. Raises ValueError on bad input."""
    th = debate.get("thesis") or {}
    text = (th.get("thesis") or "").strip()
    if not text or PLACEHOLDER in text:
        raise ValueError("thesis.thesis is empty or still a placeholder")
    notes: List[str] = []
    raw_pillars = th.get("pillars") or []
    pillars = []
    for p in raw_pillars:
        if isinstance(p, dict):
            pillars.append(_cite(p.get("text") or "", p.get("fact_ids") or []))
        elif isinstance(p, str) and p.strip():
            pillars.append(p.strip())
    if not pillars:
        pillars = [_cite(p.get("claim") or "", p.get("fact_ids") or []) for p in debate.get("bull") or []
                   if isinstance(p, dict)]
        notes.append("pillars_from_bull_points")
    if len(pillars) > MAX_PILLARS:
        notes.append("pillars_truncated_to_{}".format(MAX_PILLARS))
        pillars = pillars[:MAX_PILLARS]
    kills = [_cite(k.get("text") if isinstance(k, dict) else str(k), (k.get("fact_ids") if isinstance(k, dict) else []) or [])
             for k in (debate.get("risk_manager") or {}).get("kill_criteria") or []]
    kills = [k for k in kills if k and PLACEHOLDER not in k]
    if not kills:
        raise ValueError("at least one kill criterion is required (gpw-trade-journal rule)")
    risks = [_cite(p.get("claim") or "", p.get("fact_ids") or []) for p in (debate.get("bear") or [])
             if isinstance(p, dict)][:MAX_RISKS_FROM_BEAR]
    catalysts = []
    if dossier:
        ev = (dossier.get("sections") or {}).get("gpw-event-calendar", {}).get("key_fields", {})
        if ev.get("next_earnings_date"):
            catalysts.append("{} {} ({})".format(ev["next_earnings_date"], "raport okresowy" if lang == "pl" else "periodic report",
                                                ev.get("next_earnings_kind")))
        if ev.get("next_ex_dividend_date"):
            catalysts.append("{} {} ({})".format(ev["next_ex_dividend_date"], "odcięcie dywidendy" if lang == "pl" else "ex-dividend",
                                                ev.get("next_ex_dividend_kind")))
    plan = th.get("plan") or {}
    entry, stop, target = plan.get("entry"), plan.get("stop"), plan.get("target")
    for name, v in (("entry", entry), ("stop", stop), ("target", target)):
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            raise ValueError("plan.{} must be a positive number".format(name))
    if entry is not None and stop is not None and not stop < entry:
        raise ValueError("plan.stop must be below plan.entry")
    if entry is not None and target is not None and not target > entry:
        raise ValueError("plan.target must be above plan.entry")
    for key, allowed in (("setup", SETUPS), ("horizon", HORIZONS), ("conviction", CONVICTION),
                         ("account_type", ACCOUNT_TYPES)):
        if th.get(key) is not None and th.get(key) not in allowed:
            raise ValueError("thesis.{} must be one of {}".format(key, ", ".join(allowed)))
    if status not in ("IDEA", "READY"):
        raise ValueError("status must be IDEA or READY")
    ticker = debate.get("ticker") or (dossier or {}).get("ticker")
    if not ticker:
        raise ValueError("ticker missing in the debate file")
    argv = [python, "scripts/journal.py", "add", "--ticker", ticker, "--thesis", text]
    for p in pillars:
        argv += ["--pillar", p]
    for k in kills:
        argv += ["--kill", k]
    for r in risks:
        argv += ["--risk", r]
    for c in catalysts:
        argv += ["--catalyst", c]
    for flag, v in (("--entry", entry), ("--stop", stop), ("--target", target)):
        if v is not None:
            argv += [flag, "{:g}".format(v)]
    argv += ["--account-type", th.get("account_type") or "regular", "--setup", th.get("setup") or "other",
             "--horizon", th.get("horizon") or "swing", "--conviction", th.get("conviction") or "M",
             "--status", status, "--lang", lang]
    if target is None:
        notes.append("no_target")
    command = "cd {} && {}".format(shlex.quote(journal_dir), " ".join(shlex.quote(a) for a in argv))
    return {"argv": argv, "command": command, "cwd": journal_dir, "executed": False, "notes": notes,
            "pillars": pillars, "kill_criteria": kills, "risks": risks, "catalysts": catalysts}
