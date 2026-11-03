# OffTrav: local RAG assistant for official foreign travel

OffTrav answers employees' questions about official foreign travel (what to prepare, which forms,
whom to email, which steps, by when) **only from the organisation's own travel documents**, cites
the passage it used, and says so when the documents do not cover a question. Everything runs locally
on **Ollama**, so no document, prompt or answer leaves the laptop.

Stat 280 capstone. Advanced component: **retrieval-augmented generation (RAG)**.

```
offtrav/            core package (ingest, retrieve, answer, safeguards, scoring, CLI)
app.py              Streamlit chat UI
config.yaml         model and retrieval settings (recorded in every evaluation run)
data/sample_synthetic/   shareable FICTIONAL corpus ("Biringan Central Bank"): PDF, DOCX, XLSX, CSV, MD
data/private/       real documents go here; git-ignored, never submitted
eval/               test cases, evaluation runner, re-scoring
scripts/            corpus/case generators, PDF text audit
tests/              automated tests + a stand-in Ollama server for CI-style checks
docs/               architecture diagram (PowerPoint, PNG) and its build script
```

## 1. Requirements

| Item | Used for | Notes |
|---|---|---|
| Ollama ≥ 0.9 | local model server | https://ollama.com (Windows, macOS, Linux) |
| `qwen3:30b-instruct` (~19 GB) | answer generation | Mixture-of-experts (3B active parameters), non-thinking version; runs on CPU with 32 GB RAM. Do not use plain `qwen3:30b`: it is the thinking version and spends its output on reasoning. |
| `nomic-embed-text` (~0.3 GB) | embeddings for retrieval | |
| Python 3.10+ | everything else | |
| Optional: `llama3.1:8b` | faster fallback | Lower quality; record it if you switch |

Do **not** use `qwen3:30b-a3b-fp16` (~61 GB): it will not fit in 32 GB RAM.
On CPU-only laptops expect answers to take from tens of seconds to a few minutes; the evaluation records the actual latency.

## 2. Install (Windows PowerShell; macOS/Linux commands are the same except the venv activation)

```powershell
ollama pull qwen3:30b-instruct
ollama pull nomic-embed-text

cd offtrav
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m offtrav check             # confirms Ollama is running and both models are pulled
```

## 3. Build the index and chat

```powershell
# shareable synthetic corpus
python -m offtrav ingest --data data/sample_synthetic --index index/synthetic --org "Biringan Central Bank (synthetic)"

python -m offtrav ask "How many airfare quotations do I need?" --index index/synthetic --show-sources
python -m offtrav ask "How many airfare quotations do I need?" --index index/synthetic --show-prompt   # print the exact model input
python -m offtrav chat --index index/synthetic
streamlit run app.py                # browser UI at http://localhost:8501 (local only; see .streamlit/config.toml)

# for a class demo or a recording: only the synthetic documents can be selected
$env:OFFTRAV_DEMO="1"; streamlit run app.py
```

**What is sent to the model.** For each question the code builds two or more chat messages:
(1) a *system* message with the fixed rules (`offtrav/prompts.py`) and today's date; (2) up to two earlier
question-answer pairs, as separate messages, when chatting; (3) a *user* message with the numbered passages
`[S1]`, `[S2]` ... each labelled with file and page or row, followed by the employee's question. `--show-prompt`
prints this exactly, plus which file each `[S#]` came from. If no passage matches the question at all, the model is
not called and OffTrav answers that it could not find the information and whom to ask.

**Where the index lives.** `index/<name>/chunks.jsonl` (one passage per line, with file, page/row and text),
`embeddings.npy` (NumPy matrix from `nomic-embed-text`) and `manifest.json` (file hashes and settings). Keyword search
(BM25) is computed in memory from `chunks.jsonl` by `offtrav/retrieve.py`; no external database is used.

The chat app has four tabs:

| Tab | What it does |
|---|---|
| Ask | Grounded chat. Answers appear as they are written, with sources and warnings. Thumbs up/down are saved to `runs/feedback.jsonl`; "Save this chat" downloads the conversation. |
| My trip | Deadline planner: enter the event type and dates, get every deadline as a date (working days skip weekends), tick steps off, download an Outlook calendar file (.ics) or a CSV list. Rules come from a deadline table in `<documents>/_planner/` (any CSV/XLSX with "Deadlines" in the name); no language model is involved. |
| Contacts | The contact list from the documents, with "Write email" buttons that open Outlook with the suggested subject filled in. |
| Documents | Which files are indexed, passages per file, and ingestion warnings. |

Files and folders whose names start with `_` (for example `_planner/` or `_pdf_audit.csv`) are not indexed for answers.

Real documents (stay on the laptop):

```powershell
python scripts/compare_extraction.py data/private    # list only the numbers a person should check
python -m offtrav ingest --data data/private --index index/private --org "BSP (DES)"
```

