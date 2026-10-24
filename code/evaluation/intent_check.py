"""Intent-only regression check: one classification call per request, no investigation.

    python code/evaluation/intent_check.py       # dev requests + custom cases

Dev requests are all deletions (gold); custom cases carry hand-written labels.
Holdout texts are not used.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))  # after code/, so code/main.py wins over the empty evaluation/main.py

from deletion_audit.agent import AgentJudge          # noqa: E402
from main import select_requests, usage_cost          # noqa: E402
import custom_cases                                   # noqa: E402


def main():
    items = [(r, True) for r in select_requests("dev")]
    items += [(d["request"], d["expected_status"] != "NOT_APPLICABLE") for d in custom_cases.cases()]
    judge = AgentJudge()
    usage = []

    def one(item):
        req, want = item
        got = judge.classify_intent(req, usage)
        return req["case_id"], want, bool(got["authorizes_deletion"]), got["rationale"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(one, items))
    wrong = [r for r in rows if r[1] != r[2]]
    for cid, want, got, why in wrong:
        print(f"WRONG {cid}: expected deletion={want}, got {got}: {why[:200]}")
    print(f"{len(rows) - len(wrong)}/{len(rows)} correct; {len(usage)} calls, ~${usage_cost(usage):.4f}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
