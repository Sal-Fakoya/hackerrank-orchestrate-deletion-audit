"""Results UI (read-only): summary + case inspector.

    streamlit run code/ui/app.py

Reads saved checkpoints, saved scores and the dataset. Never calls a model and
never re-runs the pipeline. Holdout cases are not inspectable (evaluation
protocol: only holdout aggregates are looked at until the end).
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

st.set_page_config(page_title="Deletion Audit Results", layout="wide")

STATUSES = ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"]
METRICS = [("status_accuracy", "status accuracy"), ("step_precision", "step precision"),
           ("step_recall", "step recall"), ("step_f1", "step F1"),
           ("step_f1_no_evidence", "step F1 ignoring evidence_id"),
           ("sequence_accuracy", "exact sequence"), ("resource_precision", "resource precision"),
           ("resource_recall", "resource recall")]
RESULT_COLORS = {"match": "", "evidence/state differs": "background-color: rgba(230, 160, 0, 0.25)",
                 "missing (expected only)": "background-color: rgba(220, 60, 60, 0.22)",
                 "extra (predicted only)": "background-color: rgba(60, 120, 220, 0.22)"}


@st.cache_resource(show_spinner="Loading dataset and evidence index…")
def warm():
    data.build_index()
    data.resolver()
    return True


def pct(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) else "-"


def metric_table(scores_by_run: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for key, label in METRICS:
        row = {"metric": label}
        for run, s in scores_by_run.items():
            row[run] = pct(s.get(key)) if s else "-"
        rows.append(row)
    return pd.DataFrame(rows).set_index("metric")


# ====================================================================== summary
def summary_page():
    st.title("Summary")

    # ---- full-dataset run
    st.header("Full run: 518 evaluation requests")
    agent = data.all_checkpoints("agent", "eval")
    base = data.all_checkpoints("baseline", "eval")
    n = len(data.requests_for("eval"))
    st.caption(f"Agent checkpoints: version `{data.version_of('agent', 'eval')}`, "
               f"{len(agent)}/{n} cases judged. Baseline: {len(base)}/{n}.")
    if len(agent) < n:
        st.info(f"The agent run is still in progress ({len(agent)}/{n}). Figures below cover judged cases only.")
    counts = {s: sum(c["status"] == s for c in agent.values()) for s in STATUSES}
    cols = st.columns(4)
    cols[0].metric("Judged", f"{len(agent)}/{n}")
    for col, s in zip(cols[1:], STATUSES):
        col.metric(s, counts[s], f"{counts[s] / max(1, len(agent)):.1%} of judged", delta_color="off")

    # Baseline vs agent on the SAME cases only.
    common = sorted(set(agent) & set(base))
    st.subheader("Status distribution: baseline vs agent on the same cases")
    if common:
        dist = pd.DataFrame({
            "agent": {s: sum(agent[c]["status"] == s for c in common) for s in STATUSES},
            "baseline": {s: sum(base[c]["status"] == s for c in common) for s in STATUSES},
        })
        dist.index.name = "status"
        st.dataframe(dist, width="content")
        agree = sum(agent[c]["status"] == base[c]["status"] for c in common)
        st.caption(f"Both modes on the same {len(common)} cases (agent judged {len(agent)}, baseline {len(base)}). "
                   f"Same status on {agree}/{len(common)}. Agent checkpoints: version "
                   f"`{data.version_of('agent', 'eval')}`; baseline checkpoints: version "
                   f"`{data.version_of('baseline', 'eval')}`.")
        if data.version_of("baseline", "eval") == "unversioned":
            st.warning("The baseline checkpoints come from the first baseline run, before checkpoint versioning; "
                       "they predate later shared-pipeline fixes (e.g. tombstone observation, rule conditions). "
                       "Re-run `python code/main.py --mode baseline` to compare against the current code.")
    else:
        st.info("No case has been judged by both modes yet.")

    usage = data.usage_totals(agent)
    st.subheader("Token usage and estimated cost (agent, judged cases; tier-aware per call)")
    u = pd.DataFrame([{
        "model calls": usage["calls"], "input": usage["input_tokens"], "output": usage["output_tokens"],
        "cache write": usage["cache_creation_input_tokens"], "cache read": usage["cache_read_input_tokens"],
        "calls > 100K prompt (long-prompt price)": usage["long_prompt_calls"],
        "estimated USD": round(usage["cost_usd"], 3),
        "USD per case": round(usage["cost_usd"] / max(1, len(agent)), 4)}])
    st.dataframe(u, hide_index=True)

    st.subheader("Intent cross-check against CLASSIFY_REQUEST (post hoc)")
    try:
        from intent_crosscheck import crosscheck
        x = crosscheck({cid: c["status"] for cid, c in agent.items()})
        st.write(f"{x['intake_rows']} intake rows mapped to {len(x['mapped'])} eval requests. "
                 f"Agent NOT_APPLICABLE: **{len(x['predicted_na'])}**; both non-deletion: **{len(x['agree'])}**.")
        st.dataframe(pd.DataFrame([
            {"check": "both say non-deletion", "cases": ", ".join(x["agree"]) or "-"},
            {"check": "intake non-deletion, agent said deletion", "cases": ", ".join(x["intake_only"]) or "-"},
            {"check": "agent NOT_APPLICABLE without intake row", "cases": ", ".join(x["ours_only"]) or "-"},
            {"check": "intake row, case not judged yet", "cases": ", ".join(x["intake_not_judged_yet"]) or "-"},
        ]), hide_index=True)
    except Exception as e:  # noqa: BLE001 - informational panel only
        st.warning(f"Cross-check unavailable: {e}")

    # ---- dev comparison
    st.header("Dev set: baseline vs agent")
    files = data.score_files()
    dev_files = [k for k in files if k.startswith("dev_v")]
    if dev_files:
        pick = st.selectbox("Saved dev run", dev_files, index=0)
        s = data.load_json(files[pick])
        st.dataframe(metric_table({"baseline": s.get("baseline"), "agent": s.get("agent")}))
    variance = {k.replace("_scores", ""): data.load_json(p).get("agent") for k, p in files.items()
                if k.startswith("dev_") and not k.startswith("dev_final_run1_comparison")}
    if variance:
        st.subheader("Run-to-run variance (agent, dev)")
        st.dataframe(metric_table(variance))

    # ---- holdout
    st.header("Holdout (aggregates only)")
    st.caption("Holdout cases are never inspected individually; only these aggregates are shown.")
    hold = {k: data.load_json(p).get("agent") for k, p in files.items() if k.startswith("holdout_")}
    if hold:
        st.dataframe(metric_table(hold))
    m1 = data.holdout_m1_markdown()
    if m1:
        with st.expander("Milestone 1 (agent iteration 2): saved aggregate table"):
            st.markdown(m1)

    custom = data.RESULTS / "custom_results.md"
    if custom.exists():
        st.header("Custom cases")
        text = custom.read_text(encoding="utf-8")
        st.markdown(text.split("## Agent failures")[0])


# ====================================================================== inspector
def inspector_page():
    st.title("Case inspector")
    c1, c2, c3 = st.columns([1, 1, 2])
    set_name = c1.selectbox("Case set", data.SETS, index=1,
                            help="Holdout cases are excluded (evaluation protocol).")
    mode = c2.selectbox("Judgment mode", ["agent", "baseline"])
    reqs = {r["case_id"]: r for r in data.requests_for(set_name)}
    judged = data.all_checkpoints(mode, set_name)
    options = sorted(reqs, key=lambda c: (len(c), c))
    label = lambda c: f"{c}  ·  {judged[c]['status']}" if c in judged else f"{c}  ·  not judged"
    case_id = c3.selectbox("Case", options, format_func=label)
    req = reqs[case_id]
    saved = judged.get(case_id)

    # ---- request
    st.subheader("Request")
    a, b, c, d = st.columns(4)
    a.metric("Reported status (claim)", req["reported_status"])
    b.metric("Requested", req["requested_at"][:16].replace("T", " "))
    c.metric("Reported", req["reported_at"][:16].replace("T", " "))
    d.metric("audit_at (cutoff)", req["audit_at"][:16].replace("T", " "))
    st.write(f"**Email:** `{req['email']}`")
    st.text_area("Request text", req["request_text"], height=90, disabled=True)

    # ---- identifiers
    st.subheader("Resolved identifiers (current and historical)")
    rows, subject = data.identifier_rows(req["email"])
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    # ---- result
    st.subheader(f"Result ({mode})")
    if not saved:
        st.info("No saved judgment for this case yet.")
        steps, facts, j = [], [], {}
    else:
        j = saved["judgment"]
        steps = saved["steps"]
        facts = j.get("facts", [])
        st.caption(f"Checkpoint version `{saved.get('judge_version', 'unversioned')}`")
        x, y, z = st.columns(3)
        x.metric("Deletion status", saved["status"])
        y.metric("Steps", len(steps))
        z.metric("Contract errors", len(saved.get("errors", [])))
        if saved.get("errors"):
            st.error("\n".join(saved["errors"]))

    expected = data.gold(set_name, case_id)
    if expected:
        st.subheader("Predicted vs expected steps")
        g_status, g_steps = expected
        s1, s2 = st.columns(2)
        s1.metric("Expected status", g_status)
        s2.metric("Predicted status", saved["status"] if saved else "-",
                  "match" if saved and saved["status"] == g_status else "differs", delta_color="off")
        comp = pd.DataFrame(data.compare_steps(steps, g_steps))
        if not comp.empty:
            st.dataframe(comp.style.apply(lambda r: [RESULT_COLORS[r["result"]]] * len(r), axis=1),
                         hide_index=True, width="stretch")
            st.caption("Rows keyed by (phase, system, resource, operation). Amber: evidence or state differs; "
                       "red: expected but missing; blue: predicted but not expected.")
        else:
            st.write("Both empty.")
        left, right = st.columns(2)
        left.markdown("**Predicted steps_json**")
        left.dataframe(pd.DataFrame(steps)[["step_no"] + data.STEP_FIELDS] if steps else pd.DataFrame(),
                       hide_index=True, width="stretch")
        right.markdown("**Expected steps_json**")
        right.dataframe(pd.DataFrame(g_steps)[["step_no"] + data.STEP_FIELDS] if g_steps else pd.DataFrame(),
                        hide_index=True, width="stretch")
    elif saved:
        st.markdown("**Final steps_json**")
        st.dataframe(pd.DataFrame(steps)[["step_no"] + data.STEP_FIELDS] if steps else pd.DataFrame(),
                     hide_index=True, width="stretch")

    if saved:
        st.subheader("Judgment")
        if facts:
            st.markdown("**Agent's facts** (as submitted, before the deterministic guard rails)")
            st.dataframe(pd.DataFrame(facts), hide_index=True, width="stretch")
        if j.get("rejected"):
            st.markdown("**Rejected candidates**")
            st.dataframe(pd.DataFrame(j["rejected"]), hide_index=True, width="stretch")
        if j.get("notes"):
            with st.expander(f"Notes ({len(j['notes'])}): intent, summary, guard rails, fallbacks"):
                for n in j["notes"]:
                    st.markdown(f"- {n}")
        if saved.get("transcript"):
            with st.expander(f"Agent tool calls ({len(saved['transcript'])})"):
                st.dataframe(pd.DataFrame([{**t, "input": json.dumps(t["input"], ensure_ascii=False)}
                                           for t in saved["transcript"]]), hide_index=True, width="stretch")
        if j.get("usage"):
            u = data.usage_totals({case_id: saved})
            st.caption(f"{u['calls']} model calls · {u['input_tokens']:,} input · {u['output_tokens']:,} output · "
                       f"{u['cache_creation_input_tokens']:,} cache write · {u['cache_read_input_tokens']:,} cache read · "
                       f"~${u['cost_usd']:.4f}")

    # ---- timeline
    st.subheader("Evidence and activity timeline (captured at or before audit_at)")
    resources = {f["resource_id"] for f in facts} | {s["resource_id"] for s in steps}
    resources |= {r["resource_id"] for r in j.get("rejected", [])} if j else set()
    if expected:
        resources |= {s["resource_id"] for s in expected[1]}
    if set_name == "custom" and case_id == "custom_11":
        st.caption("custom_11's synthetic evidence file (custom_ev_01) is loaded only by the custom-case runner "
                   "and is not shown here.")
    tl = data.timeline(req, subject, resources)
    kinds = sorted({r["kind"] for r in tl})
    f1, f2 = st.columns([2, 1])
    picked = f1.multiselect("Record kinds", kinds, default=kinds)
    only_episode = f2.checkbox("Only since requested_at", value=False)
    shown = [r for r in tl if r["kind"] in picked and (not only_episode or r["episode"] == "in episode")]
    st.caption(f"{len(shown)} of {len(tl)} records linked to the subject's identifiers or the case's resources.")
    st.dataframe(pd.DataFrame(shown), hide_index=True, width="stretch", height=420)


warm()
page = st.sidebar.radio("Page", ["Summary", "Case inspector"])
st.sidebar.caption("Read-only view of saved results. No model calls.")
# A plain if/else (not a bare conditional expression): Streamlit "magic" would
# otherwise render the expression's value (None) at the bottom of the page.
if page == "Summary":
    summary_page()
else:
    inspector_page()
