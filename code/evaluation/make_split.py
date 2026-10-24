"""Create the fixed, stratified dev/holdout split of the 32 public sample cases.

Run once; the result is committed as split.json. Re-running with the same seed
reproduces the same split.

    python code/evaluation/make_split.py
"""

import csv
import json
import random
from pathlib import Path

SEED = 20261024
FORCED_DEV = ["case_05", "case_11", "case_12", "case_26"]  # already studied in exploration
DEV_QUOTA = {"INCOMPLETE": 14, "COMPLETE": 6}

REPO = Path(__file__).resolve().parents[2]
SAMPLE_OUTPUT = REPO / "dataset" / "sample_output.csv"
SPLIT_FILE = Path(__file__).resolve().parent / "split.json"


def main() -> None:
    with SAMPLE_OUTPUT.open(encoding="utf-8", newline="") as f:
        status = {r["case_id"]: r["deletion_status"] for r in csv.DictReader(f)}

    rng = random.Random(SEED)
    dev, holdout = [], []
    for stratum in sorted(DEV_QUOTA):
        cases = sorted(c for c, s in status.items() if s == stratum)
        forced = [c for c in FORCED_DEV if c in cases]
        pool = [c for c in cases if c not in forced]
        rng.shuffle(pool)
        need = DEV_QUOTA[stratum] - len(forced)
        dev += forced + pool[:need]
        holdout += pool[need:]

    key = lambda c: int(c.split("_")[1])
    split = {
        "seed": SEED,
        "method": "per-status stratified; forced dev cases first, rest shuffled with random.Random(seed) over sorted case_ids",
        "forced_dev": FORCED_DEV,
        "dev": sorted(dev, key=key),
        "holdout": sorted(holdout, key=key),
        "counts": {
            "dev": {s: sum(status[c] == s for c in dev) for s in DEV_QUOTA},
            "holdout": {s: sum(status[c] == s for c in holdout) for s in DEV_QUOTA},
        },
    }
    SPLIT_FILE.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(split, indent=2))


if __name__ == "__main__":
    main()
