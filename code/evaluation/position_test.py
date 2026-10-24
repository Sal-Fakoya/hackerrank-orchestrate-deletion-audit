"""Shared-run position test (dev gold only, no model calls, no pipeline changes).

Question: inside a deletion-worker run shared by several requests, does a record's
position identify the request it belongs to?

1. Order consistency: for every pair of record types in a shared run where dev gold
   labels the owners of >= 2 records of both types, is the owners' order the same?
2. Rank attribution: for each candidate ordering of the run's member requests, assign
   the i-th record of each type to the i-th request and measure accuracy against dev
   gold owners.
3. Dev impact: add the best ordering's attributions for dev cases to the saved agent
   run (final version, run A) and rescore. The simulation lives here, not in
   deletion_audit/.

    python code/evaluation/position_test.py
"""

import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from deletion_audit.data import load_dataset, parse_ts                         # noqa: E402
from deletion_audit.evidence_index import build_index                          # noqa: E402
from deletion_audit.gather import _all_requests, _case_run, gather             # noqa: E402
from deletion_audit.judge_baseline import PREFIX_CATEGORY, latest_observation, to_state  # noqa: E402
from deletion_audit.pipeline import enforce_attribution, finalize, judgment_from_dict   # noqa: E402
from deletion_audit.policy import Fact, Judgment                               # noqa: E402
from deletion_audit.paths import EVALUATION_DIR                                # noqa: E402
from score import load_gold, score                                             # noqa: E402

RUN_B = Path(__file__).resolve().parents[1] / "runs" / "agent" / "dev" / "v-74a13b21acb6"


def shared_runs(index):
    """run (tuple of activity record ids, in time order) -> member requests."""
    runs = defaultdict(list)
    for req in _all_requests():
        run = _case_run(index, req)
        if run:
            runs[tuple(r.record_id for r in run)].append(req)
    merged = defaultdict(set)   # merge overlapping runs by their event sets
    for recs, members in runs.items():
        merged[recs] |= {m["case_id"] for m in members}
    return {k: v for k, v in merged.items() if len(v) >= 2}


