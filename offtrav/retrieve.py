"""Index building and hybrid retrieval (BM25 + dense embeddings, fused with RRF).

This is the "R" in RAG. Two phases:

  PRE-PROCESSING (run once, `python -m offtrav ingest ...`) -> build_index()
    P1  read every document in the data folder           (ingest.load_corpus)
    P2  split each document into chunks                    (ingest.py: ~900 characters, table rows kept whole)
    P3  embed every chunk with nomic-embed-text on Ollama  (_embed_texts)
    P4  save the index to disk                             (chunks.jsonl, embeddings.npy, manifest.json)

  AT QUESTION TIME -> Retriever.search()  (STEP 4 of the walkthrough in answer.py)
    a   keyword score for every chunk                      (BM25)
    b   meaning score for every chunk                      (cosine similarity of embeddings)
    c   merge the two rankings                             (reciprocal rank fusion, RRF)
    d   keep the top 6, at most 3 from one file            (diversity cap)
    e   add neighbouring chunks and table catch-all rows   (_expand)

The index lives in a folder:
  chunks.jsonl     one chunk per line (text + file name + page/row + section)
  embeddings.npy   L2-normalised float32 matrix, one row per chunk (absent if built with --no-embed)
  manifest.json    files + hashes, chunking params, embedding model, warnings

Why no vector database: the corpus is a few hundred chunks, so an exact search over a NumPy
matrix takes milliseconds and needs no extra service. A vector database (Chroma, FAISS) only
pays off at a much larger scale.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .ingest import Chunk, load_corpus

# Common words that carry no meaning for search; they are ignored by the keyword scorer.
STOPWORDS = set("""a an and are as at be by can do does for from has have how i if in is it its
me my of on or our should the their them there these this to was we what when where which who
will with you your am did get got any all""".split())
TOKEN_RE = re.compile(r"[a-z0-9]+")         # a "word" for BM25 is a run of letters/digits
# catch-all rows of a table (used when the employee's city or country has no row of its own)
DEFAULT_ROW = re.compile(r"\b(not listed|not in (?:this|the) (?:list|table)|all other|any other|"
                         r"other (?:countries|cities|destinations)|rest of the world)\b", re.I)


# suffix -> replacement pairs, tried in order; the first one that fits is applied
_SUFFIXES = (("ational", "ate"), ("ations", "ate"), ("ation", "ate"), ("ments", "ment"),
             ("ing", ""), ("ies", "y"), ("es", ""), ("ed", ""), ("s", ""))


def stem(t: str) -> str:
    """Very light suffix stripping so 'liquidation'/'liquidate' and 'allowances'/'allowance' match."""
    if len(t) <= 4 or t.isdigit():          # short words and numbers are kept as they are
        return t
    for suf, rep in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: -len(suf)] + rep
            break
    if t.endswith("e") and len(t) > 4:      # "liquidate" -> "liquidat", same as "liquidation"
        t = t[:-1]
    if len(t) > 4 and t[-1] == t[-2] and t[-1] not in "aeiou":   # "travell" -> "travel"
        t = t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    """Lower-case, split into words, drop stopwords, stem. Used for both chunks and questions."""
    return [stem(t) for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class BM25:
    """Okapi BM25 keyword scoring (Robertson & Zaragoza, 2009), written out in full.

    A chunk scores high when it contains the question's words, especially rare words
    (high IDF, e.g. "clothing", "TR-09"), and is not padded with unrelated text.
    k1 controls how fast repeated words stop adding score; b controls the length penalty.
    """

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        """Count words in every chunk and compute the IDF weights once."""
        self.k1, self.b = k1, b
        self.docs = [Counter(d) for d in docs]                    # word counts per chunk
        self.lens = np.array([len(d) for d in docs], dtype=float) # chunk lengths in words
        self.avgdl = float(self.lens.mean()) if len(docs) else 0.0
        df = Counter(t for d in docs for t in set(d))             # in how many chunks each word appears
        n = len(docs)
        # inverse document frequency: rare words weigh more
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        """Return one BM25 score per chunk for the tokenized query."""
        s = np.zeros(len(self.docs))
        for i, d in enumerate(self.docs):
            norm = self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avgdl or 1))
            for t in query:
                f = d.get(t)                                      # how often the word is in this chunk
                if f:
                    s[i] += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + norm)
        return s


def _embed_texts(client, texts: list[str], prefix: str, batch: int) -> np.ndarray:
    """Embed texts with the Ollama embedding model and L2-normalise each vector.

    nomic-embed-text expects a task prefix: "search_document: " for chunks and
    "search_query: " for questions (both set in config.yaml). After normalising,
    a plain dot product equals cosine similarity.
    """
    out = []
    for i in range(0, len(texts), batch):                         # send in batches to keep requests small
        out.extend(client.embed([prefix + t for t in texts[i:i + batch]]))
    m = np.asarray(out, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
    return m


def build_index(data_dir: str | Path, index_dir: str | Path, cfg: dict, client=None,
                org_name: str = "", embed: bool = True) -> dict:
    """PRE-PROCESSING: read the documents, chunk them, embed the chunks and save the index.

    Called by `python -m offtrav ingest --data <folder> --index <folder>`.
    Returns the manifest (what was indexed, plus any extraction warnings).
    """
    r = cfg["retrieval"]
    # P1 + P2: read every supported file and split it into chunks (see ingest.py)
    chunks, report = load_corpus(data_dir, r["chunk_chars"], r["chunk_overlap"])
    if not chunks:
        raise RuntimeError(f"No text could be extracted from {data_dir}. Warnings: {report.warnings}")
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    # P4a: save the chunk text and its location, one JSON object per line
    with (index_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
    emb_model = None
    if embed:
        # P3: embed "file | section" + chunk text, so the file name also helps the meaning search
        texts = [f"{c.header()}\n{c.text}" for c in chunks]
        m = _embed_texts(client, texts, r["embed_document_prefix"], r["embed_batch"])
        np.save(index_dir / "embeddings.npy", m)                 # P4b: the "vector store"
        emb_model = client.embed_model
    elif (index_dir / "embeddings.npy").exists():
        (index_dir / "embeddings.npy").unlink()                  # keyword-only index: remove stale vectors
    # P4c: manifest records what was indexed and how, so a mismatch can be detected later
    manifest = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "org_name": org_name,
        "n_chunks": len(chunks),
        "embed_model": emb_model,
        "chunk_chars": r["chunk_chars"],
        "chunk_overlap": r["chunk_overlap"],
        "files": report.files,
        "warnings": report.warnings,
    }
    (index_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


class Retriever:
    """Loads a prebuilt index and answers search queries against it."""

    def __init__(self, index_dir: str | Path, cfg: dict, client=None):
        """Load chunks, embeddings and manifest; rebuild the BM25 index in memory."""
        self.index_dir = Path(index_dir)
        self.cfg = cfg["retrieval"]
        self.client = client                                      # needed to embed the question
        if not (self.index_dir / "chunks.jsonl").exists():
            raise FileNotFoundError(f"No index at {self.index_dir}. Run: python -m offtrav ingest ...")
        self.manifest = json.loads((self.index_dir / "manifest.json").read_text(encoding="utf-8"))
        with (self.index_dir / "chunks.jsonl").open(encoding="utf-8") as f:
            self.chunks = [Chunk(**json.loads(line)) for line in f]
        # the keyword index is rebuilt in memory at start-up (fast for a few hundred chunks)
        self.bm25 = BM25([tokenize(f"{c.header()} {c.text}") for c in self.chunks])
        emb_path = self.index_dir / "embeddings.npy"
        self.emb = np.load(emb_path) if emb_path.exists() else None
        self.mode = self.cfg["mode"]                              # "hybrid" (default), "bm25" or "dense"
        if self.mode in ("hybrid", "dense") and self.emb is None:
            self.mode = "bm25"                                    # no vectors on disk: fall back to keywords
        # the question must be embedded with the same model as the chunks, or the scores are meaningless
        if self.emb is not None and client is not None and self.manifest.get("embed_model") \
                and self.manifest["embed_model"] != client.embed_model:
            raise RuntimeError(
                f"Index was built with embed model {self.manifest['embed_model']!r} but config uses "
                f"{client.embed_model!r}. Re-run ingest or change the config.")

    @staticmethod
    def _is_table_row(c: Chunk) -> bool:
        """Spreadsheet and CSV rows are stored with a location like "sheet 'Rates' row 5"."""
        return "row " in c.location

    def _expand(self, order: list[int]) -> list[tuple[int, str]]:
        """Add context that ranking alone misses, after the top_k ranked chunks are chosen.

        neighbours:   numbered steps are often split across two chunks, so the chunk just before
                      and just after each text hit ranked 1-3 (same file, same section) is added.
        table default rows: when a table row is retrieved, the same table's catch-all rows
                      ("not listed", "all other", "rest of the world") are added, because a place
                      that is not in the table has no row of its own to match the question.
        Returns (chunk index, reason) pairs; reason is "" for ranked chunks.
        """
        n_nb = int(self.cfg.get("expand_neighbors", 0) or 0)        # at most this many neighbours in total
        use_defaults = bool(self.cfg.get("table_default_rows", False))
        out: list[tuple[int, str]] = [(i, "") for i in order]
        have = set(order)

        def same_block(a: int, b: int) -> bool:
            """Neighbours must be from the same file and section, and be running text, not a table row."""
            ca, cb = self.chunks[a], self.chunks[b]
            return ca.source == cb.source and ca.section == cb.section and not self._is_table_row(cb)

        added = 0
        anchors = [i for i in order[:3] if not self._is_table_row(self.chunks[i])]  # text hits ranked 1-3
        for a in anchors:                                           # LOOP over the top text hits
            for j, before in ((a - 1, True), (a + 1, False)):       # the chunk before and the chunk after
                if added >= n_nb or not 0 <= j < len(self.chunks) or j in have or not same_block(a, j):
                    continue
                pos = next(k for k, (i, _) in enumerate(out) if i == a)
                out.insert(pos if before else pos + 1, (j, "neighbour"))   # keep reading order
                have.add(j)
                added += 1

        if use_defaults:
            # which tables (file + section) had at least one row retrieved?
            tables = {(self.chunks[i].source, self.chunks[i].section) for i in order
                      if self._is_table_row(self.chunks[i])}
            for j, c in enumerate(self.chunks):                     # LOOP over all chunks, at most 4 added
                if (c.source, c.section) in tables and j not in have and DEFAULT_ROW.search(c.text) \
                        and sum(1 for _, r in out if r == "table default row") < 4:
                    out.append((j, "table default row"))
                    have.add(j)
        return out

    @property
    def org_name(self) -> str:
        """Organisation name saved at ingest time; used in the system prompt."""
        return self.manifest.get("org_name") or "your organisation"

    def search(self, query: str, top_k: int | None = None, mode: str | None = None) -> dict:
        """STEP 4: return the passages most relevant to `query`.

        Result: {"hits": [chunk dicts with scores], "max_bm25": best keyword score,
                 "max_dense": best cosine similarity, "mode": search mode used}.
        max_bm25 and max_dense feed the no-evidence gate in answer.py.
        """
        top_k = top_k or self.cfg["top_k"]                          # 6 by default (config.yaml)
        mode = mode or self.mode
        n_cand = self.cfg["candidates"]                             # how deep each list goes before fusion

        # 4a: keyword ranking
        bm = self.bm25.scores(tokenize(query))
        bm_order = np.argsort(-bm)[:n_cand]

        # 4b: meaning ranking (embed the question, dot product with every chunk vector = cosine)
        dense = None
        if mode in ("hybrid", "dense"):
            q = _embed_texts(self.client, [query], self.cfg["embed_query_prefix"], 1)[0]
            dense = self.emb @ q
            dn_order = np.argsort(-dense)[:n_cand]

        # 4c: combine
        if mode == "bm25":
            ranked = [(int(i), float(bm[i])) for i in bm_order if bm[i] > 0]
        elif mode == "dense":
            ranked = [(int(i), float(dense[i])) for i in dn_order]
        else:  # reciprocal rank fusion (Cormack et al., 2009): score = sum of 1 / (k + rank)
            k = self.cfg["rrf_k"]                                   # 60, the value from the original paper
            fused: dict[int, float] = {}
            for rank, i in enumerate(i for i in bm_order if bm[i] > 0):   # keyword list (only real matches)
                fused[int(i)] = fused.get(int(i), 0) + 1 / (k + rank + 1)
            for rank, i in enumerate(dn_order):                            # meaning list
                fused[int(i)] = fused.get(int(i), 0) + 1 / (k + rank + 1)
            ranked = sorted(fused.items(), key=lambda x: -x[1])           # best combined score first

        # 4d: diversity: at most `max_per_source` chunks from one file, then fill with the rest
        cap = self.cfg.get("max_per_source", 0) or top_k
        picked, per_src, overflow = [], {}, []
        for i, s in ranked:                                         # LOOP down the fused ranking
            src = self.chunks[i].source
            if per_src.get(src, 0) < cap:
                picked.append((i, s))
                per_src[src] = per_src.get(src, 0) + 1
            else:
                overflow.append((i, s))                             # kept in reserve if we run short
            if len(picked) == top_k:
                break
        picked += overflow[: top_k - len(picked)]

        # 4e: add neighbours and table catch-all rows, then package each hit with its scores
        hits = []
        for i, expanded in self._expand([i for i, _ in picked]):
            c = self.chunks[i]
            hits.append({**c.to_dict(), "score": None if expanded else round(dict(picked)[i], 5),
                         "bm25": round(float(bm[i]), 4),
                         "dense": round(float(dense[i]), 4) if dense is not None else None,
                         "expanded": expanded})                     # "" for ranked hits, else the reason
        return {
            "hits": hits,
            "max_bm25": float(bm.max()) if len(bm) else 0.0,
            "max_dense": float(dense.max()) if dense is not None else None,
            "mode": mode,
        }
