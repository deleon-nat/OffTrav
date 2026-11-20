# OffTrav: your official travel buddy

OffTrav is a local chatbot for employees preparing for official foreign travel: what to prepare, which
forms to use, whom to e-mail, which steps to take and by when. It answers **only from the organisation's own
travel documents**, cites the passage behind each statement, and says so when the documents do not cover a
question. It runs entirely on one laptop with **Ollama**, so no document, prompt or answer leaves the machine.

Stat 280 Applied Generative AI capstone, Nathaniel De Leon. GenAI component: **retrieval-augmented generation
(RAG)**. No agent framework is used (see section 4.4).

This ZIP contains only **synthetic** documents (a fictional "Biringan Central Bank"). The real BSP documents
were used with permission on the author's laptop only and are not included.

---

## Quick start (after unzipping)

Needs: Python 3.10 or newer, [Ollama](https://ollama.com), about 20 GB of free disk and 32 GB of RAM
(see section 1 for a smaller model if the machine has less).

```powershell
# 1. models (one time; about 19 GB + 0.3 GB)
ollama pull qwen3:30b-instruct
ollama pull nomic-embed-text

# 2. Python packages
cd offtrav
python -m pip install -r requirements.txt

# 3. check that Ollama is running and both models are there
python -m offtrav check

# 4. PRE-PROCESSING: build the search index from the synthetic documents (about a minute)
python -m offtrav ingest --data data/sample_synthetic --index index/synthetic --org "Biringan Central Bank (synthetic)"

# 5. open the chat app in the browser (http://localhost:8501)
python -m streamlit run app.py
```

Try: *"It's my first official trip abroad. What do I need to prepare?"*, then the follow-up
*"What about the deadline to liquidate?"*. Or in the terminal, without the browser:

```powershell
python -m offtrav chat --index index/synthetic                       # conversation with follow-ups
python -m offtrav ask "How many airfare quotations do I need?" --index index/synthetic --show-prompt
```

`--show-prompt` prints the exact model input (rules, recent turns, numbered sources, question), which is
useful for the code review. To run the automated tests without any model: `python -m pytest -q`.

---

## Project layout

```
offtrav/                  core package (all GenAI code is here)
  ingest.py               PRE-PROCESSING: read PDF/DOCX/XLSX/CSV/MD and split into passages (no LLM)
  retrieve.py             PRE-PROCESSING: embed + save the index; AT QUESTION TIME: hybrid search
  answer.py               the question-answering pipeline (prepare -> model -> finalize)
  prompts.py              the system prompt (12 rules), source formatting, baseline prompt
  ollama_client.py        HTTP calls to the local Ollama server (embeddings and chat)
  safeguards.py           injection filter, citation check, per-sentence number check
  planner.py              My trip deadline planner (deterministic, no LLM)
  contacts.py             Contacts tab: contact list and mailto: links (no LLM)
  scoring.py              evaluation scoring rules and summaries
  cli.py, config.py       command line and config.yaml loading
app.py                    Streamlit chat app (Ask, My trip, Contacts, Documents)
config.yaml               models, retrieval and safeguard settings (recorded in every evaluation run)
data/sample_synthetic/    the synthetic document set (safe to share)
data/private/             real documents go here on the owner's laptop; empty in this ZIP
eval/                     test cases (32), evaluation runner, re-scoring
evidence/synthetic/       outputs of the reported evaluation runs (synthetic documents only)
scripts/                  synthetic corpus and case generators, scanned-PDF tools
tests/                    21 automated tests + a stand-in Ollama server (no model needed)
docs/                     architecture diagram (PNG, editable PowerPoint) and its build script
static/, .streamlit/      fonts, styles and local-only Streamlit settings
```

---

## 1. Requirements and hardware

| Item | Used for | Notes |
|---|---|---|
| Ollama 0.9 or newer | local model server on `localhost:11434` | Windows, macOS or Linux |
| `qwen3:30b-instruct` (~19 GB, 4-bit) | writes the answers | Mixture of experts, about 3B active parameters, so it runs on a CPU. Use the **instruct** version; plain `qwen3:30b` is the "thinking" version and spends minutes reasoning before it answers. |
| `nomic-embed-text` (~0.3 GB) | embeddings for search | |
| Python 3.10+ and `requirements.txt` | everything else | requests, numpy, PyYAML, pypdf, python-docx, openpyxl, streamlit, pytest |

**Tested on:** Lenovo ThinkPad T14, AMD, 16 threads, 32 GB RAM, no GPU, Windows 11, Ollama 0.40.5.
Typical time per answer there: about 25 seconds on the synthetic documents, 37 to 52 seconds on the larger
real document set. No internet is needed after the models are pulled, and there are no API keys or secrets.

**Less memory?** Switch to a smaller model without editing any file, then run as usual (answers are weaker,
and the reported results are for `qwen3:30b-instruct`):

```powershell
ollama pull llama3.1:8b
$env:OFFTRAV_OLLAMA_CHAT_MODEL = "llama3.1:8b"      # macOS/Linux: export OFFTRAV_OLLAMA_CHAT_MODEL=llama3.1:8b
```

**Optional:** a virtual environment (`python -m venv .venv`, then `.venv\Scripts\Activate.ps1` or
`source .venv/bin/activate`). If the `streamlit` command is "not recognized", use `python -m streamlit`.

## 2. Running it

```powershell
python -m offtrav ingest --data data/sample_synthetic --index index/synthetic --org "Biringan Central Bank (synthetic)"
python -m streamlit run app.py                         # browser UI, local only
$env:OFFTRAV_DEMO = "1"; python -m streamlit run app.py   # demo mode: only the synthetic documents can be selected
```

The chat app has four tabs:

| Tab | What it does | LLM? |
|---|---|---|
| Ask | Chat with follow-ups. Answers stream in, with sources, the cited passages, "How the numbers were checked", and any warnings. Sidebar: switch to "The model alone" to see the baseline live. | yes |
| My trip | Deadline planner: enter event type and dates; every deadline is computed from the deadline table in `data/sample_synthetic/_planner/` (working days skip weekends). Download an Outlook calendar file. | no |
| Contacts | The contact list, with "Write email" buttons that open a draft with the subject line filled in. | no |
| Documents | What is indexed, passages per file, and extraction warnings (for example scanned pages). | no |

---

## 3. Configuration (`config.yaml`)

Every value can be overridden with an environment variable `OFFTRAV_<SECTION>_<KEY>`.

| Setting | Value | Meaning |
|---|---|---|
| `ollama.chat_model` | `qwen3:30b-instruct` | answer model |
| `ollama.embed_model` | `nomic-embed-text` | embedding model (the index must be rebuilt if this changes) |
| `ollama.temperature`, `seed`, `top_p` | 0, 42, 1 | repeatable answers |
| `ollama.num_ctx`, `num_predict` | 8192, 700 | context window and maximum answer length (tokens) |
| `ollama.think` | false | stop Qwen3 printing its reasoning |
| `retrieval.mode` | `hybrid` | `hybrid` (BM25 + embeddings), `bm25` or `dense` |
| `retrieval.top_k` | 6 | passages given to the model |
| `retrieval.max_per_source` | 3 | at most 3 passages from one file |
| `retrieval.rrf_k` | 60 | reciprocal rank fusion constant |
| `retrieval.expand_neighbors` | 4 | add up to 4 passages next to the best hits (keeps numbered steps together) |
| `retrieval.table_default_rows` | true | add a table's "not listed" rows when one of its rows is found |
| `retrieval.chunk_chars`, `chunk_overlap` | 900, 150 | passage size and overlap (characters) |
| `safeguards.*` | on | injection filter, citation check, number check, no-evidence gate |
| `conversation.history_turns` | 2 | previous question-answer pairs sent with each new question |
| `conversation.today` | empty | date given to the model; fixed for evaluation runs |

---

## 4. Code walkthrough (for the code review)

Search the code for **`STEP`**: every step below is marked with that word in a comment.

### 4.1 Pre-processing (run once per document update)

`python -m offtrav ingest ...` → `cli.cmd_ingest` → `retrieve.build_index`

| | What happens | Where |
|---|---|---|
| P1 | read every file in the folder (PDF pages, Word sections and tables, spreadsheet rows, CSV rows, Markdown sections) | `ingest.load_corpus`, `load_pdf`, `load_docx`, `load_xlsx`, `load_csv`, `load_markdown` |
| P2 | split long text into ~900-character passages with 150 characters of overlap; table rows stay whole as "Header: value; ..." | `ingest.split_text`, `_row_text` |
| P3 | embed every passage with `nomic-embed-text` (prefix `search_document:`), normalise the vectors | `retrieve._embed_texts` → `ollama_client.embed` |
| P4 | save `index/synthetic/chunks.jsonl`, `embeddings.npy`, `manifest.json` | `retrieve.build_index` |

The "vector store" is a NumPy matrix on disk. With a few hundred passages an exact search takes milliseconds,
so no vector database is needed.

### 4.2 One question, step by step

| Step | What happens | Where |
|---|---|---|
| 1 | the question is assigned to the variable `question` (chat box or starter button) | `app.py`, `typed = st.chat_input(...)`, `question = typed or question` (terminal: `cli.cmd_chat`, variable `q`) |
| 2 | trim, cap the length, flag injection attempts in the question | `answer.Assistant.prepare` |
| 3 | a short follow-up is combined with the previous question **for searching only** | `prepare`, `q_for_search` |
| 4 | hybrid search: BM25 keyword scores + cosine similarity of embeddings, merged by reciprocal rank fusion, top 6 with at most 3 per file, then neighbour passages and table catch-all rows are added | `retrieve.Retriever.search`, `_expand` |
| 5 | passages with instruction-like text are removed; if nothing relevant is left, the fixed refusal is returned **without calling the model** | `safeguards.quarantine_chunks`, `Assistant._no_evidence` |
| 6 | the prompt is built: system message (12 rules + today's date) → last 2 question-answer pairs → user message with numbered sources `[S1]..[Sn]` and the question | `prepare`, `prompts.RAG_SYSTEM`, `prompts.format_sources`, `prompts.RAG_USER` |
| 7 | `qwen3:30b-instruct` writes the answer; the app shows it as it streams | `Assistant.stream_text` → `ollama_client.chat_stream` (CLI/evaluation: `chat`) |
| 8 | every `[S#]` must point to a supplied passage; every number in each sentence is checked against the passage that sentence cites | `Assistant.finalize`, `safeguards.check_citations`, `check_claims` |
| 9 | the answer and its checks are appended to the conversation history | `app.py`, `st.session_state.messages.append(...)` |

Loops worth pointing out: the BM25 scoring loop over passages (`BM25.scores`), the fusion and per-file cap
loop (`search`), the neighbour loop (`_expand`), the streaming loop over generated pieces (`chat_stream`),
and the claim/number loops in `check_claims`. There is no loop of model calls: **one question = one LLM call**
(plus one embedding call for the question).

### 4.3 Follow-up questions and conversation memory

1. The app keeps the whole conversation in `st.session_state.messages` (Streamlit reruns the script on every
   click, so state must live there). Each item is `{"role": "user"|"assistant", "content": ...}`.
2. When a new question arrives, `app.py` passes all earlier messages as `history` to `assistant.prepare`.
3. `Assistant._history_messages` keeps the last `history_turns` = 2 pairs (4 messages) and puts them between
   the system message and the new user message, so the model sees the recent conversation.
4. Because a follow-up such as "What about the deadline to liquidate?" is too short to search on its own,
   `prepare` searches with the previous question + the follow-up (STEP 3). The model still sees the follow-up
   exactly as typed.
5. After the answer, STEP 9 appends it to `st.session_state.messages`; "New chat" clears it.

`python -m offtrav chat` does the same in the terminal with a plain `history` list (`cli.cmd_chat`).

### 4.4 Why no agent

The tool-like features (deadline planner, contact list, e-mail draft) are fixed code paths the user opens in
their own tab; the LLM never chooses or calls a tool. Deadlines need exact working-day arithmetic, which code
does reliably and a model does not, and a single grounded call is easier to test and to explain. An agent
loop would add latency on a CPU (several model calls per question) and new failure modes without improving
the answers measured here. See the written report for the comparison.

---

## 5. Evaluation

```powershell
# official comparison: baseline (same model, no documents) vs OffTrav, on the frozen test split
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split test --label test

# other splits: dev (used while building), fixcheck (written after the test to check the fixes)
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split fixcheck --label fixcheck

# third condition: every document pasted into the prompt, no retrieval
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split test --modes closed_book rag full_context --label test3

# search-only ablation, no chat model needed: recall@1/3/6 for BM25, embeddings, hybrid
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split test --retrieval-only

# quick smoke test on 2 cases
python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split test --limit 2
```

A full test run (20 cases x 2 modes) takes about 15 to 20 minutes on the tested laptop. Each run writes
`runs/<timestamp>_<label>/`: `meta.json` (all settings, machine, file hashes), `results.jsonl` (every output,
including errors), `scored.csv`, `case_evidence.csv` (question, scoring rule, both outputs, scores),
`review.csv` (fill `human_correct` to override a score), `summary.md`. After reviewing:
`python eval/score.py runs/<run folder>`.

### 5.1 Test cases and scoring

`eval/cases_synthetic.jsonl`: 32 cases, written before testing: 6 **dev** (S01 to S06), 20 **test** (T01 to
T20), and 6 **fixcheck** (F01 to F06, written after the test, before the revised pipeline was run).

| Category | Test cases | What it checks |
|---|---|---|
| fact | T01 T02 T03 T11 T14 T15 | one rule from the office order |
| table | T04 T06 T07 T08 T09 | row lookup in the allowance workbook, including the "not listed" fallback |
| calculation | T05 | rate x days |
| contact | T10 | the right office and e-mail |
| procedure | T12 T13 | multi-step process with deadlines |
| form | T16 | correct form and attachment |
| unanswerable / out of scope | T17 T18 | must say it is not covered instead of guessing |
| prompt injection | T19 (in the question), T20 (planted in an unofficial document) | the rules hold; the planted figure is never repeated |

**Primary metric: answer correctness** = correct answers / all cases in the split. Correct means every
`must_include` group matches, no `must_not_include` pattern matches, and refusal behaviour matches
`expect_refusal`. Errors, timeouts and empty outputs count as wrong. Secondary: citation accuracy, retrieval
recall at 6, latency, output tokens.

**Baseline:** the same model and settings with no documents (roughly what a general chatbot would give).

### 5.2 Results (details in `evidence/synthetic/` and the written report)

| Run | Baseline | OffTrav |
|---|---|---|
| Official test, 20 cases (reviewed) | 3/20 | **17/20** (citations 16/17, right file in top 6 18/18, median 24.6 s) |
| Every document pasted in, same 20 | | 20/20 (median 27.6 s; the synthetic set is only ~5,000 tokens) |
| Search ablation, right file at rank 1 | | keywords 13/18, embeddings 15/18, hybrid 16/18 |
| After the fixes: dev / fix-check / test already seen | 0/6, 0/6, 5/20 | 6/6, 4/6, 19/20 |
| Real documents, 23 questions (author's laptop, totals only) | 4/23 | 18/23; 20/23 after the fixes and review |

On the real documents (about 29,000 tokens), pasting every document in was rejected at a 16k context window,
and at 32k one answer took over 30 minutes; searching first is what makes the system usable on a laptop.

---

## 6. What the LLM does and what code does

| Deterministic code | LLM on Ollama |
|---|---|
| loading and chunking documents, table-row text | embeddings (`nomic-embed-text`) |
| BM25, cosine search, rank fusion, per-file cap, neighbour and catch-all rows | writing the answer from numbered sources (`qwen3:30b-instruct`) |
| injection filter, no-evidence gate, question length cap | |
| citation check, per-sentence number check, refusal detection | |
| deadline planner, contact list, e-mail drafts | |
| scoring and reports | |

People: prepare and update the documents and the deadline table, fix values flagged in scanned PDFs, write
the test cases, review the scores, and confirm any answer with the DES Administrative Unit before acting on it.

### Checks after the answer

| Result | Meaning | Shown to the user |
|---|---|---|
| found in the cited source | the number is in the cited passage, and working/calendar days agree | in "How the numbers were checked" |
| from your question | the employee gave it (for example "a 4-day trip") | same |
| calculated | equals a product or sum of grounded numbers (USD 50 x 4 = 200) | same |
| unit differs | the cited passage has the number with the other kind of day | warning |
| not in the cited source | the number is only in another passage | warning |
| no citation / not in any source | | warning |

The answer is still shown when a check fails; the warning names the number and says to confirm with the DES
Administrative Unit. **Not checked by code:** whether the rule fits the employee's destination, rank, event
type or dates. That depends on the prompt rules, is measured by the test cases, and stays with the office.

## 7. Scanned PDFs

A scanned PDF either has no text (OffTrav cannot read it; the Documents tab warns) or carries the scanner's
own OCR text, which can be wrong (a real office order said "50" for "30 calendar days").

```powershell
python scripts/compare_extraction.py data/sample_synthetic   # lists only numbers whose readings disagree
python scripts/ocr_to_docx.py "<file>.pdf"                    # scanned PDF with no text -> Word file, uncertain lines highlighted
```

`compare_extraction.py` compares the stored text, a second PDF library and a fresh OCR of the page, and flags
word/digit disagreements such as "thirty (50)". On a degraded synthetic scan it caught all 9 misread numbers.
It cannot catch a number both readings get wrong. Optional OCR engines: `pip install rapidocr-onnxruntime pypdfium2`
or an installed Tesseract.

## 8. Safeguards and data handling

- **Local only.** Ollama and Streamlit listen on `localhost`; Streamlit usage statistics are off and the fonts are bundled, so the app makes no outside requests.
- **Restricted data.** `data/private/`, `index/`, `runs/` and private case files are excluded (see `.gitignore`); this ZIP has none of them.
- **Hallucination.** Answers must come from the numbered sources with `[S#]` citations; code checks citations and numbers; the model is not called when nothing relevant is found.
- **Prompt injection.** Sources are delimited and labelled as data; passages with instruction-like text are removed and the user is told which file.
- **Personal data.** OffTrav never asks for passport numbers or other personal data.
- **Decisions.** Eligibility answers are preliminary; OffTrav approves, fills in and submits nothing. Contacts only opens a draft in the user's own mail app.
- **Demo safety.** `OFFTRAV_DEMO=1` limits the app to the synthetic index.

## 9. Tests

```powershell
python -m pytest -q          # 21 tests, about 3 seconds, no model download needed
```

The tests start `tests/mock_ollama.py`, a stand-in that imitates the Ollama API (it is not a model), so they
check the plumbing and deterministic safeguards only. Scores from the stand-in are meaningless.

## 10. Reproducibility

- Decoding: temperature 0, seed 42, top_p 1, `think: false`. Wording can still vary across Ollama versions and hardware.
- Every run records its settings, prompt version and file hashes in `meta.json`.
- Regenerate the synthetic documents and cases: `python scripts/make_synthetic_corpus.py` (needs `reportlab`) and `python scripts/make_cases.py`.
- New questions can be written in Excel (`eval/cases_template.xlsx`) and converted with `python scripts/cases_from_excel.py <in.xlsx> <out.jsonl>`.

## 11. Data sources

- Synthetic documents: written for this project; fictional organisation, rates, contacts and form numbers.
- Real documents (not distributed): BSP foreign travel office orders and memoranda, allowance guidelines, booking guide, checklists and forms, used with permission on the author's laptop only.
