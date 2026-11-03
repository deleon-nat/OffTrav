"""Re-score a finished run, e.g. after filling human_correct in runs/<run>/review.csv.

  python eval/score.py runs/20261101-101500_test
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from offtrav.scoring import score_row, write_outputs  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    run_dir = Path(sys.argv[1])
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    cases = {}
    with open(meta["cases_file"], encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                cases[c["id"]] = c
    results = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l]
    used = {r["case_id"] for r in results}
    cases = {k: v for k, v in cases.items() if k in used}
    rows = [score_row(cases[r["case_id"]], r) for r in results]
    write_outputs(run_dir, rows, cases, meta)
    print((run_dir / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
