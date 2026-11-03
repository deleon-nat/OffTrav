"""Run the baseline and the RAG system on the same cases and score them.

  # full comparison on the final test split (baseline + system)
  python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --split test

  # quick retrieval-only ablation (no chat model needed): recall@k for bm25 / dense / hybrid
  python eval/run_eval.py --index index/synthetic --cases eval/cases_synthetic.jsonl --retrieval-only

Outputs go to runs/<timestamp>_<label>/ : results.jsonl, scored.csv, case_evidence.csv,
review.csv (fill human_correct to override), summary.md, summary.json, meta.json
Re-score after human review:  python eval/score.py runs/<run folder>
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from offtrav.config import load_config  # noqa: E402
from offtrav.answer import Assistant  # noqa: E402
from offtrav.scoring import score_row, write_outputs  # noqa: E402


def load_cases(path: str, split: str) -> dict:
    cases = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                if split == "all" or c["split"] == split:
                    cases[c["id"]] = c
    return cases


def machine_info() -> dict:
    info = {"platform": platform.platform(), "processor": platform.processor() or platform.machine(),
            "cpu_count": os.cpu_count(), "python": platform.python_version()}
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:
        info["ram_gb"] = None
    info["summary"] = f"{info['platform']}, {info['processor']}, {info['cpu_count']} CPUs, RAM {info['ram_gb']} GB"
    return info


def retrieval_only(args, cfg, cases):
    from offtrav.ollama_client import OllamaClient
    from offtrav.retrieve import Retriever
    from offtrav.scoring import _src_hit
    r = Retriever(args.index, cfg, OllamaClient(cfg))
    modes = ["bm25"] + (["dense", "hybrid"] if r.emb is not None else [])
    ks = [1, 3, cfg["retrieval"]["top_k"]]
    lines = ["| Retriever | " + " | ".join(f"recall@{k}" for k in ks) + " |", "|---|" + "---|" * len(ks)]
    rows = []
    scored = [c for c in cases.values() if c.get("expected_sources")]
    for m in modes:
        hits_at = {k: 0 for k in ks}
        for c in scored:
            res = r.search(c["question"], top_k=max(ks), mode=m)
            srcs = [h["source"] for h in res["hits"] if not h.get("expanded")]  # ranked chunks only
            for k in ks:
                hit = _src_hit(c["expected_sources"], srcs[:k])
                hits_at[k] += hit
            rows.append({"case_id": c["id"], "retriever": m, "top_sources": ";".join(srcs)})
        lines.append(f"| {m} | " + " | ".join(f"{hits_at[k]}/{len(scored)}" for k in ks) + " |")
    table = "\n".join(lines)
    print(table)
    return table, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--split", default="test", choices=["dev", "test", "fixcheck", "all"])
    ap.add_argument("--modes", nargs="+", default=["closed_book", "rag"],
                    choices=["closed_book", "rag", "full_context"])
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default=str(ROOT / "runs"))
    ap.add_argument("--config", default=None)
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="run only the first N cases (smoke test)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    cases = load_cases(args.cases, args.split)
    if args.limit:
        cases = dict(list(cases.items())[: args.limit])
    if not cases:
        print("No cases selected.")
        return 1
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"_{args.label}" if args.label else "")
    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.retrieval_only:
        table, rows = retrieval_only(args, cfg, cases)
        (run_dir / "retrieval_ablation.md").write_text(
            f"# Retrieval ablation ({args.split}, {len(cases)} cases)\n\n{table}\n", encoding="utf-8")
        with (run_dir / "retrieval_ablation.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(f"Saved to {run_dir}")
        return 0

    assistant = Assistant(cfg, args.index)
    meta = {"run_id": run_id, "split": args.split, "n_cases": len(cases), "modes": args.modes,
            "cases_file": args.cases, "index": args.index, "settings": assistant._settings(),
            "index_manifest": assistant.retriever.manifest.get("files"), "machine": machine_info(),
            "started": datetime.now().isoformat(timespec="seconds")}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    rows = []
    t_start = time.perf_counter()
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for n, (cid, case) in enumerate(cases.items(), 1):
            for mode in args.modes:
                try:
                    res = assistant.ask(case["question"], mode=mode)
                except Exception as e:  # record failures instead of discarding them
                    res = {"question": case["question"], "mode": mode, "answer": "",
                           "error": f"{type(e).__name__}: {e}", "citations": [], "retrieved": [],
                           "warnings": [], "flags": {}}
                res["case_id"] = cid
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
                row = score_row(case, res)
                rows.append(row)
                status = "ERR" if row["error"] else ("ok " if row["auto_correct"] else "x  ")
                print(f"[{n}/{len(cases)}] {cid} {mode:<12} {status} {row['latency_s']}s")
    meta["finished"] = datetime.now().isoformat(timespec="seconds")
    meta["wall_time_s"] = round(time.perf_counter() - t_start, 1)
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    write_outputs(run_dir, rows, cases, meta)
    print((run_dir / "summary.md").read_text(encoding="utf-8"))
    print(f"Saved to {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
