# Evaluation evidence (synthetic documents only)

All runs below use the fictional Biringan Central Bank documents in `data/sample_synthetic`,
model `qwen3:30b-instruct` and `nomic-embed-text` on Ollama, temperature 0, seed 42, top_k 6,
on a ThinkPad T14 (AMD, 16 threads, 32 GB RAM, no GPU). Results on the real documents are
reported only as totals in the written report; their questions and answers are not distributed.

| Folder | What it is | Result |
|---|---|---|
| `1_official_test/` | Official test run, 20 cases, prompt v1.3. `case_evidence.csv` has every question, the scoring rule, both outputs and the automatic scores; `review.csv` has the reviewed scores (every score checked and confirmed by the author; 2 baseline scores changed). | Baseline 3/20, OffTrav **17/20** |
| `2_full_context/` | Same 20 cases with every document pasted into the prompt instead of retrieval. | 20/20, median 27.6 s |
| `3_retrieval_ablation/` | Right file at rank 1, top 3, top 6 for keyword, embedding and hybrid search. | Hybrid 16/18 at rank 1 |
| `4_after_fixes/` | After the three fixes (prompt v1.4, neighbour passages, table catch-all rows): dev, the six new fix-check cases (reviewed by the author, `review_fixcheck.csv`), and the test split again (labelled *already seen*, automatic scores). | dev 6/6; fix-check 4/6; test already seen 19/20 |

The official result stays 17/20. The after-fix runs are reported separately because the fixes were
chosen after seeing the test failures.

Re-score any run after editing its review file: `python eval/score.py runs/<run folder>`.