**Scanned PDFs.** A scanned office order carries the scanner's own OCR text, and the loader indexes that text
as it is, so a misread "30" becomes "50" in every answer. `scripts/compare_extraction.py` reads each page
three ways (the stored text with two PDF libraries, and a fresh OCR of the page image) and lists a number for
review only when the readings disagree, when the document's words and digits disagree ("thirty (50)"), or when the
fresh OCR is unsure. Fix the flagged values (retype the passage into a `.docx` in the same folder) before indexing.
On a degraded synthetic scan of the office order (3 pages, 62 numbers) it listed 33 items and caught all 9 misread
numbers; on the clean digital PDF it listed 7 items, all false alarms. A fresh OCR is not more accurate by itself
(it read fewer numbers correctly than the stored text on that scan), so it is used as a second opinion, not a
replacement. What it cannot catch: a number both readings get wrong the same way, a value attached to the wrong
table row, or a missing line. A scanned PDF with **no** stored text (the loader warns about it) is invisible to OffTrav: run
`python scripts/ocr_to_docx.py "<file>.pdf"`, which writes `<file> (OCR).docx` with the lines to proofread
highlighted, then move the PDF into `_originals/`. Tables rarely survive OCR; retype them as a small spreadsheet.
The older `scripts/check_pdf_text.py` still lists every number if a full proofread is wanted. Save `.doc` files as `.docx`. Optional OCR engine: `pip install rapidocr-onnxruntime pypdfium2`
(no admin rights needed) or an installed Tesseract.

## 4. Evaluate (baseline vs. system on the same cases)

```powershell
# development split (used while tuning prompts)
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split dev --label dev

# final test split: baseline (closed_book) and system (rag)
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split test --label test

# optional third condition: all documents pasted into the prompt (no retrieval; slow on CPU)
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split test --modes closed_book rag full_context --label test3

# retrieval ablation, no chat model needed (recall@1/3/6 for BM25, dense, hybrid)
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split all --retrieval-only
```

Each run writes `runs/<timestamp>_<label>/`:

| File | Content |
|---|---|
| `meta.json` | model, temperature, seed, num_ctx, top_k, prompt version, machine, file hashes |
| `results.jsonl` | full output per case and mode, including errors |
| `scored.csv` | per case × mode: automatic score, citations, retrieved files, latency, tokens |
| `case_evidence.csv` | one row per case: input, success rule, baseline output/score, system output/score, notes |
| `review.csv` | fill `human_correct` (0/1) where you disagree with the automatic score |
| `summary.md` | metrics table for the report |

After reviewing: `python eval/score.py runs/<run folder>` re-applies your overrides.

### Test cases and scoring

`eval/cases_synthetic.jsonl`: 32 cases: 6 **dev** (S01–S06), 20 **test** (T01–T20) and 6 **fixcheck** (F01–F06, see below).

| Category | Test cases | What it checks |
|---|---|---|
| fact | T01 T02 T03 T11 T14 T15 | single rule from the policy order |
| table | T04 T06 T07 T08 T09 | row lookup in the XLSX allowance schedule, including the "not listed" fallback |
| calculation | T05 | rate × days |
| contact | T10 | routing to the right office/e-mail (CSV) |
| procedure | T12 T13 | multi-step process with deadlines |
| form | T16 | correct form number and attachment |
| unanswerable / out of scope | T17 T18 | must say it is not covered instead of guessing |
| prompt injection | T19 (in the question) T20 (planted in an unofficial document) | rules hold; planted figure not repeated |

**Primary metric: answer correctness** = correct answers / all cases in the split. A case is correct when
every `must_include` group matches, no `must_not_include` pattern matches, and refusal behaviour matches
`expect_refusal`. Errors, timeouts and empty outputs count as incorrect (never dropped). The author reviews
every automatic score in `review.csv`; overrides are counted in `summary.md`.

Secondary: citation accuracy (cited file contains the ground truth; answerable cases), retrieval recall@k,
share of answers with unverified numbers, latency, output tokens.

**Test-set reuse (disclosed):** light stemming and the 3-passages-per-file cap were added after inspecting retrieval
for test case T20, before any model answers were scored.

**Changes after the test run.** The test result (17/20) is the official result and is not replaced. Three
general changes were then made for the failure patterns seen in T01, T06 and T12:

| Failure | Cause | Change |
|---|---|---|
| T01: said a 2-day course in Singapore is too short | rule lists named regions, then "the rest of the world"; the model put Asia in the wrong group | prompt rule 12: name the group the case belongs to before using its value (prompt v1.4) |
| T06: refused the hotel ceiling for Nairobi | no row for Nairobi, and the "not listed" rows were not retrieved | when a table row is retrieved, that table's catch-all rows are added (`table_default_rows`) |
| T12: next steps missed the Deed of Undertaking and insurance | the numbered steps were split across two chunks and only one was retrieved | the chunks just before and after each text hit ranked 1 to 3 are added (`expand_neighbors: 4`) |

