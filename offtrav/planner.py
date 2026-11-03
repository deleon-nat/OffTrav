"""Trip deadline planner (deterministic; no language model).

Deadline rules are not hard-coded. They are read from a table kept with the documents
(any CSV or XLSX whose file name contains "Deadlines"), so admin can update them and every
rule carries its source. Columns:

  Step          what to do, e.g. "Submit the nomination to LDD"
  Applies to    training | non-training | both
  From          the date the rule counts from: registration deadline | event start | event end |
                departure | return | FTA approval
  Days          a whole number
  Day type      working | calendar
  Direction     before | after | within (within = on or before the date, counting backwards,
                e.g. a medical clearance taken within 14 days before departure)
  Office        who receives it or does it
  Condition     optional note, e.g. "Schengen destinations only"
  Source        document and section the rule comes from

Working days skip Saturdays and Sundays. Holidays are not known to the planner, so it warns about them.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ANCHORS = {
    "registration deadline": "registration_deadline",
    "event start": "event_start",
    "event end": "event_end",
    "departure": "departure",
    "return": "return_date",
    "fta approval": "fta_approval",
}
ANCHOR_LABELS = {v: k.capitalize().replace("Fta", "FTA") for k, v in ANCHORS.items()}
ANCHOR_TEXT = {v: k.replace("fta", "FTA") for k, v in ANCHORS.items()}       # for use inside a sentence


@dataclass
class Rule:
    step: str
    applies_to: str
    anchor: str
    days: int
    day_type: str
    direction: str
    office: str
    condition: str
    source: str


def find_rules_file(data_dir: str | Path) -> Path | None:
    d = Path(data_dir)
    if not d.exists():
        return None
    for p in sorted(d.rglob("*")):
        if p.is_file() and "deadline" in p.name.lower() and p.suffix.lower() in (".csv", ".xlsx"):
            return p
    return None


def _rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = [r for r in ws.iter_rows(values_only=True)]
    head = [str(h).strip() if h else "" for h in rows[0]]
    return [dict(zip(head, ["" if v is None else v for v in r])) for r in rows[1:] if any(r)]


def load_rules(path: str | Path) -> tuple[list[Rule], list[str]]:
    rules, problems = [], []
    for n, r in enumerate(_rows(Path(path)), start=2):
        g = lambda k: str(r.get(k, "") or "").strip()
        anchor = ANCHORS.get(g("From").lower())
        if not g("Step"):
            continue
        try:
            days = int(float(g("Days")))
        except ValueError:
            problems.append(f"row {n}: Days must be a number")
            continue
        if anchor is None:
            problems.append(f"row {n}: unknown From value {g('From')!r}")
            continue
        rules.append(Rule(g("Step"), (g("Applies to") or "both").lower(), anchor, days,
                          (g("Day type") or "calendar").lower(), (g("Direction") or "before").lower(),
                          g("Office"), g("Condition"), g("Source")))
    return rules, problems


def shift(d: date, days: int, working: bool, forward: bool) -> date:
    step = timedelta(days=1 if forward else -1)
    if not working:
        return d + step * days
    moved = 0
    while moved < days:
        d += step
        if d.weekday() < 5:
            moved += 1
    return d


def plan(rules: list[Rule], event_type: str, dates: dict, today: date | None = None) -> list[dict]:
    """Return one item per applicable rule with a due date (or a note if its start date is missing)."""
    today = today or date.today()
    items = []
    for r in rules:
        if r.applies_to not in ("both", event_type):
            continue
        anchor_date = dates.get(r.anchor)
        item = {"step": r.step, "office": r.office, "condition": r.condition, "source": r.source,
                "anchor": r.anchor,
                "rule": (f"within {r.days} {r.day_type} days before {ANCHOR_TEXT[r.anchor]}"
                         if r.direction == "within" else
                         f"{r.days} {r.day_type} days {r.direction} {ANCHOR_TEXT[r.anchor]}"),
                "due": None, "window_start": None, "status": "needs date", "weekend": False}
        if anchor_date:
            working = r.day_type.startswith("work")
            if r.direction == "after":
                item["due"] = shift(anchor_date, r.days, working, forward=True)
            elif r.direction == "within":
                item["window_start"] = shift(anchor_date, r.days, working, forward=False)
                item["due"] = anchor_date
            else:
                item["due"] = shift(anchor_date, r.days, working, forward=False)
            item["weekend"] = item["due"].weekday() >= 5 and r.direction != "within"
            if item["due"] < today:
                item["status"] = "overdue"
            elif (item["due"] - today).days <= 7:
                item["status"] = "due this week"
            else:
                item["status"] = "upcoming"
        items.append(item)
    items.sort(key=lambda i: (i["due"] is None, i["due"] or date.max))
    return items


def to_ics(items: list[dict], trip_name: str) -> str:
    """Calendar file with one all-day event per dated step (imports into Outlook)."""
    stamp = date.today().strftime("%Y%m%d")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OffTrav//Trip deadlines//EN", "CALSCALE:GREGORIAN"]
    for n, i in enumerate(x for x in items if x["due"]):
        start = i["window_start"] or i["due"]
        end = i["due"] + timedelta(days=1)
        desc = f"{i['rule']}. Office: {i['office']}. Source: {i['source']}."
        if i["condition"]:
            desc += f" Note: {i['condition']}."
        out += ["BEGIN:VEVENT", f"UID:offtrav-{stamp}-{n}@localhost", f"DTSTAMP:{stamp}T000000Z",
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
                f"SUMMARY:{_ics_text(trip_name + ': ' + i['step'])}", f"DESCRIPTION:{_ics_text(desc)}",
                "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
