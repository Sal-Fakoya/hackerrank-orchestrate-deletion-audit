"""Score baseline and agent modes against the public sample answers.

    python code/evaluation/score.py --set dev                       # both modes, per-case detail
    python code/evaluation/score.py --set dev --modes baseline      # one mode
    python code/evaluation/score.py --set dev --reuse               # rescore last runs, no new calls
    python code/evaluation/score.py --set dev --rebuild             # re-derive steps from saved judgments, no new calls
    python code/evaluation/score.py --set holdout --milestone       # aggregates only

Metrics (micro-averaged over cases):
    status accuracy     deletion_status exact match
    step P / R / F1     multiset match on (phase, system_id, resource_id, operation, state, evidence_id)
    step F1 (no evid.)  same, ignoring evidence_id
    field accuracy      steps aligned by position; per field, matches / max(len(pred), len(gold))
    sequence accuracy   the whole steps_json array is identical (order included)
    resource P / R      sets of resource_ids touched by any step
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deletion_audit.data import load_dataset                  # noqa: E402
from deletion_audit.paths import EVALUATION_DIR               # noqa: E402
from deletion_audit.pipeline import finalize, judgment_from_dict  # noqa: E402
from main import RUNS_DIR, checkpoint_dir, run_cases, select_requests, write_csv  # noqa: E402


def saved_run_dir(mode: str, label: str) -> Path:
    """Latest versioned checkpoint dir; falls back to pre-versioning runs (read-only use)."""
    d = checkpoint_dir(mode, label)
    return d if d.exists() else RUNS_DIR / mode / label

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
RESULTS_DIR = EVALUATION_DIR / "results"


def _key(step, with_evidence=True):
    return tuple(step[f] for f in FIELDS if with_evidence or f != "evidence_id")


def _prf(tp, n_pred, n_gold):
    p = tp / n_pred if n_pred else 1.0
    r = tp / n_gold if n_gold else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def score(pred: dict[str, tuple[str, list]], gold: dict[str, tuple[str, list]]) -> dict:
    agg = Counter()
    per_case = []
    for case_id, (g_status, g_steps) in gold.items():
        p_status, p_steps = pred[case_id]
        tp = sum((Counter(map(_key, p_steps)) & Counter(map(_key, g_steps))).values())
        tp_ne = sum((Counter(_key(s, False) for s in p_steps) & Counter(_key(s, False) for s in g_steps)).values())
        n = max(len(p_steps), len(g_steps))
        field_hits = {f: sum(1 for a, b in zip(p_steps, g_steps) if a[f] == b[f]) for f in FIELDS}
        p_res, g_res = {s["resource_id"] for s in p_steps}, {s["resource_id"] for s in g_steps}
        agg.update(status=p_status == g_status, exact=p_steps == g_steps and p_status == g_status,
                   tp=tp, tp_ne=tp_ne, pred=len(p_steps), gold=len(g_steps), slots=n,
                   res_tp=len(p_res & g_res), res_pred=len(p_res), res_gold=len(g_res),
                   **{f"field_{f}": v for f, v in field_hits.items()})
        per_case.append({"case_id": case_id, "gold": g_status, "pred": p_status, "gold_steps": len(g_steps),
                         "pred_steps": len(p_steps), "step_f1": _prf(tp, len(p_steps), len(g_steps))[2],
                         "exact": p_steps == g_steps and p_status == g_status,
                         "missing": sorted(g_res - p_res), "extra": sorted(p_res - g_res)})
    n_cases = len(gold)
    p, r, f = _prf(agg["tp"], agg["pred"], agg["gold"])
    _, _, f_ne = _prf(agg["tp_ne"], agg["pred"], agg["gold"])
    rp, rr, _ = _prf(agg["res_tp"], agg["res_pred"], agg["res_gold"])
    return {
        "cases": n_cases,
        "status_accuracy": agg["status"] / n_cases,
        "step_precision": p, "step_recall": r, "step_f1": f, "step_f1_no_evidence": f_ne,
        "field_accuracy": {fld: agg[f"field_{fld}"] / agg["slots"] if agg["slots"] else 1.0 for fld in FIELDS},
        "sequence_accuracy": agg["exact"] / n_cases,
        "resource_precision": rp, "resource_recall": rr,
        "per_case": per_case,
    }


def load_gold(case_ids) -> dict:
    rows = {r["case_id"]: r for r in load_dataset().sample_output}
    return {c: (rows[c]["deletion_status"], json.loads(rows[c]["steps_json"])) for c in case_ids}


def load_pred(path: Path) -> dict:
    with path.open(encoding="utf-8", newline="") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def rebuild(requests: list[dict], mode: str, label: str, path: Path):
    """Re-run the deterministic tail (steps, exceptions, validation) on saved judgments."""
    results = []
    for req in requests:
        saved = json.loads((saved_run_dir(mode, label) / f"{req['case_id']}.json").read_text(encoding="utf-8"))
        results.append(finalize(req, judgment_from_dict(saved["judgment"]), saved.get("transcript")))
    write_csv(results, path)


def usage_summary(mode: str, label: str) -> dict:
    path = saved_run_dir(mode, label) / "usage.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    keys = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
    return {"calls": len(rows), **{k: sum(r[k] for r in rows) for k in keys}}


def render(set_name: str, results: dict[str, dict], usage: dict[str, dict], detail: bool) -> str:
    modes = list(results)
    out = [f"## {set_name} comparison ({results[modes[0]]['cases']} cases)", "",
           "| metric | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]

    def row(name, fn):
        out.append(f"| {name} | " + " | ".join(fn(results[m]) for m in modes) + " |")

    pct = lambda x: f"{x:.1%}"
    row("status accuracy", lambda s: pct(s["status_accuracy"]))
    row("step precision", lambda s: pct(s["step_precision"]))
    row("step recall", lambda s: pct(s["step_recall"]))
    row("step F1", lambda s: pct(s["step_f1"]))
    row("step F1 ignoring evidence_id", lambda s: pct(s["step_f1_no_evidence"]))
    for fld in FIELDS:
        row(f"field accuracy: {fld}", lambda s, fld=fld: pct(s["field_accuracy"][fld]))
    row("sequence accuracy (exact)", lambda s: pct(s["sequence_accuracy"]))
    row("resource precision", lambda s: pct(s["resource_precision"]))
    row("resource recall", lambda s: pct(s["resource_recall"]))
    row("model calls", lambda s: str(usage.get(modes[[results[m] for m in modes].index(s)], {}).get("calls", 0)))
    if detail:
        out += ["", "### Per case", "",
                "| case | gold | " + " | ".join(f"{m} status | {m} F1 | {m} exact" for m in modes) + " |",
                "|---|---|" + "---|---|---|" * len(modes)]
        by_case = {m: {c["case_id"]: c for c in results[m]["per_case"]} for m in modes}
        for case_id in by_case[modes[0]]:
            gold = by_case[modes[0]][case_id]["gold"]
            cells = []
            for m in modes:
                c = by_case[m][case_id]
                cells.append(f"{c['pred']} | {c['step_f1']:.2f} | {'yes' if c['exact'] else 'no'}")
            out.append(f"| {case_id} | {gold} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["dev", "holdout"], default="dev")
    ap.add_argument("--modes", nargs="+", default=["baseline", "agent"])
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--reuse", action="store_true", help="score the last saved run instead of re-running")
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild steps from the last saved judgments (no model calls), then score")
    ap.add_argument("--milestone", action="store_true", help="required to run the holdout set")
    args = ap.parse_args()
    if args.set == "holdout" and not args.milestone:
        sys.exit("holdout runs only at milestones: pass --milestone (aggregates only)")

    requests = select_requests(args.set)
    gold = load_gold([r["case_id"] for r in requests])
    RESULTS_DIR.mkdir(exist_ok=True)
    results, usage = {}, {}
    for mode in args.modes:
        if args.rebuild or args.reuse:
            path = saved_run_dir(mode, args.set) / "output.csv"
        else:
            path = None
        if args.rebuild:
            rebuild(requests, mode, args.set, path)
        elif not args.reuse:
            # a scoring run measures the current judge, so it never reuses checkpoints
            results_ = run_cases(requests, mode, args.workers, args.set, fresh=True)
            path = checkpoint_dir(mode, args.set) / "output.csv"
            write_csv(results_, path)
        results[mode] = score(load_pred(path), gold)
        usage[mode] = usage_summary(mode, args.set)
    text = render(args.set, results, usage, detail=args.set == "dev")
    (RESULTS_DIR / f"{args.set}_comparison.md").write_text(text, encoding="utf-8")
    (RESULTS_DIR / f"{args.set}_scores.json").write_text(json.dumps(
        {m: {k: v for k, v in s.items() if args.set == "dev" or k != "per_case"} for m, s in results.items()}
        | {"usage": usage}, indent=1), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
