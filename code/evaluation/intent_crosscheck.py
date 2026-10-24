"""Post-hoc cross-check of NOT_APPLICABLE predictions against the CLASSIFY_REQUEST
activity rows ("Privacy intake classified request for account_X as non-deletion").

These rows are excluded from the evidence index and never reach the classifier; this
script only compares the finished output with them afterwards.

    python code/evaluation/intent_crosscheck.py [output.csv]
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deletion_audit.data import load_dataset, parse_ts          # noqa: E402
from deletion_audit.evidence_index import build_index           # noqa: E402
from deletion_audit.identity import resolver                    # noqa: E402
from deletion_audit.paths import OUTPUT_CSV                     # noqa: E402


def crosscheck(pred: dict[str, str]) -> dict:
    """pred: case_id -> deletion_status. Returns the comparison with intake verdicts."""
    ds, index, ids = load_dataset(), build_index(), resolver()
    predicted_na = {c for c, s in pred.items() if s == "NOT_APPLICABLE"}
    by_user = defaultdict(list)
    for r in ds.requests:
        for u in ids.resolve(r["email"]).user_ids:
            by_user[u].append(r)
    rows = [r for r in ds.activity if r["event_type"] == "CLASSIFY_REQUEST"]
    matched, unmatched = {}, []
    for row in rows:
        account, when = row["resource_id"], parse_ts(row["event_time"])
        owners = set()
        for rec in index.by_resource.get(account, []):
            owners |= ids.owners(rec.tokens)
        # the request this verdict belongs to: same subject, requested before the verdict
        cands = [r for u in owners for r in by_user.get(u, []) if parse_ts(r["requested_at"]) <= when]
        if cands:
            req = max(cands, key=lambda r: r["requested_at"])
            matched[req["case_id"]] = row["activity_id"]
        else:
            unmatched.append((row["activity_id"], account))
    judged = set(pred)
    return {
        "intake_rows": len(rows), "mapped": matched, "unmapped": unmatched,
        "predicted_na": sorted(predicted_na),
        "agree": sorted(set(matched) & predicted_na),
        "intake_only": sorted(c for c in set(matched) - predicted_na if c in judged),
        "intake_not_judged_yet": sorted(set(matched) - judged),
        "ours_only": sorted(predicted_na - set(matched)),
    }


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_CSV
    with path.open(encoding="utf-8", newline="") as f:
        pred = {r["case_id"]: r["deletion_status"] for r in csv.DictReader(f)}
    x = crosscheck(pred)
    print(f"CLASSIFY_REQUEST rows: {x['intake_rows']}; mapped to an eval request: {len(x['mapped'])}; "
          f"unmapped: {len(x['unmapped'])}")
    print(f"predicted NOT_APPLICABLE: {len(x['predicted_na'])}")
    print(f"both say non-deletion: {len(x['agree'])} {x['agree']}")
    print(f"intake says non-deletion, we said deletion: {[(c, pred[c]) for c in x['intake_only']]}")
    print(f"we said NOT_APPLICABLE, no intake row: {x['ours_only']}")
    if x["unmapped"]:
        print(f"unmapped intake rows: {x['unmapped']}")


if __name__ == "__main__":
    main()
