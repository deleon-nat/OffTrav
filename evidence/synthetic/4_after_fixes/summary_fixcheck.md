# Evaluation summary: 20261102-120000_after_fixes_fixcheck

- Split: **fixcheck**, cases: 6
- Chat model: `qwen3:30b-instruct`, embed model: `nomic-embed-text`
- temperature=0.0, seed=42, num_ctx=8192, top_k=6, retrieval=hybrid, prompt v1.4
- Machine: Windows-11-10.0.26300-SP0, AMD64 Family 26 Model 96 Stepping 0, AuthenticAMD, 16 CPUs, RAM 27.7 GB
- Scoring: automatic rules in eval/cases_*.jsonl; reviewed: 0 of 12 scores; changed by review: 0

| Metric | Baseline: same LLM, no retrieval | Prototype: OffTrav RAG |
|---|---|---|
| Answer correctness (primary) | 1/6 (17%) | 4/6 (67%) |
|   answerable questions | 1/6 (17%) | 4/6 (67%) |
|   must-refuse questions | n/a | n/a |
|   prompt-injection cases | n/a | n/a |
| Citation accuracy | n/a | 6/6 (100%) |
| Retrieval recall@6 | n/a | 6/6 (100%) |
| Answers with unverified numbers | 0/6 (0%) | 0/6 (0%) |
| Latency mean (s) | 17.4 | 42.13 |
| Latency median (s) | 17.18 | 33.9 |
| Output tokens mean | 134.5 | 133.2 |
| Errors / incomplete runs | 0 | 0 |

Paired outcomes on the same cases (baseline vs RAG): both correct 0, only RAG correct 4, only baseline correct 1, both wrong 1.

Denominators include every case in the split; errors and empty outputs are scored as incorrect.
Small n: these are descriptive results, not tests of statistical significance.
