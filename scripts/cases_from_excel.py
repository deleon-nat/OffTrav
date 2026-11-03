"""Turn a quiz written in Excel into the cases file used by eval/run_eval.py.

  python scripts/cases_from_excel.py eval/cases_template.xlsx eval/cases_private.jsonl

Columns (sheet "Cases", one question per row):
  ID                 short unique code, e.g. P01
  Split              dev (used while improving prompts) or test (final scoring only)
  Category           free text, e.g. fact, table, contact, procedure, form, unanswerable
  Question           the question exactly as an employee would ask it
  Must include       one line per required fact; on a line, alternatives are separated by " | "
                     e.g.  3 | three        (the answer must contain 3 or three)
  Must not include   one line per text that must NOT appear (e.g. a wrong deadline)
  Should refuse      no  = must answer;  yes = must say it is not in the documents;
                     either = a refusal or a correct answer both count
  Source documents   one line per file-name part that contains the answer, e.g. 0957

Text is matched without regard to upper/lower case. Plain numbers only match whole numbers
(30 does not match 300). To write a regular expression instead, wrap it in slashes: /30 (calendar )?days/
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

COLUMNS = ["ID", "Split", "Category", "Question", "Must include", "Must not include",
           "Should refuse", "Source documents"]


def to_pattern(text: str) -> str:
    text = text.strip()
    if len(text) > 2 and text.startswith("/") and text.endswith("/"):
        return text[1:-1]                              # user-written regular expression
    pat = re.escape(text)
    if re.fullmatch(r"[\d,.]+", text):                 # whole-number match for plain numbers
        pat = rf"(?<![\w,.]){pat}(?![\d])"
    return pat


def lines(cell) -> list[str]:
    if cell is None:
        return []
    return [l.strip() for l in str(cell).splitlines() if l.strip()]


def convert(xlsx: str, out: str) -> int:
    ws = load_workbook(xlsx, data_only=True)["Cases"]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else "" for h in rows[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise SystemExit(f"Missing columns in sheet 'Cases': {missing}")
    col = {c: header.index(c) for c in COLUMNS}
    cases, seen, problems = [], set(), []
    for n, r in enumerate(rows[1:], start=2):
        if not r or not r[col["Question"]]:
            continue
        cid = str(r[col["ID"]] or f"R{n}").strip()
        if cid in seen:
            problems.append(f"row {n}: duplicate ID {cid}")
        seen.add(cid)
        split = str(r[col["Split"]] or "test").strip().lower()
        if split not in ("dev", "test"):
            problems.append(f"row {n}: Split must be dev or test, not {split!r}")
        refuse_txt = str(r[col["Should refuse"]] or "no").strip().lower()
        refuse = {"no": False, "yes": True, "either": "allowed"}.get(refuse_txt)
        if refuse is None:
            problems.append(f"row {n}: Should refuse must be no, yes or either, not {refuse_txt!r}")
            refuse = False
        must = [[to_pattern(a) for a in l.split("|") if a.strip()] for l in lines(r[col["Must include"]])]
        if refuse is False and not must:
            problems.append(f"row {n} ({cid}): an answerable question needs at least one 'Must include' line")
        cases.append({
            "id": cid, "split": split, "category": str(r[col["Category"]] or "fact").strip(),
            "question": str(r[col["Question"]]).strip(),
            "must_include": must,
            "must_not_include": [to_pattern(l) for l in lines(r[col["Must not include"]])],
            "expect_refusal": refuse,
            "expected_sources": lines(r[col["Source documents"]]),
        })
    if problems:
        print("Fix these rows in the Excel file:")
        for p in problems:
            print("  " + p)
        return 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    n_dev = sum(c["split"] == "dev" for c in cases)
    print(f"Wrote {len(cases)} cases ({n_dev} dev, {len(cases) - n_dev} test) to {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    sys.exit(convert(sys.argv[1], sys.argv[2]))
