"""Shared per-case pipeline: gather -> judge (baseline | agent) -> steps -> validate."""

import json
from dataclasses import dataclass, field

from . import future_verification
from .evidence_index import build_index
from .gather import _all_requests, gather
from .identity import resolver
from .judge_baseline import judge_baseline
from .policy import Fact, Judgment
from .steps import build_steps
from .tools import CaseTools
from .validate import validate_row

MODES = ("baseline", "agent")


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list[dict]
    judgment: Judgment
    errors: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)

    def steps_json(self) -> str:
        return json.dumps(self.steps, ensure_ascii=False, separators=(",", ":"))


def run_case(request: dict, mode: str, agent=None) -> CaseResult:
    index = build_index()
    g = gather(request, index=index)
    transcript = []
    if mode == "baseline":
        judgment = judge_baseline(request, g, index)
    elif mode == "agent":
        judgment, transcript = agent.judge(request, g, index)
    else:
        raise ValueError(mode)
    return finalize(request, judgment, transcript)


def finalize(request: dict, judgment: Judgment, transcript=None) -> CaseResult:
    """Deterministic tail shared by both modes; also used to rebuild steps from a
    saved judgment without calling any model."""
    transcript = transcript or []
    if not judgment.deletion_requested:
        return CaseResult(request["case_id"], "NOT_APPLICABLE", [], judgment, transcript=transcript)
    index = build_index()
    # the judge's raw facts stay untouched (they are what gets checkpointed)
    facts = enforce_attribution(request, judgment, index) if judgment.mode == "agent" else list(judgment.facts)
    status, steps, skipped = build_steps(facts)
    notes = list(skipped)
    if status == "INCOMPLETE":
        # Cutoff exception (future_verification.py): retained records linked only by
        # post-audit evidence get a future VERIFY_RECORD_PRESENT check, nothing else.
        covered = {f.resource_id for f in facts}
        subject = resolver().resolve(request["email"])
        extra = future_verification.post_audit_retained_links(request, subject, index, resolver(), _all_requests())
        added = [Fact(rid, index.system_of(rid), cat, "PRESENT", "", "post-audit identity link (future verification only)")
                 for rid, cat in sorted(extra.items()) if rid not in covered]
        if added:
            status2, steps, _ = build_steps(facts + added)
            assert status2 == status, "future-verification facts must never change status"
            notes += [f"future verification added for {f.resource_id} (post-audit identity link)" for f in added]
    judgment.notes += [n for n in notes if n not in judgment.notes]
    usable = CaseTools(request, index).usable_evidence_ids()
    errors = validate_row(status, steps, usable)
    return CaseResult(request["case_id"], status, steps, judgment, errors, transcript)


IDENTITY_CHANNELS = {"direct", "name", "resource_link"}


def enforce_attribution(request: dict, judgment: Judgment, index) -> list[Fact]:
    """Deterministic guard rails on the agent's scope decisions, from gathering
    provenance (agent mode only; idempotent):

    A. A fact whose resource is linked to the subject only through a deletion-worker
       run shared with other requests (no identifier, name or resource link) cannot
       be attributed to this subject -> dropped.
    B. A candidate that carries the subject's own identifier (or name / resource
       link) in usable evidence captured since requested_at, but that the agent left
       out, is restored with its latest face-value state and evidence.
    Returns the adjusted fact list; judgment.facts (the agent's own) is not modified.
    """
    from .data import parse_ts
    from .judge_baseline import PREFIX_CATEGORY, latest_observation, to_state

    g = gather(request, index=index)
    requested_at = parse_ts(request["requested_at"])
    kept = []
    for f in judgment.facts:
        cand = g.candidates.get(f.resource_id)
        if cand and not (cand.channels & IDENTITY_CHANNELS) and cand.shared_with:
            note = f"attribution rule A: dropped {f.resource_id} (only linked via a run shared with {sorted(cand.shared_with)})"
            if note not in judgment.notes:
                judgment.notes.append(note)
            continue
        kept.append(f)
    have = {f.resource_id for f in kept}
    for rid, cand in sorted(g.candidates.items()):
        if rid in have or not (cand.channels & IDENTITY_CHANNELS):
            continue
        if not any(requested_at <= r.captured_at <= g.audit_at for r in index.by_resource.get(rid, [])):
            continue
        category = PREFIX_CATEGORY.get(rid.rsplit("_", 1)[0])
        rec, obs = latest_observation(index, g, rid)
        state = to_state(obs, category) if (obs and category and cand.system_id) else None
        if state is None:
            continue
        kept.append(Fact(rid, cand.system_id, category, state, rec.evidence_id,
                         "attribution rule B: identity-linked in-episode candidate restored (face-value state)"))
        note = f"attribution rule B: restored {rid} as {category}/{state} per {rec.evidence_id}"
        if note not in judgment.notes:
            judgment.notes.append(note)
    return kept


def judgment_from_dict(d: dict) -> Judgment:
    j = Judgment(d["case_id"], d["mode"], d["deletion_requested"])
    # facts restored by rule B are regenerated by finalize(); keep only the judge's own
    j.facts = [Fact(**f) for f in d["facts"] if not f.get("reason", "").startswith("attribution rule B")]
    j.rejected, j.usage = d.get("rejected", []), d.get("usage", [])
    # notes added by finalize() are regenerated, keep the judge's own notes
    j.notes = [n for n in d.get("notes", []) if not n.startswith(("future verification added", "attribution rule"))]
    return j
