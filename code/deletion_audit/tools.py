"""Agent tools over the evidence index, bound to one case.

Every tool enforces the case's cutoff: files captured after audit_at and
activity after audit_at are invisible. CLASSIFY_REQUEST activity rows are not
in the index at all.
"""

import json
import re
import sqlite3
from datetime import datetime

from .data import load_dataset, parse_ts
from .evidence_index import EvidenceIndex, Record
from .identifiers import EMAIL_RE, HASH_RE, USER_RE, email_token, phone_digits
from .observe import observe
from .paths import DATASET_DIR
from .policy import STATES, categories, rules

MAX_RESULTS = 25
SNIPPET = 700
PAGE = 4000


def _nullable(t):
    return {"type": [t, "null"]}


TOOL_DEFS = [
    {
        "name": "search_evidence",
        "description": (
            "Search all evidence usable at this case's audit_at (files and activity rows). "
            "An email, phone number, 64-hex email hash, user_id (user_12) or resource id "
            "(search_052) is matched exactly as an identifier; anything else is matched as a "
            "case-insensitive substring of the record text (e.g. a file name, ticket id, order id "
            "or person name). Returns matching records with a face-value state hint per resource."),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": _nullable("integer")},
            "required": ["query", "limit"], "additionalProperties": False,
        },
    },
    {
        "name": "get_resource_evidence",
        "description": (
            "Everything usable at audit_at that mentions one resource, oldest first, each with a "
            "face-value state hint (hints from activity rows are workflow claims). Also lists the "
            "latest usable snapshot files that cover this resource's system but do NOT contain the "
            "resource: such a file can prove absence."),
        "input_schema": {
            "type": "object",
            "properties": {"resource_id": {"type": "string"}},
            "required": ["resource_id"], "additionalProperties": False,
        },
    },
    {
        "name": "query_sql_snapshot",
        "description": (
            "Run a read-only SQLite SELECT against one SQL snapshot file (file_id). Pass sql=null to "
            "list its tables, row counts and header comments. Useful to compare primary vs "
            "replica/failover/reporting tables for replication drift. Max 50 rows."),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}, "sql": _nullable("string")},
            "required": ["file_id", "sql"], "additionalProperties": False,
        },
    },
    {
        "name": "get_log_trace",
        "description": "All lines of one log file that share a trace id (e.g. tr-9750867, rb-061801-001).",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}, "trace_id": {"type": "string"}},
            "required": ["file_id", "trace_id"], "additionalProperties": False,
        },
    },
    {
        "name": "get_activity",
        "description": (
            "system_activity rows with event_time <= audit_at, optionally filtered by resource_id, "
            "event_type and an ISO-8601 time window. reported_result is a workflow claim, not proof."),
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_id": _nullable("string"), "event_type": _nullable("string"),
                "start": _nullable("string"), "end": _nullable("string"), "limit": _nullable("integer"),
            },
            "required": ["resource_id", "event_type", "start", "end", "limit"], "additionalProperties": False,
        },
    },
    {
        "name": "read_evidence_file",
        "description": (
            "Raw text of one usable evidence file, paginated by character offset. PDFs are text-"
            "extracted; screenshots are verbatim transcriptions."),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}, "offset": _nullable("integer")},
            "required": ["file_id", "offset"], "additionalProperties": False,
        },
    },
    {
        "name": "get_rule",
        "description": "The deletion_rules.csv row for a data category (condition and required action).",
        "input_schema": {
            "type": "object",
            "properties": {"data_category": {"type": "string"}},
            "required": ["data_category"], "additionalProperties": False,
        },
    },
    {
        "name": "submit_facts",
        "description": (
            "Finish the case. One fact per resource that belongs to this deletion subject and is "
            "governed by the policy, with its state at audit_at and the evidence id that proves it. "
            "List candidates you rejected with the reason. Python turns facts into steps."),
        "input_schema": {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "resource_id": {"type": "string"},
                            "system_id": {"type": "string"},
                            "category": {"type": "string", "enum": categories()},
                            "state": {"type": "string", "enum": list(STATES)},
                            "evidence_id": {"type": "string"},
                            "condition_met": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["resource_id", "system_id", "category", "state", "evidence_id",
                                     "condition_met", "reason"],
                        "additionalProperties": False,
                    },
                },
                "rejected": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"resource_id": {"type": "string"}, "reason": {"type": "string"}},
                        "required": ["resource_id", "reason"], "additionalProperties": False,
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["facts", "rejected", "summary"], "additionalProperties": False,
        },
    },
]
TOOL_DEFS = [{**t, "strict": True} for t in TOOL_DEFS]


