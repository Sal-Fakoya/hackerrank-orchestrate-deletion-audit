"""Judgment vocabulary (facts) and the deletion policy that turns facts into steps.

A judge (baseline or agent) produces Facts; everything after that is
deterministic. A Fact says: this resource belongs to the subject, it is of this
data category, and at audit_at it was in this state, proven by this evidence.
"""

from dataclasses import asdict, dataclass, field
from functools import cache

from .data import load_dataset

# Data flows are not a row in deletion_rules.csv; the problem statement defines
# their only allowed treatment (disable).
DATA_FLOW = "DATA_FLOW"

STATES = {
    "PRESENT": "record exists and still exposes what its rule must remove (for DELETE categories: the record exists at all; for retained records: it is available)",
    "ABSENT": "record no longer exists",
    "TREATED": "record exists and has already been redacted/anonymized as its rule requires",
    "ACTIVE": "data flow is enabled and can copy or recreate the subject's data",
    "DISABLED": "data flow is disabled",
    "RESTORABLE": "backup can still be restored with the subject's data",
    "BLOCKED": "restore of the subject's data from this backup is blocked",
}


@dataclass
class Fact:
    resource_id: str
    system_id: str
    category: str
    state: str
    evidence_id: str
    reason: str = ""
    # False when the category's rule condition does not hold (e.g. retention
    # period expired, legal hold no longer active, attachment shared with others).
    condition_met: bool = True


@dataclass
class Judgment:
    case_id: str
    mode: str
    deletion_requested: bool
    facts: list[Fact] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@cache
def rules() -> dict[str, dict]:
    return {r["data_category"]: r for r in load_dataset().rules}


def categories() -> list[str]:
    return list(rules()) + [DATA_FLOW]


def required_action(category: str, condition_met: bool = True) -> str:
    if category == DATA_FLOW:
        return "DISABLE"
    action = rules()[category]["required_action"]
    if condition_met:
        return action
    # The rule's condition does not hold, so its treatment does not apply:
    # - a retained record without a valid retention basis (expired retention,
    #   released legal hold) is ordinary personal data of the subject -> delete;
    #   backups are excluded: their condition is about restore blocking, which
    #   has no other allowed treatment;
    # - a support attachment that does not belong only to the subject keeps the
    #   other parties' content -> redact the subject's identifiers, like a message.
    if action == "RETAIN" and category != "BACKUP_ARCHIVE":
        return "DELETE"
    if category == "SUPPORT_ATTACHMENT":
        return "REDACT"
    return action


# (required action, observed state) -> (finding, remediation, verification).
# finding/remediation are None when the record is already compliant.
_TREATMENT = {
    ("DELETE", "PRESENT"): ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    ("DELETE", "TREATED"): ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    ("DELETE", "ABSENT"): (None, None, "VERIFY_RECORD_ABSENT"),
    ("REDACT", "PRESENT"): ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    ("REDACT", "TREATED"): (None, None, "VERIFY_RECORD_REDACTED"),
    ("REDACT", "ABSENT"): (None, None, "VERIFY_RECORD_ABSENT"),
    ("ANONYMIZE", "PRESENT"): ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    ("ANONYMIZE", "TREATED"): (None, None, "VERIFY_RECORD_ANONYMIZED"),
    ("ANONYMIZE", "ABSENT"): (None, None, "VERIFY_RECORD_ABSENT"),
    ("RETAIN", "PRESENT"): (None, None, "VERIFY_RECORD_PRESENT"),
    ("RETAIN", "TREATED"): (None, None, "VERIFY_RECORD_PRESENT"),
    ("RETAIN", "RESTORABLE"): ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    ("RETAIN", "BLOCKED"): (None, None, "VERIFY_RESTORE_BLOCKED"),
    ("DISABLE", "ACTIVE"): ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    ("DISABLE", "DISABLED"): (None, None, "VERIFY_DATA_FLOW_DISABLED"),
}


def treatment(category: str, state: str, condition_met: bool = True):
    """Return (finding, remediation, verification) operations, or None if the
    combination has no policy meaning (e.g. a retained record that is gone)."""
    return _TREATMENT.get((required_action(category, condition_met), state))
