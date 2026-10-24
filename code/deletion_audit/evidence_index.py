"""Parse every participant evidence file (and the activity stream) into Records.

A Record is the smallest unit that can tie identifiers to resources:
    sql      one table row (record_json blobs are decoded)
    json     one object that carries a resource_id
    log      one trace (all lines sharing a trace id) or one untraced line
    text     one line of a plain-text record
    pdf/image one line, plus one whole-document record
    activity one system_activity row

Every Record keeps the manifest captured_at of its file. Timestamps inside a
file are kept as content but never used for the audit_at cutoff.
"""

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache

from pypdf import PdfReader

from . import image_text
from .data import Dataset, load_dataset, parse_ts
from .identifiers import extract_tokens, resource_regex
from .paths import DATASET_DIR

TRACE_RE = re.compile(r"\b(?:tr|rb)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")
# Activity rows that record the intake classifier's own verdict. They are kept
# out of the index so they can only serve as a post-hoc cross-check.
EXCLUDED_EVENT_TYPES = {"CLASSIFY_REQUEST"}
# Synthetic evidence for custom evaluation cases only (code/evaluation/custom_cases.py
# sets this in its own process before the index is built). Never set for real runs.
EXTRA_MANIFEST: list[dict] = []


@dataclass
class Record:
    record_id: str
    evidence_id: str            # file_id or activity_id, as cited in steps_json
    captured_at: datetime       # manifest captured_at, or activity event_time
    kind: str
    locator: str
    resource_ids: tuple[str, ...]
    tokens: frozenset[str]
    content: object
    system_hint: str | None = None
    scope: str = "item"         # "item" or "document"

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False, sort_keys=True)


@dataclass
class EvidenceIndex:
    records: list[Record]
    by_resource: dict[str, list[Record]] = field(default_factory=dict)
    by_token: dict[str, list[Record]] = field(default_factory=dict)
    system_for_prefix: dict[str, str] = field(default_factory=dict)
    files: dict[str, dict] = field(default_factory=dict)

    def system_of(self, resource_id: str) -> str | None:
        return self.system_for_prefix.get(resource_id.rsplit("_", 1)[0])


# ---------------------------------------------------------------- parsers

def _make(ev, captured, kind, locator, payload, rx, system_hint=None, scope="item"):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    resources = tuple(dict.fromkeys(rx.findall(text)))
    tokens = frozenset(extract_tokens(text))
    if not resources and not tokens:
        return None
    return Record(f"{ev}#{locator}", ev, captured, kind, locator, resources, tokens, payload, system_hint, scope)


def _parse_sql(ev, captured, path, rx):
    script = path.read_text(encoding="utf-8")
    meta = dict(re.findall(r"^--\s*([A-Za-z ]+):\s*(.+)$", script, re.M))
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(script)
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            for i, row in enumerate(con.execute(f'SELECT * FROM "{table}"')):
                data = dict(row)
                if isinstance(data.get("record_json"), str):
                    data["record_json"] = json.loads(data["record_json"])
                payload = {"table": table, "topology": meta.get("Database topology"), "row": data}
                locator = f"{table}:{data.get('resource_id', i)}"
                system = data.get("origin_system")
                rec = _make(ev, captured, "sql", locator, payload, rx, system)
                if rec:
                    yield rec
    finally:
        con.close()


_CONTEXT_KEYS = {"captured_at", "exported_at", "data_store", "collection", "source", "origin", "region"}


def _parse_json(ev, captured, path, rx):
    root = json.loads(path.read_text(encoding="utf-8"))

    def walk(node, trail, context):
        if isinstance(node, dict):
            context = {**context, **{k: v for k, v in node.items() if k in _CONTEXT_KEYS and isinstance(v, str)}}
            if "resource_id" in node:
                system = node.get("system") or node.get("data_store") or _service_from_trail(trail)
                payload = {"path": "/".join(trail), "context": context, "record": node}
                rec = _make(ev, captured, "json", "/".join(trail) or "root", payload, rx, system)
                if rec:
                    yield rec
            for key, value in node.items():
                yield from walk(value, trail + [str(key)], context)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                yield from walk(value, trail + [str(i)], context)

    yield from walk(root, [], {})


def _service_from_trail(trail):
    if len(trail) >= 2 and trail[0] == "service_exports":
        return trail[1]
    return None


def _parse_log(ev, captured, path, rx):
    traces = defaultdict(list)
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        clean = line.replace('\\"', '"')
        m = TRACE_RE.search(clean)
        if m:
            traces[m.group(0)].append((n, clean))
        else:
            rec = _make(ev, captured, "log", f"line:{n}", clean, rx, _log_service(clean))
            if rec:
                yield rec
    for trace_id, lines in traces.items():
        payload = "\n".join(text for _, text in lines)
        rec = _make(ev, captured, "log", f"trace:{trace_id}", payload, rx, _log_service(lines[0][1]))
        if rec:
            yield rec