class CaseTools:
    def __init__(self, request: dict, index: EvidenceIndex):
        self.request = request
        self.index = index
        self.audit_at: datetime = parse_ts(request["audit_at"])
        ds = load_dataset()
        self.usable_files = {f: e for f, e in index.files.items() if parse_ts(e["captured_at"]) <= self.audit_at}
        self.usable_audit_ids = {a["audit_id"] for a in ds.audit_log if parse_ts(a["event_time"]) <= self.audit_at}
        self._sql: dict[str, sqlite3.Connection] = {}

    # ------------------------------------------------------------ helpers
    def usable(self, rec: Record) -> bool:
        return rec.captured_at <= self.audit_at

    def usable_evidence_ids(self) -> set[str]:
        acts = {r.evidence_id for r in self.index.records if r.kind == "activity" and self.usable(r)}
        return set(self.usable_files) | acts | self.usable_audit_ids

    def render(self, rec: Record, focus: str | None = None) -> dict:
        text = rec.text()
        out = {
            "evidence_id": rec.evidence_id,
            "captured_at": rec.captured_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "system_activity" if rec.kind == "activity" else self.index.files[rec.evidence_id]["file_path"],
            "kind": rec.kind, "locator": rec.locator,
            "resources": list(rec.resource_ids[:8]),
            "content": text if len(text) <= SNIPPET else text[:SNIPPET] + " …[truncated]",
        }
        hints = {}
        for rid in ([focus] if focus else rec.resource_ids[:8]):
            obs = observe(rec, rid)
            if obs:
                hints[rid] = (f"{obs.status}" + (f", direct_identifiers={obs.direct}" if obs.direct is not None else "")
                              + (f", subject_reference={obs.reference}" if obs.reference is not None else "")
                              + (" [workflow claim]" if obs.claim else ""))
        if hints:
            out["state_hint"] = hints
        return out

    # ------------------------------------------------------------ tools
    def search_evidence(self, query: str, limit: int | None = None) -> dict:
        limit = min(limit or MAX_RESULTS, 50)
        q = query.strip()
        recs: list[Record]
        if EMAIL_RE.fullmatch(q):
            recs = self.index.by_token.get(email_token(q), [])
        elif USER_RE.fullmatch(q):
            recs = self.index.by_token.get("user:" + q, [])
        elif HASH_RE.fullmatch(q.lower()):
            recs = self.index.by_token.get("hash:" + q.lower(), [])
        elif re.fullmatch(r"\+?[\d \-().]{8,}", q):
            recs = self.index.by_token.get("phone:" + phone_digits(q), [])
        elif q in self.index.by_resource:
            recs = self.index.by_resource[q]
        else:
            needle = q.lower()
            recs = [r for r in self.index.records if needle in r.text().lower()]
        recs = sorted((r for r in recs if self.usable(r)), key=lambda r: r.captured_at)
        return {"query": q, "total": len(recs), "records": [self.render(r) for r in recs[-limit:]]}

    def get_resource_evidence(self, resource_id: str) -> dict:
        recs = sorted((r for r in self.index.by_resource.get(resource_id, []) if self.usable(r)),
                      key=lambda r: r.captured_at)
        rendered = [self.render(r, resource_id) for r in recs[-40:]]
        system = self.index.system_of(resource_id)
        prefix = resource_id.rsplit("_", 1)[0]
        containing = {r.evidence_id for r in recs}
        covering = {}
        for rec in self.index.records:
            if rec.kind in ("sql", "json") and self.usable(rec) and rec.resource_ids \
                    and rec.resource_ids[0].rsplit("_", 1)[0] == prefix and rec.evidence_id not in containing:
                covering[rec.evidence_id] = rec.captured_at
        absence = sorted(covering.items(), key=lambda kv: kv[1], reverse=True)[:5]
        return {
            "resource_id": resource_id, "system_id": system, "usable_records": len(recs),
            "records": rendered,
            "snapshots_covering_system_without_this_resource": [
                {"evidence_id": f, "captured_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "source": self.index.files[f]["file_path"]} for f, t in absence],
        }

    def _connection(self, file_id: str) -> sqlite3.Connection:
        if file_id not in self._sql:
            path = DATASET_DIR / self.usable_files[file_id]["file_path"]
            con = sqlite3.connect(":memory:")
            con.executescript(path.read_text(encoding="utf-8"))
            con.execute("PRAGMA query_only = ON")
            self._sql[file_id] = con
        return self._sql[file_id]

    def query_sql_snapshot(self, file_id: str, sql: str | None) -> dict:
        entry = self.usable_files.get(file_id)
        if entry is None or not entry["file_path"].endswith(".sql"):
            return {"error": f"{file_id} is not a SQL snapshot usable at audit_at"}
        con = self._connection(file_id)
        if not sql:
            text = (DATASET_DIR / entry["file_path"]).read_text(encoding="utf-8")
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {
                "file_id": file_id, "captured_at": entry["captured_at"],
                "header": [l for l in text.splitlines() if l.startswith("--")],
                "tables": {t: {"rows": con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0],
                               "columns": [c[1] for c in con.execute(f'PRAGMA table_info("{t}")')]} for t in tables},
            }
        if not re.match(r"\s*(select|with)\b", sql, re.I):
            return {"error": "only SELECT / WITH queries are allowed"}
        try:
            cur = con.execute(sql)
            rows = cur.fetchmany(50)
        except sqlite3.Error as e:
            return {"error": str(e)}
        cols = [d[0] for d in cur.description or []]
        return {"columns": cols, "rows": [[_clip(v) for v in row] for row in rows]}

    def get_log_trace(self, file_id: str, trace_id: str) -> dict:
        entry = self.usable_files.get(file_id)
        if entry is None or not entry["file_path"].endswith(".log"):
            return {"error": f"{file_id} is not a log file usable at audit_at"}
        lines = [l for l in (DATASET_DIR / entry["file_path"]).read_text(encoding="utf-8").splitlines()
                 if trace_id in l]
        return {"file_id": file_id, "trace_id": trace_id, "lines": lines[:60]}

    def get_activity(self, resource_id=None, event_type=None, start=None, end=None, limit=None) -> dict:
        limit = min(limit or 40, 100)
        lo = parse_ts(start) if start else None
        hi = min(parse_ts(end), self.audit_at) if end else self.audit_at
        rows = []
        for rec in self.index.records:
            if rec.kind != "activity" or rec.captured_at > hi or (lo and rec.captured_at < lo):
                continue
            c = rec.content
            if resource_id and resource_id not in rec.resource_ids:
                continue
            if event_type and c["event_type"] != event_type:
                continue
            rows.append(c)
        rows.sort(key=lambda c: c["event_time"])
        return {"total": len(rows), "rows": rows[-limit:]}

    def read_evidence_file(self, file_id: str, offset: int | None = None) -> dict:
        entry = self.usable_files.get(file_id)
        if entry is None:
            return {"error": f"{file_id} does not exist or was captured after audit_at"}
        doc = [r for r in self.index.records if r.evidence_id == file_id and r.scope == "document"]
        if doc:
            text = doc[0].text()
        elif entry["file_path"].endswith((".png", ".pdf")):
            text = "\n".join(r.text() for r in self.index.records if r.evidence_id == file_id)
        else:
            text = (DATASET_DIR / entry["file_path"]).read_text(encoding="utf-8")
        offset = max(offset or 0, 0)
        return {"file_id": file_id, "path": entry["file_path"], "captured_at": entry["captured_at"],
                "origin": entry["origin"], "length": len(text), "offset": offset,
                "text": text[offset:offset + PAGE]}

    def get_rule(self, data_category: str) -> dict:
        row = rules().get(data_category)
        if row:
            return row
        if data_category == "DATA_FLOW":
            return {"data_category": "DATA_FLOW", "required_action": "DISABLE",
                    "condition": "An active sync/import/connector can copy or recreate the subject's data"}
        return {"error": f"unknown category; known: {categories()}"}

    def call(self, name: str, args: dict) -> dict:
        fn = getattr(self, name, None)
        if fn is None or name.startswith("_") or name in ("call", "render", "usable", "usable_evidence_ids"):
            return {"error": f"unknown tool {name}"}
        return fn(**args)


def _clip(v):
    s = v if not isinstance(v, str) else (v if len(v) <= 400 else v[:400] + "…")
    return s


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