def main():
    ds, index = load_dataset(), build_index()
    split = json.loads((EVALUATION_DIR / "split.json").read_text(encoding="utf-8"))
    dev = set(split["dev"])
    gold_rows = {r["case_id"]: r for r in ds.sample_output if r["case_id"] in dev}
    owner = {}
    for cid, row in gold_rows.items():
        for s in json.loads(row["steps_json"]):
            owner[s["resource_id"]] = cid
    reqs = {r["case_id"]: r for r in _all_requests()}
    by_id = {r.record_id: r for r in index.records}
    out = ["# Shared-run position test (dev gold only)", ""]

    # ---------------------------------------------------------------- 1. order consistency
    pairs_same = pairs_diff = 0
    examples = []
    runs = shared_runs(index)
    for recs, members in runs.items():
        typed = defaultdict(list)
        for rid in recs:
            res = by_id[rid].content["resource_id"]
            if res in owner:
                typed[res.rsplit("_", 1)[0]].append(owner[res])
        for (t1, o1), (t2, o2) in combinations(typed.items(), 2):
            common = [c for c in o1 if c in o2]
            if len(common) < 2:
                continue
            order2 = [c for c in o2 if c in common]
            if [c for c in o1 if c in common] == order2:
                pairs_same += 1
            else:
                pairs_diff += 1
                examples.append(f"{t1} owners {o1} vs {t2} owners {o2}")
    out += ["## 1. Owner order across record types within a shared run", "",
            f"Type pairs with >= 2 dev-labelled owners in common: {pairs_same + pairs_diff}; "
            f"same order: {pairs_same}; different order: {pairs_diff}.", ""]
    out += [f"- {e}" for e in examples] + [""]

    # ---------------------------------------------------------------- 2. rank attribution
    orderings = {
        "requested_at": lambda m: sorted(m, key=lambda c: (reqs[c]["requested_at"], c)),
        "case number": lambda m: sorted(m, key=lambda c: int(c.split("_")[1])),
        "request email": lambda m: sorted(m, key=lambda c: reqs[c]["email"].lower()),
        "audit_at": lambda m: sorted(m, key=lambda c: (reqs[c]["audit_at"], c)),
    }
    results = {}
    attributions = {}
    for name, order in orderings.items():
        correct = wrong = 0
        attr = defaultdict(list)
        for recs, members in runs.items():
            ordered = order(members)
            typed = defaultdict(list)
            for rid in recs:
                res = by_id[rid].content["resource_id"]
                typed[res.rsplit("_", 1)[0]].append((res, rid))
            for t, items in typed.items():
                for i, (res, rid) in enumerate(items):
                    if i >= len(ordered):
                        break
                    guess = ordered[i]
                    attr[guess].append((res, rid))
                    if res in owner:          # gold owner known (dev)
                        correct += owner[res] == guess
                        wrong += owner[res] != guess
        results[name] = (correct, wrong)
        attributions[name] = attr
    out += ["## 2. Rank attribution: i-th record of a type -> i-th member request", "",
            "| ordering of member requests | correct | wrong | accuracy |", "|---|---|---|---|"]
    for name, (c, w) in results.items():
        out.append(f"| {name} | {c} | {w} | {c / (c + w):.0%} |" if c + w else f"| {name} | 0 | 0 | - |")
    best = max(results, key=lambda n: results[n][0] / max(1, sum(results[n])))
    out += ["", f"Best ordering: **{best}**.", ""]

    # ---------------------------------------------------------------- 3. dev impact
    gold = load_gold(sorted(dev, key=lambda c: int(c.split("_")[1])))
    base_pred, sim_pred, added_log = {}, {}, []
    for cid in gold:
        req = ds.request(cid)
        saved = json.loads((RUN_B / f"{cid}.json").read_text(encoding="utf-8"))
        base = finalize(req, judgment_from_dict(saved["judgment"]))
        base_pred[cid] = (base.status, base.steps)
        if not saved["judgment"]["deletion_requested"]:
            sim_pred[cid] = base_pred[cid]
            continue
        j = judgment_from_dict(saved["judgment"])
        facts = enforce_attribution(req, j, index)
        have = {f.resource_id for f in facts}
        g = gather(req, index=index)
        for res, rid in attributions[best].get(cid, []):
            if res in have:
                continue
            rec, obs = latest_observation(index, g, res)
            cat = PREFIX_CATEGORY.get(res.rsplit("_", 1)[0])
            state = to_state(obs, cat) if (obs and cat) else None
            if state and index.system_of(res):
                facts.append(Fact(res, index.system_of(res), cat, state, rec.evidence_id, "position attribution"))
                have.add(res)
                added_log.append((cid, res, owner.get(res, "?")))
        sim = Judgment(cid, "position_sim", True, facts=facts)  # not "agent": rule A must not re-drop them
        res_ = finalize(req, sim)
        sim_pred[cid] = (res_.status, res_.steps)
    s0, s1 = score(base_pred, gold), score(sim_pred, gold)
    out += ["## 3. Dev impact (latest saved agent dev run of the final version = run B, rescored; no model calls)", "",
            "| metric | current (reject identity-free shared-run records) | with position attribution |",
            "|---|---|---|"]
    for k, label in [("status_accuracy", "status accuracy"), ("step_precision", "step precision"),
                     ("step_recall", "step recall"), ("step_f1", "step F1"),
                     ("sequence_accuracy", "exact sequence"), ("resource_precision", "resource precision"),
                     ("resource_recall", "resource recall")]:
        out.append(f"| {label} | {s0[k]:.1%} | {s1[k]:.1%} |")
    ok = sum(1 for c, r, o in added_log if o == c)
    out += ["", f"Records added by position attribution to dev cases: {len(added_log)} "
                f"({ok} owned by that case per dev gold, {len(added_log) - ok} not):", ""]
    out += [f"- {c}: {r} (gold owner: {o if o != '?' else 'not in any dev answer'})" for c, r, o in added_log]
    text = "\n".join(out) + "\n"
    (EVALUATION_DIR / "results" / "position_test.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
