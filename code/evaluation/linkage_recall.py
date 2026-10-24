"""Linkage experiment: does deterministic gathering recover every resource that
appears in the expected steps_json? Dev cases only.

Compares three gathering variants:
    no_batch  direct + name + resource_link
    window    no_batch + deletion-worker activity anywhere in request -> report
    run       no_batch + only the deletion-worker run whose result was reported

    python code/evaluation/linkage_recall.py     -> prints + writes linkage_recall.md
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deletion_audit.data import load_dataset                   # noqa: E402
from deletion_audit.evidence_index import build_index          # noqa: E402
from deletion_audit.gather import gather                       # noqa: E402
from deletion_audit.paths import EVALUATION_DIR                # noqa: E402

SPLIT = json.loads((EVALUATION_DIR / "split.json").read_text(encoding="utf-8"))
BASE = ("direct", "name", "resource_link")
VARIANTS = {"no_batch": BASE, "window": BASE + ("batch_window",), "run": BASE + ("batch_run",)}


def expected_resources(row) -> dict[str, str]:
    return {s["resource_id"]: s["system_id"] for s in json.loads(row["steps_json"])}


def diagnose_miss(rid, audit_at, index) -> str:
    recs = index.by_resource.get(rid, [])
    if not recs:
        return "appears in no participant file or activity row"
    pre = [r for r in recs if r.captured_at <= audit_at]
    if not pre:
        later = sorted({r.evidence_id for r in recs})
        return f"only in evidence captured after audit_at ({', '.join(later[:4])})"
    cites = sorted({r.evidence_id for r in pre})
    return f"present pre-audit but not linked by any channel ({', '.join(cites[:5])})"


def main():
    ds, index = load_dataset(), build_index()
    dev = SPLIT["dev"]
    outputs = {r["case_id"]: r for r in ds.sample_output if r["case_id"] in dev}
    head = ("| case | status | exp | " + " | ".join(f"recall {v} | cand {v}" for v in VARIANTS)
            + " | channels per expected resource (run variant) |")
    lines = [head, "|" + "---|" * (head.count("|") - 1)]
    misses, totals = [], Counter()
    noise_by_channel = {v: Counter() for v in VARIANTS}
    for case_id in dev:
        req, out = ds.request(case_id), outputs[case_id]
        exp = expected_resources(out)
        results = {v: gather(req, channels=ch, index=index) for v, ch in VARIANTS.items()}
        cells = []
        for v, g in results.items():
            found = sum(r in g.candidates for r in exp)
            noise = [c for r, c in g.candidates.items() if r not in exp]
            for c in noise:
                noise_by_channel[v]["+".join(sorted(c.channels))] += 1
            totals[f"found_{v}"] += found
            totals[f"cand_{v}"] += len(g.candidates)
            totals[f"noise_{v}"] += len(noise)
            cells.append(f"{found}/{len(exp)} | {len(g.candidates)}")
        totals["expected"] += len(exp)
        per, g_run = [], results["run"]
        for rid in exp:
            cand = g_run.candidates.get(rid)
            if cand:
                tag = ",".join(sorted(cand.channels))
                if cand.system_id != exp[rid]:
                    tag += f" (system {cand.system_id} vs {exp[rid]})"
                per.append(f"{rid}:{tag}")
            else:
                per.append(f"**{rid}:MISS**")
                misses.append((case_id, exp[rid], rid, diagnose_miss(rid, g_run.audit_at, index)))
        lines.append(f"| {case_id} | {out['deletion_status']} | {len(exp)} | {' | '.join(cells)} | {'; '.join(per)} |")
    n = totals["expected"]
    lines.append(f"| **total** | | {n} | " + " | ".join(
        f"{totals[f'found_{v}']}/{n} ({totals[f'found_{v}'] / n:.0%}) | {totals[f'cand_{v}']}" for v in VARIANTS) + " | |")

    summary = ["| variant | recall | candidates | unexpected candidates | precision |", "|---|---|---|---|---|"]
    for v in VARIANTS:
        f, c = totals[f"found_{v}"], totals[f"cand_{v}"]
        summary.append(f"| {v} | {f}/{n} ({f / n:.0%}) | {c} | {totals[f'noise_{v}']} | {f / c:.0%} |")

    report = ["# Linkage recall experiment (dev cases only)", "",
              "Gathering uses only evidence with manifest `captured_at <= audit_at` and activity with "
              "`event_time <= audit_at`. Expected resources = every `resource_id` in the sample "
              "`steps_json` (findings, remediation and verification).", "",
              "## Summary", "", *summary, "", "## Per case", "", *lines, "",
              "## Misses (run variant)", "",
              "| case | system | resource | diagnosis |", "|---|---|---|---|",
              *[f"| {c} | {s} | {r} | {d} |" for c, s, r, d in misses], "",
              "## Unexpected candidates by linking channel(s)", ""]
    for v in VARIANTS:
        report += [f"**{v}:** " + ", ".join(f"{k} {cnt}" for k, cnt in noise_by_channel[v].most_common()), ""]
    text = "\n".join(report)
    (EVALUATION_DIR / "linkage_recall.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
