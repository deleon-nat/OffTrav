# Evaluation summary: 20261025-131749_test

- Split: **test**, cases: 20
- Chat model: `qwen3:30b-instruct`, embed model: `nomic-embed-text`
- temperature=0.0, seed=42, num_ctx=8192, top_k=6, retrieval=hybrid, prompt v1.3
- Machine: Windows-11-10.0.26300-SP0, AMD64 Family 26 Model 96 Stepping 0, AuthenticAMD, 16 CPUs, RAM 27.7 GB
- Scoring: automatic rules in eval/cases_*.jsonl; reviewed: 40 of 40 scores; changed by review: 2

| Metric | Baseline: same LLM, no retrieval | Prototype: OffTrav RAG |
|---|---|---|
| Answer correctness (primary) | 3/20 (15%) | 17/20 (85%) |
|   answerable questions | 3/17 (18%) | 14/17 (82%) |
|   must-refuse questions | 0/1 (0%) | 1/1 (100%) |
|   prompt-injection cases | 0/2 (0%) | 2/2 (100%) |
| Citation accuracy | n/a | 16/17 (94%) |
| Retrieval recall@6 | n/a | 18/18 (100%) |
| Answers with unverified numbers | 0/20 (0%) | 1/20 (5%) |
| Latency mean (s) | 14.72 | 24.68 |
| Latency median (s) | 13.03 | 24.56 |
| Output tokens mean | 126.8 | 66.2 |
| Errors / incomplete runs | 0 | 0 |

Paired outcomes on the same cases (baseline vs RAG): both correct 3, only RAG correct 14, only baseline correct 0, both wrong 3.

Denominators include every case in the split; errors and empty outputs are scored as incorrect.
Small n: these are descriptive results, not tests of statistical significance.
