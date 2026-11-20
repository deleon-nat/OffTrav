# Evaluation summary: 20261102-120558_after_fixes_test_seen

- Split: **test**, cases: 20
- Chat model: `qwen3:30b-instruct`, embed model: `nomic-embed-text`
- temperature=0.0, seed=42, num_ctx=8192, top_k=6, retrieval=hybrid, prompt v1.4
- Machine: Windows-11-10.0.26300-SP0, AMD64 Family 26 Model 96 Stepping 0, AuthenticAMD, 16 CPUs, RAM 27.7 GB
- Scoring: automatic rules in eval/cases_*.jsonl; reviewed: 0 of 40 scores; changed by review: 0

| Metric | Baseline: same LLM, no retrieval | Prototype: OffTrav RAG |
|---|---|---|
| Answer correctness (primary) | 5/20 (25%) | 19/20 (95%) |
|   answerable questions | 4/17 (24%) | 16/17 (94%) |
|   must-refuse questions | 0/1 (0%) | 1/1 (100%) |
|   prompt-injection cases | 0/2 (0%) | 2/2 (100%) |
| Citation accuracy | n/a | 17/17 (100%) |
| Retrieval recall@6 | n/a | 18/18 (100%) |
| Answers with unverified numbers | 0/20 (0%) | 1/20 (5%) |
| Latency mean (s) | 14.4 | 33.43 |
| Latency median (s) | 11.9 | 29.22 |
| Output tokens mean | 135.9 | 94.8 |
| Errors / incomplete runs | 0 | 0 |

Paired outcomes on the same cases (baseline vs RAG): both correct 5, only RAG correct 14, only baseline correct 0, both wrong 1.

Denominators include every case in the split; errors and empty outputs are scored as incorrect.
Small n: these are descriptive results, not tests of statistical significance.
