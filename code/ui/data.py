"""Read-only data access for the results UI.

Reads saved run checkpoints (code/runs/), saved scores (code/evaluation/results/)
and the dataset. It never imports the agent, never calls a model and never runs
the pipeline. The deterministic dataset readers from deletion_audit (evidence
index, identity resolver, face-value observation) are used read-only; this
directory is outside deletion_audit/, so it does not affect the checkpoint
version hash.
"""

import csv
import json
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
sys.path.append(str(CODE / "evaluation"))

from deletion_audit.data import load_dataset, parse_ts                # noqa: E402
from deletion_audit.evidence_index import build_index                 # noqa: E402
from deletion_audit.identifiers import email_hash                     # noqa: E402
from deletion_audit.identity import resolver                          # noqa: E402
from deletion_audit.observe import observe                            # noqa: E402
from deletion_audit.paths import EVALUATION_DIR, REPO_ROOT            # noqa: E402

RUNS = CODE / "runs"
RESULTS = EVALUATION_DIR / "results"
VARIANCE = RESULTS / "variance"
SPLIT = json.loads((EVALUATION_DIR / "split.json").read_text(encoding="utf-8"))
SETS = ("eval", "dev", "custom")   # holdout is deliberately not inspectable (protocol)


def run_dir(mode: str, label: str) -> Path | None:
    """Latest versioned checkpoint dir for a run label, else the pre-versioning dir."""
    base = RUNS / mode / label
    latest = base / "LATEST"
    if latest.exists():
        d = base / f"v-{latest.read_text(encoding='utf-8').strip()}"
        if d.exists():
            return d
    return base if base.exists() else None


def version_of(mode: str, label: str) -> str:
    d = run_dir(mode, label)
    return d.name[2:] if d and d.name.startswith("v-") else "unversioned"


def custom_cases() -> list[dict]:
    path = RESULTS / "custom_cases.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def requests_for(set_name: str) -> list[dict]:
    ds = load_dataset()
    if set_name == "eval":
        return ds.requests
    if set_name == "dev":
        keep = set(SPLIT["dev"])
        return [r for r in ds.sample_requests if r["case_id"] in keep]
    if set_name == "custom":
        return [c["request"] for c in custom_cases()]
    raise ValueError(set_name)


def gold(set_name: str, case_id: str):
    """(status, steps) for dev and custom cases; None otherwise (never for holdout)."""
    if set_name == "dev":
        for r in load_dataset().sample_output:
            if r["case_id"] == case_id:
                return r["deletion_status"], json.loads(r["steps_json"])
    if set_name == "custom":
        for c in custom_cases():
            if c["request"]["case_id"] == case_id:
                return c["expected_status"], c["expected_steps"]
    return None


def checkpoint(mode: str, set_name: str, case_id: str) -> dict | None:
    d = run_dir(mode, set_name)
    path = d / f"{case_id}.json" if d else None
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None   # being written


def all_checkpoints(mode: str, set_name: str) -> dict[str, dict]:
    out = {}
    d = run_dir(mode, set_name)
    if not d:
        return out
    for path in d.glob("case_*.json"):
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    for path in d.glob("custom_*.json"):
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return out


# ------------------------------------------------------------------ identities
def identifier_rows(email: str) -> tuple[list[dict], object]:
    ds, ids = load_dataset(), resolver()
    subject = ids.resolve(email)
    rows = []
    for r in ds.identities:
        if r["user_id"] in subject.user_ids:
            for field in ("name", "email", "phone", "email_hash"):
                rows.append({"user_id": r["user_id"], "field": field, "value": r[field],
                             "kind": "current", "source": f"user_identities (updated {r['updated_at']})"})
    for a in ds.audit_log:
        if a["user_id"] in subject.user_ids:
            rows.append({"user_id": a["user_id"], "field": a["field_name"], "value": a["old_value"],
                         "kind": "historical", "source": f"{a['audit_id']} (changed {a['event_time']})"})
            if a["field_name"] == "email":
                rows.append({"user_id": a["user_id"], "field": "email_hash (derived)",
                             "value": email_hash(a["old_value"]), "kind": "historical",
                             "source": f"sha256 of {a['audit_id']} old email"})
    if not subject.resolved:
        rows.append({"user_id": "-", "field": "email", "value": email, "kind": "request only",
                     "source": "matches no current or historical identity"})
    return rows, subject


