"""Compare two ways of reading a PDF and list only the numbers a person should check.

Route A  "text layer": the text already stored in the PDF (what ingestion uses today; for a scanned
         office order this is the scanner's own OCR).
Route B  "fresh OCR":  the page is rendered at 300 dpi and read again, independently, with Tesseract
         (if installed) or RapidOCR (pip-only, no admin rights needed).

Instead of asking a person to proofread every number, a number is flagged only when:
  word_digit      the document writes it twice and the two disagree, e.g. "thirty (50)"
  routes_differ   the two routes read different numbers on the same page
  low_confidence  fresh OCR is unsure of the line the number is on
  no_text_layer   the page has no usable text layer at all
  loader_differs  two PDF libraries read the stored text differently (points at our loading step)

  python scripts/compare_extraction.py data/private                      # writes data/private/_extraction_review.csv
  python scripts/compare_extraction.py scanned.pdf --truth original.pdf  # accuracy of each route (synthetic test)

What it cannot catch: a number that both routes misread the same way, a value attached to the wrong
table row, or a missing line. Those are left to the targeted checks described in the README.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

NUM_RE = re.compile(r"(?<![A-Za-z]-)(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?")
ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
        "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
WORD = r"(?:" + "|".join(sorted(list(ONES) + list(TENS), key=len, reverse=True)) + r")"
WORD_DIGIT = re.compile(rf"\b({WORD}(?:[\s-]+{WORD})?(?:\s+hundred(?:\s+(?:and\s+)?{WORD}(?:[\s-]+{WORD})?)?)?)\s*\(\s*([^)]{{1,8}})\s*\)",
                        re.IGNORECASE)
LOW_CONF = 0.85


def words_to_int(text: str) -> int | None:
    total, cur = 0, 0
    for w in re.split(r"[\s-]+", text.lower().replace(" and ", " ")):
        if w in ONES:
            cur += ONES[w]
        elif w in TENS:
            cur += TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w:
            return None
    return total + cur


def norm(n: str) -> str:
    n = n.replace(",", "")
    return n.rstrip("0").rstrip(".") if "." in n else n


MARKER = re.compile(r"(?m)^\W{0,3}\(?[a-z]?\d{1,2}[.)]\s")     # list numbering such as "12." or "(3)" at a line start


def content_numbers(text: str) -> Counter:
    """Numbers that carry meaning: list numbering at the start of a line is left out."""
    return numbers(MARKER.sub(" ", text))


def numbers(text: str) -> Counter:
    return Counter(norm(m) for m in NUM_RE.findall(text))


def context(text: str, n: str, width: int = 45) -> str:
    m = re.search(rf"(?<![\d,]){re.escape(n)}(?![\d])", text.replace(",", ""))
    flat = text.replace(",", "")
    if not m:
        return ""
    return re.sub(r"\s+", " ", flat[max(0, m.start() - width): m.end() + width]).strip()


def word_digit_mismatches(text: str) -> list[tuple[str, str, str]]:
    out = []
    for words, digits in WORD_DIGIT.findall(text):
        v = words_to_int(words)
        d = norm(re.sub(r"[^\d.,]", "", digits))
        if v is not None and d and d != str(v):
            out.append((words.strip(), digits.strip(), f"{words.strip()} ({digits.strip()})"))
    return out


# ---------------------------------------------------------------- route B: fresh OCR
def make_ocr(engine: str = "auto"):
    """Return (ocr function, engine name). Each OCR function maps a page image to [(line text, confidence)]."""
    def rapid():
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR()

        def run(img):
            import numpy as np
            res, _ = eng(np.array(img.convert("RGB")))
            lines = sorted(res or [], key=lambda r: (round(r[0][0][1] / 12), r[0][0][0]))
            return [(r[1], float(r[2])) for r in lines]
        return run, "RapidOCR"

    def tess():
        import pytesseract
        pytesseract.get_tesseract_version()

        def run(img):
            g = img.convert("L")
            d = pytesseract.image_to_data(g, config="--psm 4", output_type=pytesseract.Output.DICT)
            lines: dict = {}
            for i, w in enumerate(d["text"]):
                if w.strip():
                    key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
                    lines.setdefault(key, []).append((w, max(float(d["conf"][i]), 0) / 100))
            return [(" ".join(w for w, _ in v), min(c for _, c in v)) for v in lines.values()]
        return run, "Tesseract"

    order = {"auto": (tess, rapid), "tesseract": (tess,), "rapidocr": (rapid,)}[engine]
    for make in order:
        try:
            return make()
        except Exception:
            continue
    return None, "none (install Tesseract, or: pip install rapidocr-onnxruntime pypdfium2)"


def render_pages(path: Path, dpi: int = 300):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    for i in range(len(pdf)):
        yield pdf[i].render(scale=dpi / 72).to_pil()


def read_pdf(path: Path, ocr) -> list[dict]:
    pages = []
    layer = [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
    try:                                       # a second reader of the same text layer (is our loader at fault?)
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(path))
        layer2 = [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    except Exception:
        layer2 = [None] * len(layer)
    images = render_pages(path) if ocr else iter(())
    for i, text in enumerate(layer):
        page = {"page": i + 1, "layer": text, "layer2": layer2[i] if i < len(layer2) else None, "ocr": "", "ocr_lines": []}
        if ocr:
            try:
                lines = ocr(next(images))
                page["ocr_lines"] = lines
                page["ocr"] = "\n".join(t for t, _ in lines)
            except StopIteration:
                pass
        pages.append(page)
    return pages


def review_rows(name: str, pages: list[dict], have_ocr: bool) -> list[dict]:
    rows = []
    for p in pages:
        add = lambda check, a="", b="", ctx="": rows.append(
            {"file": name, "page": p["page"], "check": check, "text_layer": a, "fresh_ocr": b, "context": ctx})
        if len(p["layer"].strip()) < 50:
            add("no_text_layer", ctx="page has little or no stored text; use the fresh OCR or retype it")
        if p.get("layer2") is not None:
            a2, b2 = numbers(p["layer"]), numbers(p["layer2"])
            for n in sorted(((a2 - b2) + (b2 - a2)).keys()):
                add("loader_differs", n, "", "pypdf and pdfium read the stored text differently: " + context(p["layer"] or p["layer2"], n))
        for route, key in (("text_layer", "layer"), ("fresh_ocr", "ocr")):
            for words, digits, ctx in word_digit_mismatches(p[key]):
                add("word_digit", *((digits, "") if route == "text_layer" else ("", digits)), ctx=f"{route}: {ctx}")
        if have_ocr:
            a, b = content_numbers(p["layer"]), content_numbers(p["ocr"])
            for n in sorted((a - b).keys(), key=lambda x: float(x) if x.replace(".", "").isdigit() else 0):
                add("routes_differ", n, "", context(p["layer"], n))
            for n in sorted((b - a).keys(), key=lambda x: float(x) if x.replace(".", "").isdigit() else 0):
                add("routes_differ", "", n, context(p["ocr"], n))
            for text, conf in p["ocr_lines"]:
                if conf < LOW_CONF and NUM_RE.search(text):
                    add("low_confidence", "", ", ".join(norm(x) for x in NUM_RE.findall(text)), f"{text} (confidence {conf:.2f})")
    return rows


def accuracy(pages: list[dict], truth: list[str]) -> dict:
    """Number tokens found by each route against the original text (synthetic test only)."""
    out = {}
    for route, key in (("text layer", "layer"), ("fresh OCR", "ocr")):
        tp = fp = fn = 0
        for p, t in zip(pages, truth):
            got, want = numbers(p[key]), numbers(t)
            tp += sum((got & want).values()); fp += sum((got - want).values()); fn += sum((want - got).values())
        out[route] = {"correct": tp, "wrong_or_extra": fp, "missed": fn, "of": tp + fn}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a PDF or a folder of PDFs")
    ap.add_argument("--truth", help="original PDF with correct text (synthetic test only)")
    ap.add_argument("--out", help="CSV to write (default: <folder>/_extraction_review.csv)")
    ap.add_argument("--engine", default="auto", choices=["auto", "tesseract", "rapidocr"])
    args = ap.parse_args()
    target = Path(args.target)
    files = [target] if target.is_file() else sorted(target.rglob("*.pdf"))
    if not files:
        print("No PDF files found.")
        return 1
    ocr, engine = make_ocr(args.engine)
    print(f"Fresh OCR engine: {engine}")
    rows, n_numbers, n_pages = [], 0, 0
    for f in files:
        pages = read_pdf(f, ocr)
        n_pages += len(pages)
        n_numbers += sum(sum(numbers(p["layer"]).values()) for p in pages)
        rows += review_rows(f.name, pages, ocr is not None)
        if args.truth:
            truth = [(p.extract_text() or "") for p in PdfReader(args.truth).pages]
            for route, a in accuracy(pages, truth).items():
                print(f"  {route:10s}: {a['correct']}/{a['of']} numbers read correctly, "
                      f"{a['wrong_or_extra']} wrong or extra, {a['missed']} missed")
            truth_wd = sum(len(word_digit_mismatches(t)) for t in truth)
            print(f"  word/digit pairs that disagree in the original: {truth_wd}")
    out = Path(args.out) if args.out else (target if target.is_dir() else target.parent) / "_extraction_review.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "page", "check", "text_layer", "fresh_ocr", "context"])
        w.writeheader()
        w.writerows(rows)
    by = Counter(r["check"] for r in rows)
    print(f"{len(files)} file(s), {n_pages} page(s), {n_numbers} numbers in the text layer.")
    print(f"Items for a person to check: {len(rows)}  " + "  ".join(f"{k}={v}" for k, v in sorted(by.items())))
    print(f"Written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
