"""Deterministic step builder: Facts -> (deletion_status, ordered steps)."""

from .policy import Fact, treatment

OPERATION_ORDER = [
    # FINDING
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    # REMEDIATION
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    # VERIFICATION
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
PHASE = {op: "FINDING" for op in OPERATION_ORDER[:4]}
PHASE |= {op: "REMEDIATION" for op in OPERATION_ORDER[4:9]}
PHASE |= {op: "VERIFICATION" for op in OPERATION_ORDER[9:]}
PHASE_ORDER = ["FINDING", "REMEDIATION", "VERIFICATION"]
RESULT_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}


def build_steps(facts: list[Fact]) -> tuple[str, list[dict], list[str]]:
    """Return (deletion_status, steps, skipped-fact notes) for a deletion request."""
    findings, remediations, verifications, skipped = [], [], [], []
    for fact in facts:
        ops = treatment(fact.category, fact.state, fact.condition_met)
        if ops is None:
            skipped.append(f"{fact.resource_id}: no policy treatment for {fact.category}/{fact.state}")
            continue
        finding, remediation, verification = ops
        if finding:
            findings.append((finding, fact))
            remediations.append((remediation, fact))
        verifications.append((verification, fact))

    complete = not findings
    raw = []
    for op, fact in findings:
        raw.append(_step(op, fact, fact.evidence_id))
    for op, fact in remediations:
        raw.append(_step(op, fact, ""))
    for op, fact in verifications:
        raw.append(_step(op, fact, fact.evidence_id if complete else ""))

    raw = _dedupe(raw)
    raw.sort(key=lambda s: (PHASE_ORDER.index(s["phase"]), OPERATION_ORDER.index(s["operation"]),
                            s["system_id"], s["resource_id"]))
    for i, step in enumerate(raw, 1):
        step["step_no"] = i
    return ("COMPLETE" if complete else "INCOMPLETE"), raw, skipped


def _step(op: str, fact: Fact, evidence_id: str) -> dict:
    return {
        "step_no": 0,
        "phase": PHASE[op],
        "system_id": fact.system_id,
        "resource_id": fact.resource_id,
        "operation": op,
        "state": RESULT_STATE[op],
        "evidence_id": evidence_id,
    }


def _dedupe(steps: list[dict]) -> list[dict]:
    seen, out = set(), []
    for s in steps:
        key = (s["phase"], s["system_id"], s["resource_id"], s["operation"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
