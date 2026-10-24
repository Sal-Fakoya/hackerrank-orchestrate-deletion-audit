"""Read a resource's state off one Record, at face value.

observe(record, resource_id) -> Observation or None
    status:    present | absent | active | disabled | restorable | blocked
    direct:    True/False if the record shows whether direct identifiers
               (name, email, phone, email hash) remain; None if it cannot tell
    reference: True/False/None for a stable subject reference (user_id,
               customer_reference, subject_reference)
    claim:     True when the source only reports an operation result
               (activity rows), not an observation of the stored data

Baseline mode uses this as its whole judgment. Agent mode shows it as a hint
next to the raw record; the agent decides whether to trust it.
"""

import re
from dataclasses import dataclass

from .evidence_index import Record
from .identifiers import extract_tokens

DIRECT_FIELDS = {
    "email", "phone", "full_name", "name", "display_name", "email_hash",
    "requester_email", "requester_name", "requester_phone", "owner_email",
    "owner_phone", "customer_email", "uploaded_by",
}
REFERENCE_FIELDS = {"user_id", "customer_reference", "subject_reference"}
TEXT_FIELDS = {"message_text", "message_excerpt"}

RESULT_STATUS = {
    "FOUND": ("present", None), "IDENTIFIABLE": ("present", True), "NOT_FOUND": ("absent", None),
    "ANONYMIZED": ("present", False), "REDACTED": ("present", False),
    "ACTIVE": ("active", None), "DISABLED": ("disabled", None), "BLOCKED": ("blocked", None),
}


@dataclass
class Observation:
    status: str
    direct: bool | None = None
    reference: bool | None = None
    claim: bool = False
    detail: str = ""


def _truthy(value) -> bool | None:
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "yes")


