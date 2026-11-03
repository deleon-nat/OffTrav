"""Index building and hybrid retrieval (BM25 + dense embeddings, fused with RRF).

The index lives in a folder:
  chunks.jsonl     one chunk per line
  embeddings.npy   L2-normalised float32 matrix (absent if built with --no-embed)
  manifest.json    files + hashes, chunking params, embedding model, warnings
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

STOPWORDS = set("""a an and are as at be by can do does for from has have how i if in is it its
me my of on or our should the their them there these this to was we what when where which who
will with you your am did get got any all""".split())
TOKEN_RE = re.compile(r"[a-z0-9]+")
# catch-all rows of a table (used when the employee's city or country has no row of its own)
DEFAULT_ROW = re.compile(r"\b(not listed|not in (?:this|the) (?:list|table)|all other|any other|"
                         r"other (?:countries|cities|destinations)|rest of the world)\b", re.I)


_SUFFIXES = (("ational", "ate"), ("ations", "ate"), ("ation", "ate"), ("ments", "ment"),
             ("ing", ""), ("ies", "y"), ("es", ""), ("ed", ""), ("s", ""))


def stem(t: str) -> str:
    """Very light suffix stripping so 'liquidation'/'liquidate' and 'allowances'/'allowance' match."""
    if len(t) <= 4 or t.isdigit():
        return t
    for suf, rep in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: -len(suf)] + rep
            break
    if t.endswith("e") and len(t) > 4:
        t = t[:-1]
    if len(t) > 4 and t[-1] == t[-2] and t[-1] not in "aeiou":
        t = t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(d) for d in docs]
        self.lens = np.array([len(d) for d in docs], dtype=float)
        self.avgdl = float(self.lens.mean()) if len(docs) else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        s = np.zeros(len(self.docs))
        for i, d in enumerate(self.docs):
            norm = self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avgdl or 1))
            for t in query:
                f = d.get(t)
                if f:
                    s[i] += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + norm)
        return s


def _embed_texts(client, texts: list[str], prefix: str, batch: int) -> np.ndarray:
    out = []
    for i in range(0, len(texts), batch):
        out.extend(client.embed([prefix + t for t in texts[i:i + batch]]))
    m = np.asarray(out, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
    return m


def build_index(data_dir: str | Path, index_dir: str | Path, cfg: dict, client=None,
                org_name: str = "", embed: bool = True) -> dict:
    r = cfg["retrieval"]
    chunks, report = load_corpus(data_dir, r["chunk_chars"], r["chunk_overlap"])
    if not chunks:
        raise RuntimeError(f"No text could be extracted from {data_dir}. Warnings: {report.warnings}")
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    with (index_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
    emb_model = None
    if embed:
        texts = [f"{c.header()}\n{c.text}" for c in chunks]
        m = _embed_texts(client, texts, r["embed_document_prefix"], r["embed_batch"])
        np.save(index_dir / "embeddings.npy", m)
        emb_model = client.embed_model
    elif (index_dir / "embeddings.npy").exists():
        (index_dir / "embeddings.npy").unlink()
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
    def __init__(self, index_dir: str | Path, cfg: dict, client=None):
        self.index_dir = Path(index_dir)
        self.cfg = cfg["retrieval"]
        self.client = client
        if not (self.index_dir / "chunks.jsonl").exists():
            raise FileNotFoundError(f"No index at {self.index_dir}. Run: python -m offtrav ingest ...")
        self.manifest = json.loads((self.index_dir / "manifest.json").read_text(encoding="utf-8"))
        with (self.index_dir / "chunks.jsonl").open(encoding="utf-8") as f:
            self.chunks = [Chunk(**json.loads(line)) for line in f]
        self.bm25 = BM25([tokenize(f"{c.header()} {c.text}") for c in self.chunks])
        emb_path = self.index_dir / "embeddings.npy"
        self.emb = np.load(emb_path) if emb_path.exists() else None
        self.mode = self.cfg["mode"]
        if self.mode in ("hybrid", "dense") and self.emb is None:
            self.mode = "bm25"
        if self.emb is not None and client is not None and self.manifest.get("embed_model") \
                and self.manifest["embed_model"] != client.embed_model:
            raise RuntimeError(
                f"Index was built with embed model {self.manifest['embed_model']!r} but config uses "
                f"{client.embed_model!r}. Re-run ingest or change the config.")

    @staticmethod
    def _is_table_row(c: Chunk) -> bool:
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
        n_nb = int(self.cfg.get("expand_neighbors", 0) or 0)
        use_defaults = bool(self.cfg.get("table_default_rows", False))
        out: list[tuple[int, str]] = [(i, "") for i in order]
        have = set(order)

        def same_block(a: int, b: int) -> bool:
            ca, cb = self.chunks[a], self.chunks[b]
            return ca.source == cb.source and ca.section == cb.section and not self._is_table_row(cb)

        added = 0
        anchors = [i for i in order[:3] if not self._is_table_row(self.chunks[i])]  # text hits ranked 1-3
        for a in anchors:
            for j, before in ((a - 1, True), (a + 1, False)):
                if added >= n_nb or not 0 <= j < len(self.chunks) or j in have or not same_block(a, j):
                    continue
                pos = next(k for k, (i, _) in enumerate(out) if i == a)
                out.insert(pos if before else pos + 1, (j, "neighbour"))
                have.add(j)
                added += 1

        if use_defaults:
            tables = {(self.chunks[i].source, self.chunks[i].section) for i in order
                      if self._is_table_row(self.chunks[i])}
            for j, c in enumerate(self.chunks):
                if (c.source, c.section) in tables and j not in have and DEFAULT_ROW.search(c.text) \
                        and sum(1 for _, r in out if r == "table default row") < 4:
                    out.append((j, "table default row"))
                    have.add(j)
        return out

    @property
    def org_name(self) -> str:
        return self.manifest.get("org_name") or "your organisation"

    def search(self, query: str, top_k: int | None = None, mode: str | None = None) -> dict:
        top_k = top_k or self.cfg["top_k"]
        mode = mode or self.mode
        n_cand = self.cfg["candidates"]
        bm = self.bm25.scores(tokenize(query))
        bm_order = np.argsort(-bm)[:n_cand]
        dense = None
        if mode in ("hybrid", "dense"):
            q = _embed_texts(self.client, [query], self.cfg["embed_query_prefix"], 1)[0]
            dense = self.emb @ q
            dn_order = np.argsort(-dense)[:n_cand]
        if mode == "bm25":
            ranked = [(int(i), float(bm[i])) for i in bm_order if bm[i] > 0]
        elif mode == "dense":
            ranked = [(int(i), float(dense[i])) for i in dn_order]
        else:  # reciprocal rank fusion
            k = self.cfg["rrf_k"]
            fused: dict[int, float] = {}
            for rank, i in enumerate(i for i in bm_order if bm[i] > 0):
                fused[int(i)] = fused.get(int(i), 0) + 1 / (k + rank + 1)
            for rank, i in enumerate(dn_order):
                fused[int(i)] = fused.get(int(i), 0) + 1 / (k + rank + 1)
            ranked = sorted(fused.items(), key=lambda x: -x[1])
        # diversity: at most `max_per_source` chunks from one file, then fill with the rest
        cap = self.cfg.get("max_per_source", 0) or top_k
        picked, per_src, overflow = [], {}, []
        for i, s in ranked:
            src = self.chunks[i].source
            if per_src.get(src, 0) < cap:
                picked.append((i, s))
                per_src[src] = per_src.get(src, 0) + 1
            else:
                overflow.append((i, s))
            if len(picked) == top_k:
                break
        picked += overflow[: top_k - len(picked)]
        hits = []
        for i, expanded in self._expand([i for i, _ in picked]):
            c = self.chunks[i]
            hits.append({**c.to_dict(), "score": None if expanded else round(dict(picked)[i], 5),
                         "bm25": round(float(bm[i]), 4),
                         "dense": round(float(dense[i]), 4) if dense is not None else None,
                         "expanded": expanded})
        return {
            "hits": hits,
            "max_bm25": float(bm.max()) if len(bm) else 0.0,
            "max_dense": float(dense.max()) if dense is not None else None,
            "mode": mode,
        }
