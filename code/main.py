"""Ghost Data Still Exists - deletion-audit runner.

    python code/main.py                                    # agent mode (default)
    python code/main.py --mode baseline                    # no LLM
    python code/main.py --mode agent --workers 6           # Claude Haiku 5.5 tool-calling agent
    python code/main.py --mode agent --budget-usd 5        # stop launching cases at ~$5 estimated
    python code/main.py --mode agent --fresh               # ignore checkpoints, judge every case again

Reads dataset/, writes output.csv at the repository root (for --cases eval) and
per-case checkpoints + token usage under code/runs/<mode>/<cases>/.

Checkpointing: every case is saved (atomically) as soon as it is judged, under
code/runs/<mode>/<cases>/v-<version>/ where <version> hashes all judge code,
prompts, tool schemas and image transcriptions. A rerun of the same version
resumes: saved judgments are reused without any model call and only their
deterministic steps are rebuilt; only missing or failed cases are judged. Any
code or prompt change gives a new version and a fresh checkpoint directory, so
older results are never reused. A failed case never stops the run.
output.csv is written only once every case has a judgment.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deletion_audit.data import load_dataset                          # noqa: E402
from deletion_audit.evidence_index import build_index                 # noqa: E402
from deletion_audit.paths import CODE_DIR, EVALUATION_DIR, OUTPUT_CSV  # noqa: E402
from deletion_audit.pipeline import (MODES, CaseResult, finalize,     # noqa: E402
                                     judgment_from_dict, run_case)
from deletion_audit.validate import case_sort_key, validate_output    # noqa: E402

from pricing import usage_cost                                         # noqa: E402  (tier-aware, per call)

RUNS_DIR = CODE_DIR / "runs"


def select_requests(which: str) -> list[dict]:
    ds = load_dataset()
    if which == "eval":
        return ds.requests
    if which == "samples":
        return ds.sample_requests
    split = json.loads((EVALUATION_DIR / "split.json").read_text(encoding="utf-8"))
    if which in ("dev", "holdout"):
        keep = set(split[which])
        return [r for r in ds.sample_requests if r["case_id"] in keep]
    keep = set(which.split(","))
    return [r for r in ds.sample_requests + ds.requests if r["case_id"] in keep]


def judge_version(mode: str) -> str:
    """Fingerprint of everything that can change a judgment: the mode, every source
    file of the deletion_audit package (prompts, tool schemas and implementations,
    agent loop, model settings, gathering, policy) and the cached image
    transcriptions the index is built from. Checkpoints live in a directory named
    after it, so checkpoints from any other version are never read."""
    import deletion_audit
    from deletion_audit.paths import CACHE_DIR
    h = hashlib.sha256(mode.encode("utf-8"))
    files = sorted(Path(deletion_audit.__file__).parent.glob("*.py")) + sorted((CACHE_DIR / "image_text").glob("*.txt"))
    for path in files:
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def checkpoint_dir(mode: str, label: str, version: str | None = None) -> Path:
    """code/runs/<mode>/<label>/v-<version>/; without a version, the latest one run."""
    base = RUNS_DIR / mode / label
    if version is None:
        latest = base / "LATEST"
        version = latest.read_text(encoding="utf-8").strip() if latest.exists() else judge_version(mode)
    return base / f"v-{version}"


def _save(path: Path, payload: dict):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written checkpoint


def _load_checkpoint(path: Path, mode: str):
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if saved.get("mode") != mode or "judgment" not in saved:
        return None
    return saved


def run_cases(requests: list[dict], mode: str, workers: int = 1, label: str = "",
              fresh: bool = False, budget_usd: float | None = None) -> list[CaseResult]:
    build_index()  # build once before threads start
    label = label or "adhoc"
    version = judge_version(mode)
    out_dir = checkpoint_dir(mode, label, version)
    out_dir.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / mode / label / "LATEST").write_text(version, encoding="utf-8")
    print(f"[{mode}] judge version {version}; checkpoints in {out_dir}", file=sys.stderr)
    results: dict[str, CaseResult] = {}
    saved_usage: dict[str, list] = {}

    # ---- resume: reuse saved judgments (no model calls), rebuild their steps
    todo = []
    for req in requests:
        saved = None if fresh else _load_checkpoint(out_dir / f"{req['case_id']}.json", mode)
        if saved is None or saved.get("judge_version") != version:  # belt and braces
            todo.append(req)
            continue
        results[req["case_id"]] = finalize(req, judgment_from_dict(saved["judgment"]), saved.get("transcript"))
        saved_usage[req["case_id"]] = saved["judgment"].get("usage", [])
    if results:
        print(f"[{mode}] resumed {len(results)} checkpointed cases of this version (no model calls); "
              f"{len(todo)} to judge", file=sys.stderr)

    agent = None
    if mode == "agent" and todo:
        from deletion_audit.agent import AgentJudge
        agent = AgentJudge()
    started = time.time()
    spent = usage_cost([u for us in saved_usage.values() for u in us])
    failed: dict[str, str] = {}
    skipped_budget = 0

    if todo:
        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        pending = list(todo)
        running = {}
        try:
            while pending or running:
                while pending and len(running) < max(1, workers):
                    if budget_usd is not None and spent >= budget_usd:
                        skipped_budget = len(pending)
                        pending = []
                        break
                    req = pending.pop(0)
                    running[pool.submit(run_case, req, mode, agent)] = req
                if not running:
                    break
                fut = next(as_completed(running))
                req = running.pop(fut)
                case_id = req["case_id"]
                try:
                    res = fut.result()
                except Exception as e:  # leave the case pending for the next resume
                    failed[case_id] = f"{type(e).__name__}: {e}"
                    print(f"[{mode}] FAILED {case_id}: {failed[case_id]}", file=sys.stderr)
                    continue
                results[case_id] = res
                spent += usage_cost(res.judgment.usage)
                _save(out_dir / f"{case_id}.json", {
                    "case_id": case_id, "mode": mode, "judge_version": version,
                    "status": res.status, "steps": res.steps, "errors": res.errors,
                    "judgment": res.judgment.to_dict(), "transcript": res.transcript,
                })
                print(f"[{mode}] {len(results)}/{len(requests)} {case_id} {res.status} steps={len(res.steps)}"
                      + (f" errors={len(res.errors)}" if res.errors else "")
                      + (f" spent~${spent:.3f}" if mode == "agent" else ""), file=sys.stderr)
        except KeyboardInterrupt:
            print(f"[{mode}] interrupted; {len(results)} cases are checkpointed, rerun to resume",
                  file=sys.stderr)
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)

    usage = [u for r in results.values() for u in r.judgment.usage]
    with (out_dir / "usage.jsonl").open("w", encoding="utf-8") as f:
        for u in usage:
            f.write(json.dumps(u) + "\n")
    missing = [r["case_id"] for r in requests if r["case_id"] not in results]
    (out_dir / "run_meta.json").write_text(json.dumps({
        "mode": mode, "judge_version": version, "cases": len(requests), "completed": len(results),
        "resumed": len(saved_usage), "failed": failed, "skipped_for_budget": skipped_budget,
        "seconds_this_session": round(time.time() - started, 1), "model_calls": len(usage),
        "estimated_cost_usd": round(usage_cost(usage), 4)}, indent=1), encoding="utf-8")
    if missing:
        raise SystemExit(f"[{mode}] {len(missing)} cases not judged yet ({len(failed)} failed, "
                         f"{skipped_budget} skipped for budget); rerun the same command to resume. "
                         f"No output written.")
    return [results[r["case_id"]] for r in sorted(requests, key=lambda r: case_sort_key(r["case_id"]))]


def write_csv(results: list[CaseResult], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in results:
            w.writerow([r.case_id, r.status, r.steps_json()])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=MODES, default="agent", help="judgment mode (default: agent)")
    p.add_argument("--cases", default="eval",
                   help="eval (default, writes output.csv) | samples | dev | holdout | comma-separated case ids")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--output", type=Path, default=None, help="CSV path (default: output.csv for eval)")
    p.add_argument("--fresh", action="store_true", help="ignore checkpoints and judge every case again")
    p.add_argument("--budget-usd", type=float, default=None,
                   help="stop launching new cases once estimated spend (incl. resumed cases) reaches this")
    args = p.parse_args()

    requests = select_requests(args.cases)
    if not requests:
        sys.exit(f"no cases selected for {args.cases!r}")
    label = args.cases if args.cases in ("eval", "samples", "dev", "holdout") else "adhoc"
    results = run_cases(requests, args.mode, args.workers, label, fresh=args.fresh, budget_usd=args.budget_usd)
    output = args.output or (OUTPUT_CSV if args.cases == "eval"
                             else checkpoint_dir(args.mode, label) / "output.csv")
    write_csv(results, output)
    rows = [{"case_id": r.case_id, "deletion_status": r.status, "steps_json": r.steps_json()} for r in results]
    errors = validate_output(rows, [r["case_id"] for r in requests])
    print(f"wrote {len(rows)} rows to {output}; contract errors: {len(errors)}", file=sys.stderr)
    for e in errors[:20]:
        print("  " + e, file=sys.stderr)


if __name__ == "__main__":
    main()
