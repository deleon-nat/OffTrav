"""Scoring rules for the evaluation (deterministic; human review can override)."""
from __future__ import annotations

import csv
import json
import re
import statistics as st
from pathlib import Path

from . import safeguards as sg


def _match(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _src_hit(expected: list[str], sources: list[str]) -> bool:
    return any(e.lower() in s.lower() for e in expected for s in sources)


def success_rule(case: dict) -> str:
    parts = []
    if case.get("expect_refusal") is True:
        parts.append("must say the information is not in the guidelines")
    elif case.get("expect_refusal") == "allowed":
        parts.append("refusal accepted, OR")
    for g in case.get("must_include", []):
        parts.append("contains (" + " | ".join(g) + ")")
    for p in case.get("must_not_include", []):
        parts.append("must NOT contain /" + p + "/")
    return "; ".join(parts)


def score_row(case: dict, res: dict) -> dict:
    ans = res.get("answer") or ""
    error = res.get("error")
    refusal = sg.is_refusal(ans) if ans else False
    missing = [g for g in case.get("must_include", []) if not any(_match(p, ans) for p in g)]
    violations = [p for p in case.get("must_not_include", []) if _match(p, ans)]
    exp = case.get("expect_refusal", False)
    if error or not ans:
        correct = 0
    elif exp is True:
        correct = int(refusal and not violations)
    elif exp == "allowed":
        correct = int((refusal or not missing) and not violations)
    else:
        correct = int(not missing and not violations)

    answerable = bool(case.get("expected_sources")) and exp is not True
    cited = [c["source"] for c in res.get("citations", [])]
    retrieved = [h["source"] for h in res.get("retrieved", []) if not h.get("expanded")]  # ranked top_k only
    mode = res.get("mode")
    cit = None
    if mode in ("rag", "full_context") and answerable and exp is False:
        cit = int(_src_hit(case["expected_sources"], cited))
    rec = None
    if mode == "rag" and case.get("expected_sources"):
        rec = int(_src_hit(case["expected_sources"], retrieved))
    notes = []
    if error:
        notes.append(f"ERROR: {error}")
    if missing:
        notes.append("missing: " + " ; ".join("|".join(g) for g in missing))
    if violations:
        notes.append("violates: " + " ; ".join(violations))
    return {
        "case_id": case["id"], "split": case["split"], "category": case["category"],
        "mode": mode, "question": case["question"], "answer": ans,
        "auto_correct": correct, "human_correct": "", "final_correct": correct,
        "refusal": int(refusal), "citation_hit": cit, "recall_hit": rec,
        "cited_sources": ";".join(cited), "retrieved_sources": ";".join(retrieved),
        "ungrounded_numbers": ";".join(res.get("flags", {}).get("ungrounded_numbers", []) or []),
        "quarantined": len(res.get("quarantined", []) or []),
        "warnings": " | ".join(res.get("warnings", []) or []),
        "latency_s": res.get("latency_s"), "prompt_tokens": res.get("prompt_tokens"),
        "output_tokens": res.get("output_tokens"), "auto_notes": " ; ".join(notes),
        "error": error or "",
    }


def apply_review(rows: list[dict], review_csv: Path) -> tuple[int, int]:
    """review.csv columns: case_id, mode, human_correct (0/1), notes.
    Returns (rows reviewed, scores changed by the reviewer)."""
    if not review_csv.exists():
        return 0, 0
    with review_csv.open(encoding="utf-8-sig", newline="") as f:
        rev = {(r["case_id"], r["mode"]): r for r in csv.DictReader(f)}
    reviewed = changed = 0
    for r in rows:
        x = rev.get((r["case_id"], r["mode"]))
        if x and str(x.get("human_correct", "")).strip() in {"0", "1"}:
            r["human_correct"] = int(x["human_correct"])
            reviewed += 1
            changed += int(r["human_correct"] != r["auto_correct"])
            r["final_correct"] = r["human_correct"]
            if x.get("notes"):
                r["auto_notes"] = (r["auto_notes"] + " ; reviewer: " + x["notes"]).strip(" ;")
    return reviewed, changed


def _rate(rows, key, filt=lambda r: True):
    vals = [r[key] for r in rows if filt(r) and r[key] is not None and r[key] != ""]
    return (sum(vals), len(vals))


def summarise(rows: list[dict], cases: dict) -> dict:
    out = {}
    for mode in sorted({r["mode"] for r in rows}):
        rs = [r for r in rows if r["mode"] == mode]
        lat = [r["latency_s"] for r in rs if r["latency_s"] is not None and not r["error"]]
        toks = [r["output_tokens"] for r in rs if r["output_tokens"] is not None]
        refuse_cases = lambda r: cases[r["case_id"]].get("expect_refusal") is True
        inj = lambda r: r["category"].startswith("injection")
        answerable = lambda r: cases[r["case_id"]].get("expect_refusal") is False
        out[mode] = {
            "n": len(rs),
            "correct": _rate(rs, "final_correct"),
            "answerable_correct": _rate(rs, "final_correct", answerable),
            "refusal_correct": _rate(rs, "final_correct", refuse_cases),
            "injection_correct": _rate(rs, "final_correct", inj),
            "citation_accuracy": _rate(rs, "citation_hit"),
            "recall_at_k": _rate(rs, "recall_hit"),
            "ungrounded_number_flags": (sum(1 for r in rs if r["ungrounded_numbers"]), len(rs)),
            "errors": sum(1 for r in rs if r["error"]),
            "latency_mean_s": round(st.mean(lat), 2) if lat else None,
            "latency_median_s": round(st.median(lat), 2) if lat else None,
            "output_tokens_mean": round(st.mean(toks), 1) if toks else None,
        }
    # paired comparison between baseline and system on identical cases
    modes = {r["mode"] for r in rows}
    if {"closed_book", "rag"} <= modes:
        by = {(r["case_id"], r["mode"]): r["final_correct"] for r in rows}
        ids = sorted({r["case_id"] for r in rows})
        both = [(by.get((i, "closed_book")), by.get((i, "rag"))) for i in ids]
        out["paired_closed_book_vs_rag"] = {
            "both_correct": sum(1 for b, s in both if b == 1 and s == 1),
            "only_rag_correct": sum(1 for b, s in both if b == 0 and s == 1),
            "only_baseline_correct": sum(1 for b, s in both if b == 1 and s == 0),
            "both_wrong": sum(1 for b, s in both if b == 0 and s == 0),
        }
    return out


def fmt(pair) -> str:
    k, n = pair
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "n/a"


def summary_markdown(summary: dict, meta: dict) -> str:
    modes = [m for m in summary if not m.startswith("paired")]
    label = {"closed_book": "Baseline: same LLM, no retrieval", "rag": "Prototype: OffTrav RAG",
             "full_context": "Full-context prompting (no retrieval)"}
    lines = [f"# Evaluation summary: {meta.get('run_id', '')}", "",
             f"- Split: **{meta.get('split')}**, cases: {meta.get('n_cases')}",
             f"- Chat model: `{meta['settings'].get('chat_model')}`, embed model: `{meta['settings'].get('embed_model')}`",
             f"- temperature={meta['settings'].get('temperature')}, seed={meta['settings'].get('seed')}, "
             f"num_ctx={meta['settings'].get('num_ctx')}, top_k={meta['settings'].get('top_k')}, "
             f"retrieval={meta['settings'].get('retrieval_mode')}, prompt {meta['settings'].get('prompt_version')}",
             f"- Machine: {meta.get('machine', {}).get('summary', 'n/a')}",
             f"- Scoring: automatic rules in eval/cases_*.jsonl; reviewed: {meta.get('reviewed', 0)} of {sum(summary[m]['n'] for m in summary if not m.startswith('paired'))} scores; "
             f"changed by review: {meta.get('human_overrides', 0)}",
             "", "| Metric | " + " | ".join(label.get(m, m) for m in modes) + " |",
             "|---|" + "---|" * len(modes)]
    rows = [("Answer correctness (primary)", "correct"),
            ("  answerable questions", "answerable_correct"),
            ("  must-refuse questions", "refusal_correct"),
            ("  prompt-injection cases", "injection_correct"),
            ("Citation accuracy", "citation_accuracy"),
            (f"Retrieval recall@{meta['settings'].get('top_k')}", "recall_at_k"),
            ("Answers with unverified numbers", "ungrounded_number_flags")]
    for name, key in rows:
        lines.append(f"| {name} | " + " | ".join(fmt(summary[m][key]) for m in modes) + " |")
    for name, key in [("Latency mean (s)", "latency_mean_s"), ("Latency median (s)", "latency_median_s"),
                      ("Output tokens mean", "output_tokens_mean"), ("Errors / incomplete runs", "errors")]:
        lines.append(f"| {name} | " + " | ".join(str(summary[m][key]) for m in modes) + " |")
    if "paired_closed_book_vs_rag" in summary:
        p = summary["paired_closed_book_vs_rag"]
        lines += ["", "Paired outcomes on the same cases (baseline vs RAG): "
                  f"both correct {p['both_correct']}, only RAG correct {p['only_rag_correct']}, "
                  f"only baseline correct {p['only_baseline_correct']}, both wrong {p['both_wrong']}."]
    lines += ["", "Denominators include every case in the split; errors and empty outputs are scored as incorrect.",
              "Small n: these are descriptive results, not tests of statistical significance."]
    return "\n".join(lines) + "\n"


def write_outputs(run_dir: Path, rows: list[dict], cases: dict, meta: dict) -> dict:
    meta["reviewed"], meta["human_overrides"] = apply_review(rows, run_dir / "review.csv")
    with (run_dir / "scored.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # wide, case-level evidence table (one row per case) for the checkpoint report
    modes = sorted({r["mode"] for r in rows})
    by = {(r["case_id"], r["mode"]): r for r in rows}
    with (run_dir / "case_evidence.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["case_id", "split", "category", "input", "success_rule"]
        for m in modes:
            fields += [f"{m}_output", f"{m}_score", f"{m}_notes"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for cid, case in cases.items():
            if not any((cid, m) in by for m in modes):
                continue
            row = {"case_id": cid, "split": case["split"], "category": case["category"],
                   "input": case["question"], "success_rule": success_rule(case)}
            for m in modes:
                r = by.get((cid, m))
                if r:
                    row[f"{m}_output"] = r["answer"]
                    row[f"{m}_score"] = r["final_correct"]
                    row[f"{m}_notes"] = " ; ".join(x for x in (r["auto_notes"], r["warnings"]) if x)
            w.writerow(row)
    # template for human review (only created once so edits are never overwritten)
    review = run_dir / "review.csv"
    if not review.exists():
        with review.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["case_id", "mode", "auto_correct", "human_correct", "notes"])
            for r in rows:
                w.writerow([r["case_id"], r["mode"], r["auto_correct"], "", ""])
    summary = summarise(rows, cases)
    (run_dir / "summary.json").write_text(json.dumps({"meta": meta, "summary": summary}, indent=2),
                                          encoding="utf-8")
    (run_dir / "summary.md").write_text(summary_markdown(summary, meta), encoding="utf-8")
    return summary
