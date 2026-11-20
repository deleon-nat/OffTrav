# Evaluation summary: 20261102-113254_full_context

- Split: **test**, cases: 20
- Chat model: `qwen3:30b-instruct`, embed model: `nomic-embed-text`
- temperature=0.0, seed=42, num_ctx=8192, top_k=6, retrieval=hybrid, prompt v1.3
- Machine: Windows-11-10.0.26300-SP0, AMD64 Family 26 Model 96 Stepping 0, AuthenticAMD, 16 CPUs, RAM 27.7 GB
- Scoring: automatic rules in eval/cases_*.jsonl; reviewed: 0 of 20 scores; changed by review: 0

| Metric | Full-context prompting (no retrieval) |
|---|---|
| Answer correctness (primary) | 20/20 (100%) |
|   answerable questions | 17/17 (100%) |
|   must-refuse questions | 1/1 (100%) |
|   prompt-injection cases | 2/2 (100%) |
| Citation accuracy | 17/17 (100%) |
| Retrieval recall@6 | n/a |
| Answers with unverified numbers | 0/20 (0%) |
| Latency mean (s) | 51.6 |
| Latency median (s) | 27.59 |
| Output tokens mean | 92.7 |
| Errors / incomplete runs | 0 |

Denominators include every case in the split; errors and empty outputs are scored as incorrect.
Small n: these are descriptive results, not tests of statistical significance.
