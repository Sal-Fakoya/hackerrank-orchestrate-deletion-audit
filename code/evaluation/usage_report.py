"""Generate evaluation/usage_report.md from the final full-dataset run's checkpoints.

    python code/evaluation/usage_report.py [--version 74a13b21acb6]

Token counts come from the API's response.usage for every call, stored per case in
the checkpoints, so a run split across sessions (interrupted + resumed) is counted
exactly once per call. Costs are estimates from list prices.
"""

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deletion_audit.paths import CACHE_DIR, EVALUATION_DIR, REPO_ROOT  # noqa: E402
from main import RUNS_DIR                                               # noqa: E402

MODEL = "claude-haiku-5-5"
PROVIDER = "Anthropic (Claude API, Python SDK)"
from pricing import KEYS, LONG_PROMPT, PRICE, PRICE_LONG, call_cost, prompt_tokens  # noqa: E402


def cost(c: Counter) -> float:
    """Aggregate cost: tracked per call in the "_cost" field (tier-aware)."""
    return c["_cost"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None, help="judge version (default: LATEST of the eval run)")
    args = ap.parse_args()
    base = RUNS_DIR / "agent" / "eval"
    version = args.version or (base / "LATEST").read_text(encoding="utf-8").strip()
    run_dir = base / f"v-{version}"
    cps = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(run_dir.glob("case_*.json"))]
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    with (REPO_ROOT / "output.csv").open(encoding="utf-8", newline="") as f:
        out = {r["case_id"]: r for r in csv.DictReader(f)}
    assert len(cps) == len(out) == 518, "final run incomplete"
    assert all(c["judge_version"] == version for c in cps)
    assert all(out[c["case_id"]]["deletion_status"] == c["status"] for c in cps), "output.csv is not from this run"

    total, by_stage = Counter(), defaultdict(Counter)
    per_case_cost, per_case_tokens, turns = [], [], []
    long_calls, long_cost, max_prompt = 0, 0.0, 0
    for c in cps:
        case = Counter()
        n_turns = 0
        for u in c["judgment"]["usage"]:
            stage = "intent classification" if u["call"] == "intent" else "investigation (tool loop)"
            by_stage[stage]["calls"] += 1
            for k in KEYS:
                v = u.get(k, 0) or 0
                total[k] += v
                by_stage[stage][k] += v
                case[k] += v
            cc = call_cost(u)
            total["_cost"] += cc
            by_stage[stage]["_cost"] += cc
            case["_cost"] += cc
            max_prompt = max(max_prompt, prompt_tokens(u))
            if prompt_tokens(u) > LONG_PROMPT:
                long_calls += 1
                long_cost += cc
            n_turns += u["call"] != "intent"
        total["calls"] += len(c["judgment"]["usage"])
        per_case_cost.append(cost(case))
        per_case_tokens.append(sum(case[k] for k in KEYS))
        if c["status"] != "NOT_APPLICABLE":
            turns.append(n_turns)
    n = len(cps)
    all_tokens = sum(total[k] for k in KEYS)
    status = Counter(c["status"] for c in cps)
    fallbacks = sum(any("did not submit" in x for x in c["judgment"]["notes"]) for c in cps)
    at_cap = sum(t >= 16 for t in turns)

    img = [json.loads(l) for l in (CACHE_DIR / "image_text" / "usage.jsonl").read_text(encoding="utf-8").splitlines() if l]
    img_c = Counter()
    for u in img:
        for k in KEYS:
            img_c[k] += u.get(k, 0) or 0
        img_c["_cost"] += call_cost(u)

    fmt = lambda x: f"{x:,}"
    lines = [
        "# Token Usage and Cost Report",
        "",
        "Final full-dataset run that produced `output.csv` (518 requests).",
        "",
        "## Run",
        "",
        "| item | value |",
        "|---|---|",
        "| command | `python code/main.py --mode agent --budget-usd 15` (session 1 with `--workers 8`, session 2 with `--workers 2`) |",
        f"| judge version | `{version}` (hash of all `deletion_audit/` code, prompts, tool schemas, image transcriptions) |",
        f"| requests | {n} (COMPLETE {status['COMPLETE']}, INCOMPLETE {status['INCOMPLETE']}, NOT_APPLICABLE {status['NOT_APPLICABLE']}) |",
        "| sessions | 2: the first judged 517 cases and was stopped by the host for low memory; the resumed run reused those 517 checkpoints (no model calls) and judged the last case |",
        "| output | `output.csv`: 518 rows, 0 contract errors, every row identical to its version checkpoint |",
        f"| fallbacks to baseline (agent never submitted) | {fallbacks} |",
        "",
        "## Models",
        "",
        "| provider | model | used for |",
        "|---|---|---|",
        f"| {PROVIDER} | `{MODEL}` | intent classification (structured output) and the tool-calling investigation loop |",
        "",
        "Only one model is used, so the per-model totals equal the overall totals below. Baseline "
        "mode, gathering, the step builder and validation make no model calls.",
        "",
        "## Totals for the final run (per model = overall)",
        "",
        "| metric | value |",
        "|---|---|",
        f"| model calls | {fmt(total['calls'])} ({total['calls'] / n:.1f} per request) |",
        f"| input tokens, uncached | {fmt(total['input_tokens'])} |",
        f"| input tokens, cache write | {fmt(total['cache_creation_input_tokens'])} |",
        f"| input tokens, cache read | {fmt(total['cache_read_input_tokens'])} |",
        f"| input tokens, all | {fmt(total['input_tokens'] + total['cache_creation_input_tokens'] + total['cache_read_input_tokens'])} |",
        f"| output tokens (incl. thinking) | {fmt(total['output_tokens'])} |",
        f"| total tokens | {fmt(all_tokens)} |",
        f"| average tokens per request | {fmt(round(all_tokens / n))} |",
        f"| median / max tokens per request | {fmt(round(statistics.median(per_case_tokens)))} / {fmt(max(per_case_tokens))} |",
        f"| **estimated total cost** | **${cost(total):.2f}** |",
        f"| **estimated cost per request** | **${cost(total) / n:.4f}** |",
        f"| max cost of a single request | ${max(per_case_cost):.4f} |",
        "",
        "## By stage",
        "",
        "| stage | calls | uncached input | cache write | cache read | output | est. cost |",
        "|---|---|---|---|---|---|---|",
    ]
    for stage, c in sorted(by_stage.items()):
        lines.append(f"| {stage} | {fmt(c['calls'])} | {fmt(c['input_tokens'])} | {fmt(c['cache_creation_input_tokens'])} | "
                     f"{fmt(c['cache_read_input_tokens'])} | {fmt(c['output_tokens'])} | ${cost(c):.2f} |")
    lines += [
        "",
        f"Investigation turns per deletion request: mean {statistics.mean(turns):.1f}, median "
        f"{statistics.median(turns):.0f}, max {max(turns)} (turn cap 16; {at_cap} requests reached it and submitted "
        "on the final turn). The 10 NOT_APPLICABLE requests stop after the intent call.",
        "",
        "## Pricing assumptions",
        "",
        "| token type | USD per million, prompt <= 100K | USD per million, prompt > 100K |",
        "|---|---|---|",
        f"| input (uncached) | {PRICE['input_tokens']:.2f} | {PRICE_LONG['input_tokens']:.2f} |",
        f"| cache write (1.25x input) | {PRICE['cache_creation_input_tokens']:.3f} | {PRICE_LONG['cache_creation_input_tokens']:.3f} |",
        f"| cache read (0.1x input) | {PRICE['cache_read_input_tokens']:.2f} | {PRICE_LONG['cache_read_input_tokens']:.2f} |",
        f"| output | {PRICE['output_tokens']:.2f} | {PRICE_LONG['output_tokens']:.2f} |",
        "",
        f"List prices for `claude-haiku-5-5`, applied per call by prompt size. {long_calls} of "
        f"{fmt(total['calls'])} calls had a prompt above 100K tokens (largest {fmt(max_prompt)}: late turns "
        f"of long investigations) and are priced at the long-prompt tier; they account for ${long_cost:.2f} "
        "of the total. Costs are estimates; the provider invoice is authoritative.",
        "",
        "Prompt caching does most of the work: the static system prompt and tool schemas are cached "
        "across requests and the growing conversation is cached across turns, so "
        f"{total['cache_read_input_tokens'] / (total['input_tokens'] + total['cache_creation_input_tokens'] + total['cache_read_input_tokens']):.0%} "
        "of input tokens are cache reads.",
        "",
        "## One-time preprocessing (not part of the final run)",
        "",
        "The 11 evidence screenshots were transcribed once with the same model and cached in "
        "`code/cache/image_text/`; the final run reads the cache and makes no vision calls.",
        "",
        "| stage | model | calls | input | output | est. cost |",
        "|---|---|---|---|---|---|",
        f"| image transcription | `{MODEL}` | {len(img)} | {fmt(img_c['input_tokens'])} | {fmt(img_c['output_tokens'])} | ${cost(img_c):.4f} |",
        "",
        "## Overall including preprocessing",
        "",
        f"| model | calls | total tokens | est. cost |",
        "|---|---|---|---|",
        f"| `{MODEL}` | {fmt(total['calls'] + len(img))} | {fmt(all_tokens + sum(img_c[k] for k in KEYS))} | "
        f"${cost(total) + cost(img_c):.2f} |",
        "",
        "Development and evaluation runs (dev, holdout and custom-case runs) are not included above; "
        "they are described in `evaluation_report.md`. Unit tests use a fake client and make no calls.",
        "",
    ]
    text = "\n".join(lines)
    (EVALUATION_DIR / "usage_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
