"""Baseline judgment: no LLM.

- intent:   keyword match for a deletion verb in a handful of languages
- category: fixed resource-prefix -> data category mapping
- state:    latest usable record that states anything about the resource,
            taken at face value (activity claims included)
- scope:    every gathered candidate is accepted
"""

import re

from .evidence_index import EvidenceIndex
from .gather import Gathered
from .observe import Observation, observe
from .policy import DATA_FLOW, Fact, Judgment, required_action

DELETION_KEYWORDS = re.compile(
    r"delet|eras|remov|close my account|close this account|"     # en
    r"elimin|borr|"                                              # es
    r"exclus|apag|"                                              # pt
    r"suppr|effac|"                                              # fr
    r"lösch|losch|entfern|"                                      # de
    r"cancell|rimuov|"                                           # it
    r"削除|삭제|删除",                                            # ja / ko / zh
    re.IGNORECASE,
)

PREFIX_CATEGORY = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "cache": "CACHE_ENTRY",
    "support": "SUPPORT_MESSAGE",
    "object": "SUPPORT_ATTACHMENT",
    "analytics": "ANALYTICS_PROFILE",
    "export": "USER_EXPORT",
    "marketing": "MARKETING_PROFILE",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "audit_record": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "backup": "BACKUP_ARCHIVE",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "data_flow": DATA_FLOW,
}


def keyword_intent(text: str) -> bool:
    return bool(DELETION_KEYWORDS.search(text))


def to_state(obs: Observation, category: str) -> str | None:
    """Translate a face-value observation into a policy state for a category."""
    status = obs.status
    if category == DATA_FLOW:
        return {"active": "ACTIVE", "disabled": "DISABLED", "present": "ACTIVE"}.get(status)
    if category == "BACKUP_ARCHIVE":
        return {"restorable": "RESTORABLE", "blocked": "BLOCKED", "present": "RESTORABLE",
                "absent": "BLOCKED"}.get(status)
    if status == "absent":
        return "ABSENT"
    if status != "present":
        return None
    action = required_action(category)
    if action == "REDACT":
        return "PRESENT" if obs.direct is not False else "TREATED"
    if action == "ANONYMIZE":
        return "PRESENT" if (obs.direct is not False or obs.reference) else "TREATED"
    return "PRESENT"


def latest_observation(index: EvidenceIndex, g: Gathered, rid: str):
    """Most recent usable (record, observation) for a resource."""
    best = None
    for rec in index.by_resource.get(rid, []):
        if rec.captured_at > g.audit_at:
            continue
        obs = observe(rec, rid)
        if obs is None:
            continue
        # Same timestamp: prefer an observation over an activity claim.
        key = (rec.captured_at, not obs.claim)
        if best is None or key > best[0]:
            best = (key, rec, obs)
    return (best[1], best[2]) if best else (None, None)


def judge_baseline(request: dict, g: Gathered, index: EvidenceIndex) -> Judgment:
    j = Judgment(request["case_id"], "baseline", keyword_intent(request["request_text"]))
    if not j.deletion_requested:
        return j
    for rid in sorted(g.candidates):
        cand = g.candidates[rid]
        category = PREFIX_CATEGORY.get(rid.rsplit("_", 1)[0])
        if category is None or cand.system_id is None:
            j.notes.append(f"{rid}: no category mapping")
            continue
        rec, obs = latest_observation(index, g, rid)
        if rec is None:
            j.notes.append(f"{rid}: no usable observation")
            continue
        state = to_state(obs, category)
        if state is None:
            j.notes.append(f"{rid}: observation {obs.status} has no state for {category}")
            continue
        j.facts.append(Fact(rid, cand.system_id, category, state, rec.evidence_id, obs.detail))
    return j
