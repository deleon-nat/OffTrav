"""Deterministic safeguards around the LLM (no model calls here).

- quarantine_chunks: drop retrieved text that looks like instructions to an AI (indirect prompt injection)
- detect_question_injection: flag user attempts to override the rules (logged, not blocked)
- check_citations: validate [S#] tags against the sources actually supplied
- ungrounded_numbers: numbers in the answer that appear in neither the sources nor the question
- is_refusal: detect the standard "not found" answer
"""
from __future__ import annotations

import re

INJECTION_PATTERNS = [
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)",
    r"disregard (?:the |your |all )?(?:previous |prior )?(?:instructions|rules)",
    r"(?:system|developer) (?:note|prompt|message|instruction)s? (?:for|to) (?:ai|assistants?|chatbots?|llms?)",
    r"\byou are now\b",
    r"new instructions\s*:",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

REFUSAL_PATTERNS = [
    r"couldn[’']?t find", r"could not find", r"can(?:no|')t find",
    r"not (?:found|covered|stated|included|specified|mentioned|available|provided) in",
    r"no (?:information|guidance|details|mention)",
    r"(?:do not|don[’']?t) have (?:any )?(?:information|details|data)",
    r"outside (?:of )?(?:my|the|this) scope", r"out of scope",
    r"(?:sources|documents|guidelines) do not (?:say|state|mention|cover|specify|contain|include|provide)",
    r"i (?:do not|don[’']?t) know",
]
_REF = [re.compile(p, re.IGNORECASE) for p in REFUSAL_PATTERNS]

CITE_RE = re.compile(r"\[S(\d+)\]")
NUM_RE = re.compile(r"(?<![A-Za-z]-)(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?")   # skips form codes like LD-07

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty-five": 45, "sixty": 60, "ninety": 90,
}


def suspicious(text: str) -> bool:
    return any(p.search(text) for p in _INJ)


