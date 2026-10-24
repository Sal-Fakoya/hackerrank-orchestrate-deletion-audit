"""Output-contract validation for one row and for a whole output file."""

import json
import re

from .steps import OPERATION_ORDER, PHASE, PHASE_ORDER, RESULT_STATE

STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def validate_row(status: str, steps: list[dict], usable_evidence: set[str] | None = None) -> list[str]:
    """Return a list of contract violations (empty when the row is valid)."""
    errors = []
    if status not in STATUSES:
        errors.append(f"bad status {status}")
    if status == "NOT_APPLICABLE" and steps:
        errors.append("NOT_APPLICABLE must have []")
    if status == "COMPLETE" and any(s["phase"] != "VERIFICATION" for s in steps):
        errors.append("COMPLETE may only contain VERIFICATION steps")
    if status == "INCOMPLETE" and not any(s["phase"] == "FINDING" for s in steps):
        errors.append("INCOMPLETE needs at least one FINDING")
    prev = None
    for i, s in enumerate(steps, 1):
        if set(s) != STEP_KEYS:
            errors.append(f"step {i}: keys {sorted(s)}")
            continue
        if s["step_no"] != i:
            errors.append(f"step {i}: step_no {s['step_no']}")
        op = s["operation"]
        if op not in PHASE:
            errors.append(f"step {i}: unknown operation {op}")
            continue
        if PHASE[op] != s["phase"]:
            errors.append(f"step {i}: {op} is not a {s['phase']} operation")
        if RESULT_STATE[op] != s["state"]:
            errors.append(f"step {i}: {op} must have state {RESULT_STATE[op]}")
        key = (PHASE_ORDER.index(s["phase"]), OPERATION_ORDER.index(op), s["system_id"], s["resource_id"])
        if prev is not None and key < prev:
            errors.append(f"step {i}: out of order")
        prev = key
        future = status == "INCOMPLETE" and s["phase"] != "FINDING"
        if future and s["evidence_id"]:
            errors.append(f"step {i}: future step must have empty evidence_id")
        if not future and not s["evidence_id"]:
            errors.append(f"step {i}: missing evidence_id")
        if s["evidence_id"] and usable_evidence is not None and s["evidence_id"] not in usable_evidence:
            errors.append(f"step {i}: evidence {s['evidence_id']} not usable at audit_at")
    return errors


def validate_output(rows: list[dict], expected_case_ids: list[str]) -> list[str]:
    errors = []
    ids = [r["case_id"] for r in rows]
    if ids != sorted(expected_case_ids, key=case_sort_key):
        errors.append("case ids missing, duplicated or unsorted")
    for r in rows:
        try:
            steps = json.loads(r["steps_json"])
        except json.JSONDecodeError as e:
            errors.append(f"{r['case_id']}: invalid JSON ({e})")
            continue
        errors += [f"{r['case_id']}: {e}" for e in validate_row(r["deletion_status"], steps)]
    return errors


def case_sort_key(case_id: str):
    """Numeric order of case ids (case_9 < case_10); tolerates suffixes like custom_05a."""
    m = re.match(r"(\d+)(.*)", case_id.rsplit("_", 1)[1])
    return (int(m.group(1)), m.group(2)) if m else (float("inf"), case_id)
