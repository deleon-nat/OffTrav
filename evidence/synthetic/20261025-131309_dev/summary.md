# Evaluation summary: 20261025-131309_dev

- Split: **dev**, cases: 6
- Chat model: `qwen3:30b-instruct`, embed model: `nomic-embed-text`
- temperature=0.0, seed=42, num_ctx=8192, top_k=6, retrieval=hybrid, prompt v1.3
- Machine: Windows-11-10.0.26300-SP0, AMD64 Family 26 Model 96 Stepping 0, AuthenticAMD, 16 CPUs, RAM 27.7 GB
- Scoring: automatic rules in eval/cases_*.jsonl; reviewed: 0 of 12 scores; changed by review: 0

| Metric | Baseline: same LLM, no retrieval | Prototype: OffTrav RAG |
|---|---|---|
| Answer correctness (primary) | 0/6 (0%) | 6/6 (100%) |
|   answerable questions | 0/5 (0%) | 5/5 (100%) |
|   must-refuse questions | 0/1 (0%) | 1/1 (100%) |
|   prompt-injection cases | n/a | n/a |
| Citation accuracy | n/a | 5/5 (100%) |
| Retrieval recall@6 | n/a | 5/5 (100%) |
| Answers with unverified numbers | 0/6 (0%) | 0/6 (0%) |
| Latency mean (s) | 19.52 | 27.0 |
| Latency median (s) | 11.78 | 25.95 |
| Output tokens mean | 86.8 | 31 |
| Errors / incomplete runs | 0 | 0 |

Paired outcomes on the same cases (baseline vs RAG): both correct 0, only RAG correct 6, only baseline correct 0, both wrong 0.

Denominators include every case in the split; errors and empty outputs are scored as incorrect.
Small n: these are descriptive results, not tests of statistical significance.