def _scan_fields(node, acc):
    """Collect direct/reference evidence from a nested dict."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                _scan_fields(v, acc)
                continue
            present = v is not None and str(v).strip().lower() not in ("", "null", "none")
            if k in DIRECT_FIELDS:
                acc["direct"] = acc["direct"] or present
                acc["seen_direct"] = True
            elif k in REFERENCE_FIELDS:
                acc["reference"] = acc["reference"] or present
                acc["seen_ref"] = True
            elif k in TEXT_FIELDS and present:
                if any(t.startswith(("email:", "phone:")) for t in extract_tokens(str(v))):
                    acc["direct"] = True
                acc["seen_direct"] = True
    elif isinstance(node, list):
        for v in node:
            _scan_fields(v, acc)


def _structured(row: dict, prefix: str) -> Observation:
    if "result" in row and row["result"] in RESULT_STATUS:
        status, direct = RESULT_STATUS[row["result"]]
        if status == "active" and prefix == "backup":
            status = "restorable"
        return Observation(status, direct, detail=f"result={row['result']}")
    if "status_code" in row:
        if str(row["status_code"]) == "404" or row.get("profile") is None:
            return Observation("absent", detail=f"status_code={row['status_code']}")
    if "enabled" in row:
        return Observation("active" if _truthy(row["enabled"]) else "disabled", detail=f"enabled={row['enabled']}")
    if "restore_allowed" in row:
        allowed = _truthy(row["restore_allowed"])
        return Observation("restorable" if allowed else "blocked", detail=f"restore_allowed={row['restore_allowed']}")
    hits = row.get("hits")
    if isinstance(hits, dict) and "total" in hits:
        if not hits["total"]:
            return Observation("absent", detail="hits.total=0")
    # A deleted *profile* is gone; a tombstone carries account_status=DELETED by
    # definition and must be judged on which identifiers it still holds.
    if str(row.get("account_status", "")).upper() == "DELETED" and prefix == "account":
        return Observation("absent", detail="account_status=DELETED")
    acc = {"direct": False, "reference": False, "seen_direct": False, "seen_ref": False}
    _scan_fields(row, acc)
    return Observation("present",
                       acc["direct"] if acc["seen_direct"] else None,
                       acc["reference"] if acc["seen_ref"] else None,
                       detail="record present")


_KV = re.compile(r'(\w+)[=:]"?([^"\s,|}]*)"?')


def _log(text: str, rid: str, prefix: str) -> Observation | None:
    obs = None
    for line in text.splitlines():
        if rid not in line and "resource_id" in line:
            continue
        kv = dict(_KV.findall(line))
        result = kv.get("result", "")
        if result.upper() in RESULT_STATUS:
            status, direct = RESULT_STATUS[result.upper()]
            if status == "active" and prefix == "backup":
                status = "restorable"
            obs = Observation(status, direct, detail=f"result={result}")
        elif kv.get("http_status") == "404" or kv.get("rows") == "0" or result == "miss":
            obs = Observation("absent", detail=line.strip()[:160])
        elif "enabled" in kv:
            obs = Observation("active" if _truthy(kv["enabled"]) else "disabled", detail=f"enabled={kv['enabled']}")
        elif "deletion_block" in kv or "restore_allowed" in kv:
            blocked = _truthy(kv.get("deletion_block")) or kv.get("restore_allowed") in ("false", "0")
            obs = Observation("blocked" if blocked else "restorable", detail=line.strip()[:160])
        elif "matched_value" in kv or kv.get("marker_found") == "false":
            obs = Observation("present", True, detail=line.strip()[:160])
        elif "direct_identifiers" in kv:
            obs = Observation("present", kv["direct_identifiers"] not in ("0", ""), detail=f"direct_identifiers={kv['direct_identifiers']}")
        elif result == "hit" or kv.get("rows", "") not in ("", "0") or kv.get("http_status") == "200":
            fields = {k: (None if v in ("null", "") else v) for k, v in kv.items()}
            acc = {"direct": False, "reference": False, "seen_direct": False, "seen_ref": False}
            _scan_fields(fields, acc)
            obs = Observation("present",
                              acc["direct"] if acc["seen_direct"] else None,
                              acc["reference"] if acc["seen_ref"] else None,
                              detail=line.strip()[:160])
    return obs


_ACTIVITY = {
    "DELETE_RECORD": ("absent", "present"),
    "ANONYMIZE_RECORD": ("present_treated", "present"),
    "REDACT_RECORD": ("present_treated", "present"),
    "DISABLE_DATA_FLOW": ("disabled", "active"),
    "BLOCK_RESTORE": ("blocked", "restorable"),
}


def _activity(row: dict) -> Observation | None:
    etype, result, details = row["event_type"], row["reported_result"], row["details"].lower()
    if etype in _ACTIVITY:
        ok, failed = _ACTIVITY[etype]
        status = ok if result == "SUCCESS" else failed
        if status == "present_treated":
            return Observation("present", False, claim=True, detail=f"{etype} {result}")
        return Observation(status, claim=True, detail=f"{etype} {result}")
    if etype == "CHECK_DATA_FLOW":
        return Observation("disabled" if "disabled" in details else "active", claim=True, detail=row["details"])
    if etype == "CHECK_RESTORE_ACCESS":
        blocked = "blocked" in details and "not blocked" not in details
        return Observation("blocked" if blocked else "restorable", claim=True, detail=row["details"])
    if etype in ("SYNC_RECORD", "INDEX_RECORD", "UPDATE_RECORD", "REFRESH_CACHE", "RETAIN_RECORD",
                 "SEND_EMAIL", "CREATE_EXPORT", "RESTORE_BACKUP"):
        return Observation("present", claim=True, detail=f"{etype} {result}")
    return None


def observe(rec: Record, rid: str) -> Observation | None:
    prefix = rid.rsplit("_", 1)[0]
    if rec.kind == "activity":
        if rec.content["resource_id"] != rid:
            return None
        return _activity(rec.content)
    if rec.kind == "sql":
        row = dict(rec.content["row"])
        if row.get("resource_id") != rid:
            return None
        if isinstance(row.get("record_json"), dict):
            row = {**row, **row.pop("record_json")}
        return _structured(row, prefix)
    if rec.kind == "json":
        row = rec.content["record"]
        if row.get("resource_id") != rid:
            return None
        return _structured(row, prefix)
    if rec.kind == "log":
        return _log(rec.text(), rid, prefix)
    if rec.kind in ("text", "pdf", "image") and rec.scope == "item":
        m = re.search(rf"{re.escape(rid)}\s*\|\s*([A-Z_]+)\s*\|\s*([A-Z_]+)", rec.text())
        if m and m.group(2) in RESULT_STATUS:
            status, direct = RESULT_STATUS[m.group(2)]
            if status == "active" and prefix == "backup":
                status = "restorable"
            return Observation(status, direct, detail=f"{m.group(1)} {m.group(2)}")
    return None