The six **fixcheck** cases (F01–F06) are new questions on the same three patterns, written before the revised
pipeline was run. The revised pipeline is reported on dev (no regressions), fixcheck, and the test split
again, labelled *after the fixes, test cases already seen*. Set `expand_neighbors: 0` and
`table_default_rows: false` to switch the retrieval changes off.

**Baseline:** the same model and settings, prompted directly without retrieval (how an employee would use a
general chatbot). Note: the synthetic organisation is fictional, so the baseline cannot know its facts; the
comparison on real documents (`index/private`, private cases) is the more realistic one.

### Writing a quiz in Excel

For a new document set (for example the real documents), write the questions and expected answers in
`eval/cases_template.xlsx` (the "How to fill in" sheet explains each column), then convert it:

```powershell
python scripts/cases_from_excel.py eval/cases_private.xlsx eval/cases_private.jsonl
python eval/run_eval.py --index index/private --cases eval/cases_private.jsonl --split test --label private
```

Files named `eval/cases_private*` are git-ignored.

## 5. What the LLM does vs. deterministic code

| Deterministic code | LLM (Ollama) |
|---|---|
| loading PDF/DOCX/XLSX/CSV/MD, chunking, table-row serialisation | embeddings (`nomic-embed-text`) |
| BM25, cosine search, reciprocal-rank fusion, per-file cap, adding neighbouring passages and table catch-all rows | composing the answer from numbered sources (`qwen3:30b-instruct`) |
| prompt-injection quarantine, no-evidence gate, question length cap | |
| citation validation, claim-level number check, refusal detection | |
| deadline planner (dates from the deadline table) | |
| scoring and reporting | |

People: prepare and update the documents and the deadline table, fix the values flagged by
`compare_extraction.py`, write the test cases, review every evaluation score, and confirm any answer with the
DES Administrative Unit before acting on it.

### Checks after the answer

Code splits the answer into sentences and checks every number against the passage that sentence cites:

| Result | Meaning | Shown to the user |
|---|---|---|
| found in the cited source | the number is in the cited passage, and "working" or "calendar" days agree | in "How the numbers were checked" |
| from your question | the employee gave it (e.g. "a 4-day trip") | same |
| calculated | not in any source, but equals a product or sum of numbers that are (USD 50 x 4 = 200) | same |
| unit differs | the cited passage has the number with the other kind of days | warning |
| not in the cited source | the number is in another passage, not the one cited | warning |
| no citation / not in any source | | warning |

Citation tags are also checked: a tag must point to a passage that was supplied. The answer is still shown when a
check fails; the warnings say which number to check and tell the employee to confirm with the DES Administrative
Unit. **Not checked by code:** whether the rule applies to the employee's destination, rank, event type or dates
(contextual accuracy). That depends on the prompt rules (name the group before using its value; ask for missing
facts), is measured by the test cases, and stays with the employee and the office. The deadline planner's dates are
computed by code from the deadline table, so they are checked by the planner tests, not by this answer check.

## 6. Safeguards and data handling

- **Local only.** Ollama and the chat app listen on `localhost` only; Streamlit usage statistics are switched off and the fonts are bundled, so the app makes no outside requests. Documents, index, prompts, answers and run logs stay in this folder.
- **Restricted data.** `data/private/`, `index/`, `runs/` and private case files are git-ignored. Submit only the synthetic corpus and its runs.
- **Hallucination.** Prompt restricts answers to the supplied sources and requires `[S#]` citations; post-checks flag
  invalid citations, answers without citations, and numbers not found in the passage their sentence cites
  (see "Checks after the answer").
- **Demo safety.** `OFFTRAV_DEMO=1` limits the app to the synthetic index, so a class demo or recording cannot
  search the private documents.
- **Prompt injection.** Retrieved passages are delimited and labelled as data; passages with instruction-like text
  are excluded and the user is told which file was excluded.
- **Sensitive data.** The assistant is told not to ask for passport numbers or other personal data.
- **Human review.** Answers show sources; the UI reminds users to confirm with the cited document or office.
  The assistant does not decide eligibility or approvals.

## 7. Tests

```powershell
python -m pytest -q
```

The tests start `tests/mock_ollama.py`, a stand-in server that imitates the Ollama API (it is not a model), so they
check plumbing and deterministic safeguards without downloading anything. Scores from the stand-in are meaningless.

## 8. Reproducibility notes

- Decoding: temperature 0, seed 42, top_p 1, `think: false`. Wording can still vary across Ollama versions and hardware.
- Record `ollama --version` and `ollama show qwen3:30b-instruct` output with your results.
- Regenerate the synthetic corpus and cases: `python scripts/make_synthetic_corpus.py` and `python scripts/make_cases.py`.
- Override any setting without editing files, e.g. `$env:OFFTRAV_OLLAMA_CHAT_MODEL="llama3.1:8b"`.

## 9. Data sources

- Synthetic corpus: written for this project; fictional organisation, rates, contacts and form numbers.
- Real documents (not distributed): the organisation's foreign travel office order, booking guide and forms,
  used with permission, only on the author's laptop. Never uploaded, committed, shown or recorded.
