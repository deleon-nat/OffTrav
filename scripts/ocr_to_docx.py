"""Turn a scanned PDF that has no usable text into a Word file OffTrav can index.

Each page is rendered at 300 dpi and read with OCR (Tesseract if installed, otherwise RapidOCR).
Lines that the OCR was unsure about AND that contain a number are highlighted in yellow, so a person
proofreads only those lines against the printed page, then removes the highlight.

  python scripts/ocr_to_docx.py "data/private/Guidelines on FT Allowances.pdf"
  # writes data/private/Guidelines on FT Allowances (OCR).docx
  # then move the PDF to data/private/_originals/ so it is not indexed twice

Tables are read line by line and often come out scrambled; retype important tables as a small
spreadsheet in the same folder.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_extraction import LOW_CONF, NUM_RE, make_ocr, render_pages  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", help="Word file to write (default: '<pdf name> (OCR).docx' next to the PDF)")
    ap.add_argument("--engine", default="auto", choices=["auto", "tesseract", "rapidocr"])
    args = ap.parse_args()
    from docx import Document
    from docx.enum.text import WD_COLOR_INDEX

    pdf = Path(args.pdf)
    ocr, engine = make_ocr(args.engine)
    if ocr is None:
        print(f"No OCR engine available: {engine}")
        return 1
    out = Path(args.out) if args.out else pdf.with_name(f"{pdf.stem} (OCR).docx")
    doc = Document()
    doc.add_heading(pdf.stem, level=1)
    doc.add_paragraph(f"Text read by OCR ({engine}) from the scanned file {pdf.name}. "
                      "Highlighted lines contain numbers the OCR was unsure about; check them against the printed page.")
    flagged = total = 0
    for i, img in enumerate(render_pages(pdf), 1):
        doc.add_heading(f"Page {i}", level=2)
        for text, conf in ocr(img):
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            total += 1
            run = doc.add_paragraph().add_run(text)
            if conf < LOW_CONF and NUM_RE.search(text):
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
                flagged += 1
    doc.save(out)
    print(f"{engine}: {total} lines, {flagged} highlighted for checking. Written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
