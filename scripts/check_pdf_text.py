"""Audit the text layer of PDFs before indexing them.

Scanned PDFs often have OCR errors in exactly the places that matter (e.g. "30 calendar days"
read as "50"). This script lists every page with little or no text and every number with its
surrounding words, so a person can proofread the figures against the printed page.

  python scripts/check_pdf_text.py data/private            # writes data/private/_pdf_audit.csv
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from pypdf import PdfReader

NUM = re.compile(r"(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?")
ODD = re.compile(r"[^\x00-\x7F\u2013\u2014\u2018\u2019\u201c\u201d\u00b0]")  # unusual characters = OCR noise


def main() -> int:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "data/private")
    out = folder / "_pdf_audit.csv"
    rows = []
    for pdf in sorted(folder.rglob("*.pdf")):
        reader = PdfReader(str(pdf))
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            flat = re.sub(r"\s+", " ", text)
            if len(flat.strip()) < 40:
                rows.append([pdf.name, i, "", "NO/LOW TEXT - scanned page, needs OCR or retyping", ""])
                continue
            odd = len(ODD.findall(flat)) / max(len(flat), 1)
            if odd > 0.02:
                rows.append([pdf.name, i, "", f"possible OCR noise ({odd:.1%} unusual characters)", ""])
            for m in NUM.finditer(flat):
                ctx = flat[max(0, m.start() - 60): m.end() + 60]
                rows.append([pdf.name, i, m.group(), "check number", ctx])
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "page", "number", "issue", "context"])
        w.writerows(rows)
    print(f"{len(rows)} items written to {out}. Proofread the numbers against the printed pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
