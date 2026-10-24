"""Narrow, documented exception to the audit_at cutoff (PLAN.md §2).

Evidence captured AFTER audit_at may be used for exactly one thing: linking the
subject's identity to a record of a retained category (SECURITY_AUDIT_LOG,
LEGAL_HOLD_RECORD), so that an INCOMPLETE case's post-remediation checks also
verify that this retained record still exists. It is never used for state,
status, findings, remediation, or citations: the added steps are future
VERIFY_RECORD_PRESENT checks with an empty evidence_id, and COMPLETE cases
(whose verification steps must cite pre-audit proof) are never touched.
"""

from datetime import datetime

from .data import parse_ts
from .evidence_index import EvidenceIndex
from .identity import IdentityResolver, Subject
from .policy import rules

ENABLED = True
# Retained categories and the resource prefix that carries each of them.
RETAINED_PREFIX = {"audit_record": "SECURITY_AUDIT_LOG", "legal_hold": "LEGAL_HOLD_RECORD"}


def _retention_ok(record_text: str, audit_at: datetime) -> bool:
    """If the record states a retention end, it must not have passed at audit_at."""
    import re
    m = re.search(r'retention_until"?\s*[:=]\s*"?(\d{4}-\d{2}-\d{2}T[\d:]+Z)', record_text)
    return parse_ts(m.group(1)) >= audit_at if m else True


def post_audit_retained_links(request: dict, subject: Subject, index: EvidenceIndex,
                              ids: IdentityResolver, all_requests: list[dict]) -> dict[str, str]:
    """resource_id -> category for retained records linked to the subject only by
    evidence captured after audit_at (and before the subject's next request)."""
    if not ENABLED or not subject.resolved:
        return {}
    audit_at = parse_ts(request["audit_at"])
    requested_at = parse_ts(request["requested_at"])
    from .gather import same_episode
    later = [parse_ts(r["requested_at"]) for r in all_requests
             if not same_episode(r, request) and parse_ts(r["requested_at"]) > requested_at
             and ids.resolve(r["email"]).user_ids & subject.user_ids]
    horizon = min(later) if later else None
    out = {}
    for tok in subject.tokens:
        for rec in index.by_token.get(tok, []):
            if rec.captured_at <= audit_at or (horizon and rec.captured_at >= horizon):
                continue
            if ids.owners(rec.tokens) - subject.user_ids:
                continue
            for rid in rec.resource_ids:
                category = RETAINED_PREFIX.get(rid.rsplit("_", 1)[0])
                if category and rules()[category]["required_action"] == "RETAIN" \
                        and _retention_ok(rec.text(), audit_at):
                    out[rid] = category
    return out
