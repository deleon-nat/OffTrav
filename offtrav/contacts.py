"""Read the contact list(s) that are part of a document set (any CSV/XLSX with an Email column).

Used by the Contacts tab. Deterministic code, no LLM: it lists offices and builds a mailto: link
with the suggested subject line filled in. OffTrav never sends e-mail itself; the link only
opens a draft in the user's own mail app.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import quote

# a basic "looks like an e-mail address" test
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _read(path: Path) -> list[dict]:
    """Read a CSV or the first sheet of an XLSX as a list of {column: value} dicts."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h).strip() if h else "" for h in rows[0]]
    return [dict(zip(head, ["" if v is None else str(v) for v in r])) for r in rows[1:] if any(r)]


def load_contacts(data_dir: str | Path) -> list[dict]:
    """Find every table with an 'Email' column in the document folder and return its rows as contacts."""
    d = Path(data_dir)
    out = []
    if not d.exists():
        return out
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".csv", ".xlsx") or "deadline" in p.name.lower():
            continue
        try:
            rows = _read(p)
        except Exception:
            continue
        if not rows or not any(k and k.strip().lower() == "email" for k in rows[0]):
            continue
        for r in rows:
            norm = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            out.append({"topic": norm.get("topic", ""), "office": norm.get("office", ""),
                        "email": norm.get("email", ""), "when": norm.get("when to contact", ""),
                        "subject": norm.get("suggested subject line", ""), "source": p.name})
    return out


def fill_subject(template: str, name: str = "", event: str = "", dates: str = "", destination: str = "") -> str:
    """Fill placeholders like <Name> or <Event> in the suggested subject line; unknown ones are left as is."""
    values = {"name": name, "event": event, "travel dates": dates, "dates": dates, "destination": destination,
              "departure date": dates, "course title": event, "fta no.": "", }
    def rep(m):
        v = values.get(m.group(1).strip().lower())
        return v if v else m.group(0)
    return re.sub(r"<([^>]+)>", rep, template)


def mailto(email: str, subject: str) -> str | None:
    """Build a mailto: link (opens a draft in the user's mail app), or None if the address looks invalid."""
    if not EMAIL_RE.match(email or ""):
        return None
    return f"mailto:{email}?subject={quote(subject)}"