# ------------------------------------------------------------------ timeline
def timeline(request: dict, subject, resources: set[str], limit: int = 400) -> list[dict]:
    """Evidence records and activity up to audit_at that mention the subject's
    identifiers or the case's resources."""
    index = build_index()
    audit_at = parse_ts(request["audit_at"])
    requested_at = parse_ts(request["requested_at"])
    seen, rows = set(), []
    pool = [rec for tok in subject.tokens for rec in index.by_token.get(tok, [])]
    pool += [rec for rid in resources for rec in index.by_resource.get(rid, [])]
    for rec in pool:
        if rec.record_id in seen or rec.captured_at > audit_at:
            continue
        seen.add(rec.record_id)
        hints = []
        for rid in rec.resource_ids[:6]:
            obs = observe(rec, rid)
            if obs:
                hints.append(f"{rid}: {obs.status}" + (" (claim)" if obs.claim else ""))
        text = rec.text()
        rows.append({
            "time": rec.captured_at.strftime("%Y-%m-%d %H:%M"),
            "episode": "in episode" if rec.captured_at >= requested_at else "before request",
            "evidence_id": rec.evidence_id,
            "kind": rec.kind,
            "source": "system_activity" if rec.kind == "activity" else index.files[rec.evidence_id]["file_path"],
            "locator": rec.locator,
            "resources": ", ".join(rec.resource_ids[:6]),
            "face-value state": "; ".join(hints),
            "content": text if len(text) < 400 else text[:400] + " …",
        })
    rows.sort(key=lambda r: (r["time"], r["evidence_id"]))
    return rows[-limit:]


# ------------------------------------------------------------------ step comparison
STEP_FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def compare_steps(pred: list[dict], expected: list[dict]) -> list[dict]:
    """Union of predicted and expected steps keyed by (phase, system, resource, operation)."""
    key = lambda s: (s["phase"], s["system_id"], s["resource_id"], s["operation"])
    p = {key(s): s for s in pred}
    e = {key(s): s for s in expected}
    order = [key(s) for s in expected] + [k for k in (key(s) for s in pred) if k not in e]
    rows = []
    for k in order:
        ps, es = p.get(k), e.get(k)
        if ps and es:
            verdict = "match" if ps["evidence_id"] == es["evidence_id"] and ps["state"] == es["state"] else "evidence/state differs"
        elif es:
            verdict = "missing (expected only)"
        else:
            verdict = "extra (predicted only)"
        rows.append({"result": verdict, "phase": k[0], "system_id": k[1], "resource_id": k[2], "operation": k[3],
                     "expected evidence": es["evidence_id"] if es else "", "predicted evidence": ps["evidence_id"] if ps else "",
                     "expected #": str(es["step_no"]) if es else "", "predicted #": str(ps["step_no"]) if ps else ""})
    return rows


# ------------------------------------------------------------------ scores
def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def score_files() -> dict[str, Path]:
    files = {}
    for p in sorted(VARIANCE.glob("*.json")):
        files[p.stem] = p
    for p in sorted(RESULTS.glob("*_scores.json")):
        files[f"latest {p.stem}"] = p
    return files


def holdout_m1_markdown() -> str | None:
    p = RUNS / "holdout_m1_aggregate.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def output_csv_statuses() -> dict[str, str]:
    p = REPO_ROOT / "output.csv"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8", newline="") as f:
        return {r["case_id"]: r["deletion_status"] for r in csv.DictReader(f)}


def usage_totals(checkpoints: dict[str, dict]) -> dict:
    """Token totals and tier-aware estimated cost (code/pricing.py, priced per call)."""
    from pricing import KEYS, call_cost, is_long
    tot = {k: 0 for k in KEYS}
    calls = long_calls = 0
    cost_usd = 0.0
    for c in checkpoints.values():
        for u in c["judgment"].get("usage", []):
            calls += 1
            long_calls += is_long(u)
            cost_usd += call_cost(u)
            for k in KEYS:
                tot[k] += u.get(k, 0) or 0
    tot.update(calls=calls, long_prompt_calls=long_calls, cost_usd=cost_usd)
    return tot
