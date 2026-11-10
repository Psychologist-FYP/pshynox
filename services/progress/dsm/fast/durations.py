"""Deterministic duration parser for Stage B in code (English, Roman Urdu, Urdu script).

``parse("about two months")`` → ``{"days": 60, "kind": "approximate", "text": "two months"}``. Unparseable or
non-duration text ("current_episode", "ongoing", "since childhood") returns ``None``: an unknown duration, never 0.

Kinds: ``exact`` (a stated number and unit), ``at_least`` ("more than …", "for months", "the past few weeks"),
``approximate`` ("about …", "a few …", "kuch …"). Lower bounds are used for vague quantities, so a vague phrase never
inflates a duration past a DSM threshold. Phrases relative to a date ("since summer", "since March") resolve only when
the session date is known. Readings of vague phrases are a project heuristic pending clinical review (plan §11).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

MONTH = 365.25 / 12  # so "six months" is 183 days, the GAD/SAD threshold
UNITS = {
    # English
    "day": 1, "days": 1, "week": 7, "weeks": 7, "wk": 7, "wks": 7, "fortnight": 14, "fortnights": 14,
    "month": MONTH, "months": MONTH, "mo": MONTH, "semester": 120, "semesters": 120, "term": 90, "terms": 90,
    "year": 365, "years": 365, "yr": 365, "yrs": 365,
    # Roman Urdu
    "din": 1, "dino": 1, "dinon": 1, "dinoon": 1, "roz": 1, "hafta": 7, "hafte": 7, "haftay": 7, "haftey": 7, "hafton": 7,
    "mahina": MONTH, "mahine": MONTH, "mahinay": MONTH, "mahiney": MONTH, "mahino": MONTH, "mahinon": MONTH, "maah": MONTH,
    "saal": 365, "sal": 365, "saalon": 365, "baras": 365, "baris": 365,
    # Urdu script
    "دن": 1, "دنوں": 1, "روز": 1, "ہفتہ": 7, "ہفتے": 7, "ہفتوں": 7, "مہینہ": MONTH, "مہینے": MONTH, "مہینوں": MONTH, "ماہ": MONTH,
    "سال": 365, "سالوں": 365, "برس": 365,
}
NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty": 20, "thirty": 30,
    "ek": 1, "aik": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "panch": 5, "paanch": 5, "chay": 6, "chhe": 6, "che": 6,
    "saat": 7, "aath": 8, "aat": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "pandrah": 15, "bees": 20,
    "ایک": 1, "دو": 2, "تین": 3, "چار": 4, "پانچ": 5, "چھ": 6, "سات": 7, "آٹھ": 8, "نو": 9, "دس": 10, "بارہ": 12,
}
VAGUE = {"couple": 2, "few": 2, "several": 3, "kuch": 2, "kai": 2, "chand": 2, "کچھ": 2, "کئی": 2, "چند": 2}  # lower bounds
AT_LEAST = ("more than", "over", "at least", "longer than", "zyada", "ziada", "se zyada", "سے زیادہ", "کم از کم")
APPROX = ("about", "around", "almost", "nearly", "roughly", "approximately", "taqreeban", "lagbhag", "qareeb", "تقریباً", "تقریبا")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items()) if m != "may"})
SEASONS = {"spring": 3, "summer": 6, "monsoon": 7, "autumn": 9, "fall": 9, "winter": 12, "garmi": 6, "garmiyon": 6, "sardi": 12, "sardiyon": 12}
URDU_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_TOKEN = re.compile(r"[\w']+", re.UNICODE)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").translate(URDU_DIGITS).casefold().replace("-", " ")


def _number(tok: str) -> tuple[float, str] | None:
    if re.fullmatch(r"\d+(\.\d+)?", tok):
        return float(tok), "exact"
    if tok in NUMBERS:
        return float(NUMBERS[tok]), "exact"
    if tok in VAGUE:
        return float(VAGUE[tok]), "approximate"
    return None


def parse(text: str | None, session_date: date | None = None) -> dict | None:
    """The longest duration stated in ``text`` (days), or None."""
    norm = _norm(text or "")
    if not norm.strip():
        return None
    toks = _TOKEN.findall(norm)
    found: list[dict] = []
    qualifier_kind = "at_least" if any(q in norm for q in AT_LEAST) else "approximate" if any(q in norm for q in APPROX) else None
    for i, tok in enumerate(toks):
        unit = UNITS.get(tok)
        if unit is None:
            continue
        prev = toks[i - 1] if i else ""
        prev2 = toks[i - 2] if i > 1 else ""
        num = _number(prev)
        if prev in {"and", "aur"} or (prev == "a" and prev2 == "half"):  # "a year and a half", "half a year"
            num = num or (1.0, "exact")
        if num is None and prev in {"few", "couple", "several"} and prev2 in {"a", "the"}:
            num = (float(VAGUE[prev]), "approximate")
        if num is None and prev2 and _number(prev2) and prev in {"of", "se"}:  # "couple of weeks"
            num = _number(prev2)
        half = (prev == "half") or (prev == "a" and prev2 == "half") or (i + 3 < len(toks) + 1 and toks[i + 1:i + 4] == ["and", "a", "half"])
        if num is not None:
            value, kind = num
            days = value * unit
            if toks[i + 1:i + 4] == ["and", "a", "half"]:
                days += unit / 2
            elif prev == "half" or (prev == "a" and prev2 == "half"):
                days = unit / 2
        elif half:
            days, kind = unit / 2, "exact"
        elif tok.endswith(("s", "on", "o", "وں")) and unit > 1 and prev in {"for", "past", "last", "since", "from", "kai", "kayi"}:
            days, kind = 2 * unit, "at_least"  # "for months", "past weeks": at least two units
        elif prev in {"past", "last", "pichle", "pichhle", "pichla", "guzishta", "گزشتہ", "پچھلے"} or (prev == "the" and prev2 in {"past", "over"}):
            days, kind = unit, "at_least"  # "since last month", "the past year"
        else:
            continue
        found.append({"days": int(days + 0.5), "kind": qualifier_kind or kind, "text": " ".join(toks[max(0, i - 2):i + 1])})
    if session_date is not None:
        for i, tok in enumerate(toks):
            if (i and toks[i - 1] in {"since", "from", "se"} or (i + 1 < len(toks) and toks[i + 1] in {"se"})) and (tok in MONTHS or tok in SEASONS):
                month = MONTHS.get(tok) or SEASONS[tok]
                start = date(session_date.year if month <= session_date.month else session_date.year - 1, month, 1)
                days = (session_date - start).days
                if days > 0:
                    found.append({"days": days, "kind": "approximate", "text": f"since {tok}"})
    if not found:
        return None
    return max(found, key=lambda f: f["days"])
