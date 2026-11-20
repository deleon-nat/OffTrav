"""Deterministic document loading and chunking (no LLM involved).

Supported: .pdf .docx .xlsx/.xlsm .csv .md .txt
Each file becomes a list of "units" (location + section + text). Long units are split
into overlapping chunks on paragraph/sentence boundaries. Table rows (xlsx/csv/docx tables)
are serialised as "Header: value; Header: value" so each row is retrievable on its own.

This is steps P1 (read) and P2 (chunk) of the pre-processing described in retrieve.py.
Nothing here calls a model: the same files always give the same chunks.

Document types in the index (see data/sample_synthetic for safe examples):
  .pdf   office orders and memoranda (the rules)
  .docx  booking guide (headings + numbered steps + tables)
  .xlsx  allowance rate tables, checklists, forms laid out on a sheet
  .csv   contact list (office, e-mail, what it handles)
  .md    FAQ / forms index, and one unofficial tips page with a planted prompt injection
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# file types we can read; anything else is skipped with a warning in the manifest
SUPPORTED = {".pdf", ".docx", ".xlsx", ".xlsm", ".csv", ".md", ".txt"}
SCANNED_PAGE_MIN_CHARS = 40   # pages with less extractable text are probably scanned images


@dataclass
class Unit:
    """A natural piece of a document before chunking: a PDF page, a Word section, a table row."""
    location: str      # where it is, shown to the user: "p.3", "sheet 'Rates' row 5", "section: Steps"
    section: str       # heading it falls under (used to keep neighbouring chunks together)
    text: str


@dataclass
class Chunk:
    """One searchable passage. This is what retrieval ranks and what the model sees as [S#]."""
    id: str            # short hash, stable across runs for the same text
    source: str        # file name relative to the data folder
    location: str
    section: str
    text: str

    def header(self) -> str:
        """'file | location | section': added to the text for searching, so file names help too."""
        parts = [self.source, self.location]
        if self.section:
            parts.append(self.section)
        return " | ".join(p for p in parts if p)

    def to_dict(self) -> dict:
        """Plain dict, for chunks.jsonl and for passing hits around."""
        return asdict(self)


@dataclass
class IngestReport:
    """What was loaded (file, hash, counts) and every problem found, saved in manifest.json."""
    files: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    """Normalise spaces and blank lines left over from PDF and Word extraction."""
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _row_text(headers: list[str], values: list) -> str:
    """Turn a table row into 'Header: value; Header: value' so it still makes sense on its own.

    Example: "City: Brussels; Hotel ceiling (USD): 300; Region: Europe".
    """
    parts = []
    for h, v in zip(headers, values):
        if v is None or str(v).strip() == "":
            continue
        h = (h or "").strip()
        parts.append(f"{h}: {v}" if h else str(v))
    return "; ".join(parts)


# ---------------------------------------------------------------------- loaders
def load_pdf(path: Path, report: IngestReport) -> list[Unit]:
    """One unit per page, from the PDF's text layer. Pages with almost no text are flagged as scans."""
    from pypdf import PdfReader
    units = []
    reader = PdfReader(str(path))
    for i, page in enumerate(reader.pages, start=1):
        text = _clean(page.extract_text() or "")
        if len(text) < SCANNED_PAGE_MIN_CHARS:
            # a scanned page has no text to index; see scripts/ocr_to_docx.py and compare_extraction.py
            report.warnings.append(
                f"{path.name} p.{i}: little or no text layer (scanned?). Convert to clean text before relying on it.")
            if not text:
                continue
        units.append(Unit(location=f"p.{i}", section="", text=text))
    return units


def load_docx(path: Path, report: IngestReport) -> list[Unit]:
    """Walk the Word file in reading order: headings start a new section, tables become rows."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    units: list[Unit] = []
    section = ""
    buf: list[str] = []
    n_tables = 0

    def flush():
        """Save the paragraphs collected under the current heading as one unit."""
        if buf:
            units.append(Unit(location=f"section: {section}" if section else "body", section=section,
                              text=_clean("\n".join(buf))))
            buf.clear()

    for child in doc.element.body.iterchildren():      # paragraphs and tables, in document order
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            txt = p.text.strip()
            if not txt:
                continue
            style = (p.style.name or "") if p.style is not None else ""
            if style.startswith("Heading") or style == "Title":
                flush()
                section = txt
            else:
                prefix = "- " if "List" in style else ""
                buf.append(prefix + txt)
        elif tag == "tbl":
            flush()
            n_tables += 1
            table = Table(child, doc)
            rows = [[c.text.strip() for c in r.cells] for r in table.rows]
            if not rows:
                continue
            headers = rows[0]
            for j, r in enumerate(rows[1:], start=2):
                t = _row_text(headers, r)
                if t:
                    units.append(Unit(location=f"table {n_tables} row {j}", section=section, text=t))
    flush()
    return units


def _looks_like_column_name(h: str) -> bool:
    """Short, non-numeric text looks like a column heading; a long sentence does not."""
    h = h.strip()
    return len(h) <= 40 and not re.fullmatch(r"[\d.,\s]+", h)


def load_xlsx(path: Path, report: IngestReport) -> list[Unit]:
    """Each sheet: a real table becomes one unit per row; a form or checklist becomes running text."""
    from openpyxl import load_workbook
    wb = load_workbook(str(path), data_only=True, read_only=True)   # data_only: read values, not formulas
    units = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(v is not None and str(v).strip() for v in r)]   # drop empty rows
        if not rows:
            continue
        # header = first row with >= 2 non-empty cells; single-column sheets are treated as text rows
        header_idx = next((i for i, r in enumerate(rows)
                           if sum(1 for v in r if v not in (None, "")) >= 2), None)
        if header_idx is None:
            for i, r in enumerate(rows, start=1):
                txt = " ".join(str(v) for v in r if v not in (None, ""))
                if i == 1 and len(rows) > 1 and len(txt) < 40:
                    continue  # a short single-cell first row is a column title
                units.append(Unit(location=f"sheet '{ws.title}' row {i}", section=ws.title, text=txt))
            continue
        headers = [str(h) if h is not None else "" for h in rows[header_idx]]
        if not all(_looks_like_column_name(h) for h in headers if h.strip()):
            # a form or checklist laid out on a sheet (no column headings): keep it as running text,
            # one line per row, so numbered items and their continuation lines stay together
            text = "\n".join(" ".join(str(v).strip() for v in r if v not in (None, "") and str(v).strip())
                              for r in rows)
            units.append(Unit(location=f"sheet '{ws.title}'", section=ws.title, text=text))
            continue
        for i, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
            t = _row_text(headers, r)
            if t:
                units.append(Unit(location=f"sheet '{ws.title}' row {i}", section=ws.title, text=t))
    return units


def load_csv(path: Path, report: IngestReport) -> list[Unit]:
    """One unit per row; the first row gives the column headings (e.g. the contact list)."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    headers = rows[0]
    return [Unit(location=f"row {i}", section="", text=_row_text(headers, r))
            for i, r in enumerate(rows[1:], start=2) if any(c.strip() for c in r)]


def load_markdown(path: Path, report: IngestReport) -> list[Unit]:
    """One unit per heading section; nested headings are kept as 'Parent > Child'."""
    text = path.read_text(encoding="utf-8", errors="replace")
    units, heads, buf = [], [], []

    def flush():
        """Save the lines under the current heading as one unit."""
        body = _clean("\n".join(buf))
        if body:
            sec = " > ".join(heads)
            units.append(Unit(location=f"section: {heads[-1]}" if heads else "body", section=sec, text=body))
        buf.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            level = len(m.group(1))
            heads[:] = heads[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return units


# file extension -> loader function
LOADERS = {".pdf": load_pdf, ".docx": load_docx, ".xlsx": load_xlsx, ".xlsm": load_xlsx,
           ".csv": load_csv, ".md": load_markdown, ".txt": load_markdown}


# ---------------------------------------------------------------------- chunking
def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split long text into chunks of at most max_chars, cut at sentence or line breaks.

    Each new chunk starts with the last `overlap` characters of the previous one (900 and 150
    in config.yaml), so a rule that straddles a boundary still appears whole in one chunk.
    """
    if len(text) <= max_chars:
        return [text]
    # split into sentences / paragraphs, then pack greedily with overlap
    pieces = re.split(r"(?<=[.;:!?])\s+|\n+", text)
    pieces = [p.strip() for p in pieces if p.strip()]
    chunks, cur = [], ""
    for p in pieces:                                   # LOOP: pack sentences into chunks greedily
        while len(p) > max_chars:                      # very long sentence: hard split
            head, p = p[:max_chars], p[max_chars - overlap:]
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if len(cur) + len(p) + 1 <= max_chars:
            cur = f"{cur} {p}".strip()
        else:
            if cur:
                chunks.append(cur)
            tail = cur[-overlap:] if overlap and cur else ""
            # start the overlap at a word boundary
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            cur = f"{tail} {p}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def chunk_id(source: str, location: str, i: int, text: str) -> str:
    """Short stable id from file, location, position and text."""
    return hashlib.sha1(f"{source}|{location}|{i}|{text}".encode()).hexdigest()[:12]


def file_sha256(path: Path) -> str:
    """Fingerprint of a file, stored in the manifest so we know exactly which version was indexed."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def load_corpus(data_dir: str | Path, chunk_chars: int = 900, overlap: int = 150
                ) -> tuple[list[Chunk], IngestReport]:
    """P1 + P2: read every supported file under data_dir and return all chunks plus a report."""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        raise FileNotFoundError(f"Data folder not found: {data_dir}")
    report = IngestReport()
    chunks: list[Chunk] = []
    for path in sorted(p for p in data_dir.rglob("*") if p.is_file()):   # LOOP over files, sorted = same order every run
        ext = path.suffix.lower()
        rel_parts = path.relative_to(data_dir).parts
        if path.name.startswith(("~$", ".")) or any(part.startswith("_") for part in rel_parts):
            continue   # temp files, and helper files/folders such as _planner/ or _pdf_audit.csv
        if ext not in SUPPORTED:
            if ext == ".doc":
                report.warnings.append(f"{path.name}: old .doc format is not supported; save it as .docx.")
            else:
                report.warnings.append(f"{path.name}: unsupported file type, skipped.")
            continue
        try:
            units = LOADERS[ext](path, report)
        except Exception as e:  # keep going; report the failure
            report.warnings.append(f"{path.name}: failed to load ({type(e).__name__}: {e})")
            continue
        rel = str(path.relative_to(data_dir)).replace("\\", "/")
        n = 0
        for u in units:                                 # each unit may become several chunks
            for i, piece in enumerate(split_text(u.text, chunk_chars, overlap)):
                chunks.append(Chunk(id=chunk_id(rel, u.location, i, piece), source=rel,
                                    location=u.location, section=u.section, text=piece))
                n += 1
        report.files.append({"file": rel, "sha256": file_sha256(path), "units": len(units), "chunks": n})
        if n == 0:
            report.warnings.append(f"{rel}: no text extracted.")
    return chunks, report