def quarantine_chunks(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for h in hits:
        (dropped if suspicious(h["text"]) else kept).append(h)
    return kept, dropped


def detect_question_injection(question: str) -> bool:
    return suspicious(question)


def is_refusal(answer: str) -> bool:
    return any(p.search(answer) for p in _REF)


def check_citations(answer: str, n_sources: int) -> dict:
    ids = [int(x) for x in CITE_RE.findall(answer)]
    valid = sorted({i for i in ids if 1 <= i <= n_sources})
    invalid = sorted({i for i in ids if not 1 <= i <= n_sources})
    return {"cited": valid, "invalid": invalid, "has_citation": bool(valid)}


def _norm_num(s: str) -> str:
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def numbers_in(text: str) -> set[str]:
    text = CITE_RE.sub(" ", text)
    nums = {_norm_num(m) for m in NUM_RE.findall(text)}
    low = text.lower()
    for w in sorted(NUMBER_WORDS, key=len, reverse=True):      # "forty-five" before "forty" and "five"
        pat = rf"(?<![\w-]){re.escape(w)}(?![\w-])"
        if re.search(pat, low):
            nums.add(str(NUMBER_WORDS[w]))
            low = re.sub(pat, " ", low)
    return {n for n in nums if n}


def ungrounded_numbers(answer: str, context: str, question: str = "") -> list[str]:
    """Numbers in the answer that are not present in the sources or the question.

    Results of arithmetic (e.g. 270 x 4 = 1080) are reported too; the UI labels them
    'not found verbatim in the sources - please verify', which is the intended behaviour.
    """
    allowed = numbers_in(context) | numbers_in(question)
    found = numbers_in(CITE_RE.sub(" ", answer))
    # ignore list numbering like "1." "2." at the start of lines
    list_markers = {m for m in re.findall(r"(?m)^\s*(\d{1,2})[.)]\s", answer)}
    return sorted((found - allowed) - list_markers, key=lambda x: float(x))


# ---------------------------------------------------------------- claim-level checks
# A "claim" is one sentence or list item of the answer. For every number in a claim, code checks
# where the number comes from:
#   supported      the number is in a passage the claim cites (and day units agree)
#   from_question  the employee gave the number (e.g. "4-day trip")
#   calculated     not in any source, but equals a product or sum of numbers that are (e.g. 50 x 4 = 200)
#   unit_mismatch  cited passage has the number with another day unit (working vs calendar days)
#   not_in_cited   the number is in another retrieved passage, not in the one cited
#   uncited        the claim has a number but no citation
#   not_found      the number is in no source and is not an explained calculation
# What code does NOT check: whether the rule applies to the employee's category, destination or dates.
# That is left to the prompt rules, the test cases, and the employee confirming with the office.
CLAIM_OK = {"supported", "from_question", "calculated"}
_DAY_UNIT = re.compile(r"(\d[\d,]*)\)?\s*(calendar|working)\s+days?", re.IGNORECASE)
_WORD_DAY_UNIT = re.compile(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\s+(calendar|working)\s+days?", re.IGNORECASE)


def _day_units(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for n, u in _DAY_UNIT.findall(text):
        out.setdefault(_norm_num(n), set()).add(u.lower())
    for w, u in _WORD_DAY_UNIT.findall(text):
        out.setdefault(str(NUMBER_WORDS[w.lower()]), set()).add(u.lower())
    return out


def _claims(answer: str) -> list[str]:
    parts = []
    for line in answer.splitlines():
        line = line.strip()
        if not line:
            continue
        pieces = [p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", line) if p.strip()]
        merged: list[str] = []
        for p in pieces:                       # keep list numbering ("1.", "(2)") with the text after it
            if merged and re.fullmatch(r"[-*]?\s*\(?\d{1,2}[.)]", merged[-1].strip()):
                merged[-1] = f"{merged[-1]} {p}"
            else:
                merged.append(p)
        parts += merged
    return parts


def _explain_calc(target: str, inputs: set[str]) -> str:
    try:
        t = float(target)
        vals = sorted({float(x) for x in inputs if x and float(x) != t})[:15]
    except ValueError:
        return ""
    for i, a in enumerate(vals):
        for b in vals[i:]:
            if abs(a * b - t) < 1e-9 and a != 1 and b != 1:
                return f"{a:g} x {b:g}"
            if abs(a + b - t) < 1e-9:
                return f"{a:g} + {b:g}"
    return ""


def check_claims(answer: str, hits: list[dict], question: str = "", extra: str = "") -> list[dict]:
    """Check every number in every claim against the passages the claim cites."""
    def ctx(h):
        return " ".join((h["text"], h.get("section", ""), h["source"], h["location"]))
    per_hit = [numbers_in(ctx(h)) for h in hits]
    any_src = set().union(*per_hit) if per_hit else set()
    asked = numbers_in(question) | numbers_in(extra)
    grounded_in_answer = (numbers_in(CITE_RE.sub(" ", answer)) & (any_src | asked))
    results = []
    claims = _claims(answer)
    for k, claim in enumerate(claims):
        body = re.sub(r"^\s*(?:[-*]|\d{1,2}[.)])\s+", "", claim)          # drop list markers
        nums = numbers_in(CITE_RE.sub(" ", body))
        if not nums:
            continue
        cites = sorted({int(x) for x in CITE_RE.findall(claim) if 1 <= int(x) <= len(hits)})
        if not cites and claim.rstrip().endswith(":") and k + 1 < len(claims):
            # a lead-in line ("... within 45 calendar days:") is covered by the citations that follow it
            cites = sorted({int(x) for x in CITE_RE.findall(claims[k + 1]) if 1 <= int(x) <= len(hits)})
        cited_txt = " ".join(ctx(hits[i - 1]) for i in cites)
        cited_nums = set().union(*(per_hit[i - 1] for i in cites)) if cites else set()
        units = _day_units(body)
        src_units = _day_units(cited_txt)
        checks = []
        for n in sorted(nums, key=float):
            status, detail = "", ""
            if n in cited_nums:
                status = "supported"
                if n in units and n in src_units and not (units[n] & src_units[n]):
                    status = "unit_mismatch"
                    detail = (f"the answer says {n} {'/'.join(sorted(units[n]))} days, the cited source says "
                              f"{'/'.join(sorted(src_units[n]))} days")
            elif n in asked:
                status = "from_question"
            elif n in any_src:
                status = "not_in_cited" if cites else "uncited"
            else:
                calc = _explain_calc(n, grounded_in_answer | (numbers_in(body) & (any_src | asked)))
                status, detail = ("calculated", f"{n} = {calc}") if calc else ("not_found", "")
            checks.append({"number": n, "status": status, "detail": detail})
        results.append({"claim": claim, "cites": [f"S{i}" for i in cites], "checks": checks})
    return results


def claim_warnings(results: list[dict]) -> list[str]:
    msgs = []
    for r in results:
        where = ", ".join(f"[{c}]" for c in r["cites"]) or "no source"
        for c in r["checks"]:
            n, st = c["number"], c["status"]
            if st == "unit_mismatch":
                msgs.append(f"Check {n}: {c['detail']} ({where}).")
            elif st == "not_in_cited":
                msgs.append(f"Check {n}: it is not in the cited source {where}, only in another retrieved passage.")
            elif st == "uncited":
                msgs.append(f"Check {n}: it has no citation.")
            elif st == "not_found":
                msgs.append(f"Check {n}: it is not in any source and is not an explained calculation.")
    return msgs
