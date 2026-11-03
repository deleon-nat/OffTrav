"""Read the contact list(s) that are part of a document set (any CSV/XLSX with an Email column)."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import quote

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _read(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h).strip() if h else "" for h in rows[0]]
    return [dict(zip(head, ["" if v is None else str(v) for v in r])) for r in rows[1:] if any(r)]


def load_contacts(data_dir: str | Path) -> list[dict]:
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
    values = {"name": name, "event": event, "travel dates": dates, "dates": dates, "destination": destination,
              "departure date": dates, "course title": event, "fta no.": "", }
    def rep(m):
        v = values.get(m.group(1).strip().lower())
        return v if v else m.group(0)
    return re.sub(r"<([^>]+)>", rep, template)


def mailto(email: str, subject: str) -> str | None:
    if not EMAIL_RE.match(email or ""):
        return None
    return f"mailto:{email}?subject={quote(subject)}"