def _log_service(line):
    m = re.search(r'(?:service|function|logger)[=:"]+"?([a-z_]+)', line)
    return m.group(1) if m else None


def _parse_lines(ev, captured, kind, text, rx):
    """Line records plus one whole-document record (text, PDF, image)."""
    for n, line in enumerate(text.splitlines(), 1):
        rec = _make(ev, captured, kind, f"line:{n}", line.strip(), rx)
        if rec:
            yield rec
    doc = _make(ev, captured, kind, "document", text, rx, scope="document")
    if doc:
        yield doc


def _pdf_text(path):
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _parse_activity(row, rx):
    payload = {k: row[k] for k in ("activity_id", "event_time", "event_type", "source_system",
                                   "target_system", "resource_id", "reported_result", "details")}
    rec = _make(row["activity_id"], parse_ts(row["event_time"]), "activity", row["activity_id"],
                payload, rx, row["target_system"])
    if rec and row["resource_id"] in rec.resource_ids:
        # keep the row's own resource first
        rec.resource_ids = (row["resource_id"],) + tuple(r for r in rec.resource_ids if r != row["resource_id"])
    return rec


# ---------------------------------------------------------------- build

def _resource_prefixes(ds: Dataset) -> set[str]:
    return {row["resource_id"].rsplit("_", 1)[0] for row in ds.activity if row["resource_id"]}


@cache
def build_index() -> EvidenceIndex:
    ds = load_dataset()
    rx = resource_regex(_resource_prefixes(ds))
    records: list[Record] = []
    files = {}
    for entry in ds.manifest + EXTRA_MANIFEST:
        ev, captured = entry["file_id"], parse_ts(entry["captured_at"])
        path = DATASET_DIR / entry["file_path"]
        files[ev] = entry
        suffix = path.suffix.lower()
        if suffix == ".sql":
            records += _parse_sql(ev, captured, path, rx)
        elif suffix == ".json":
            records += _parse_json(ev, captured, path, rx)
        elif suffix == ".log":
            records += _parse_log(ev, captured, path, rx)
        elif suffix == ".txt":
            records += _parse_lines(ev, captured, "text", path.read_text(encoding="utf-8"), rx)
        elif suffix == ".pdf":
            records += _parse_lines(ev, captured, "pdf", _pdf_text(path), rx)
        elif suffix == ".png":
            text = image_text.cached_text(ev)
            if text is None:
                raise RuntimeError(f"{ev}: image not transcribed; run python -m deletion_audit.image_text")
            records += _parse_lines(ev, captured, "image", text, rx)
        else:
            raise ValueError(f"unsupported evidence type: {path}")
    for row in ds.activity:
        if row["event_type"] in EXCLUDED_EVENT_TYPES:
            continue
        rec = _parse_activity(row, rx)
        if rec:
            records.append(rec)

    index = EvidenceIndex(records=records, files=files)
    by_resource, by_token = defaultdict(list), defaultdict(list)
    for rec in records:
        for rid in rec.resource_ids:
            by_resource[rid].append(rec)
        for tok in rec.tokens:
            by_token[tok].append(rec)
    index.by_resource, index.by_token = dict(by_resource), dict(by_token)
    index.system_for_prefix = _infer_systems(records)
    return index


def _infer_systems(records: list[Record]) -> dict[str, str]:
    """Map resource prefix -> system_id using systems that structured evidence
    states for a record's own resource. Log emitters and activity targets name
    the calling service rather than the owning one, so they are only a fallback."""
    votes, fallback = defaultdict(Counter), defaultdict(Counter)
    for rec in records:
        if not rec.system_hint or not rec.resource_ids or rec.scope == "document":
            continue
        prefix = rec.resource_ids[0].rsplit("_", 1)[0]
        (fallback if rec.kind in ("activity", "log") else votes)[prefix][rec.system_hint] += 1
    # Plain-text lines state "<system> | <resource>" explicitly.
    for rec in records:
        if rec.kind in ("text", "pdf", "image") and rec.scope == "item":
            m = re.search(r"\|\s*([a-z_]+_(?:service|platform))\s*\|\s*([a-z_]+)_\d+", rec.text())
            if m:
                votes[m.group(2)][m.group(1)] += 1
    out = {}
    for prefix in set(votes) | set(fallback):
        counter = votes.get(prefix) or fallback[prefix]
        out[prefix] = counter.most_common(1)[0][0]
    return out
