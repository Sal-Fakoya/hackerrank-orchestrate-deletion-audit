"""Edge-case coverage check over the 518 eval requests and all evidence. No LLM calls.

    python code/evaluation/coverage.py          -> prints + writes results/coverage.md
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deletion_audit.data import load_dataset, parse_ts                 # noqa: E402
from deletion_audit.evidence_index import build_index                  # noqa: E402
from deletion_audit.gather import _all_requests                        # noqa: E402
from deletion_audit.identifiers import email_token                     # noqa: E402
from deletion_audit.identity import resolver                           # noqa: E402
from deletion_audit.paths import EVALUATION_DIR                        # noqa: E402

INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above)|disregard|system prompt|you are (an?|the) (ai|assistant|model)"
    r"|as an ai|mark (this|the case|it) (as )?complete|output (only|the following)|<\s*/?\s*(system|instructions?)\s*>"
    r"|new instructions|override|jailbreak|developer mode|respond with|set (deletion_)?status", re.I)
THIRD_PARTY = re.compile(
    r"\b(my|our) (late |deceased )?(mother|father|mom|dad|husband|wife|partner|son|daughter|child|children|"
    r"brother|sister|friend|client|customer|employee|colleague|parent|grandmother|grandfather)\b"
    r"|on behalf of|\b(his|her|their) (account|data|profile)\b|power of attorney|guardian", re.I)
WITHDRAWN = re.compile(
    r"withdraw|cancel (my|the|that) (request|deletion)|never ?mind|changed my mind|don'?t (delete|proceed)"
    r"|stop the deletion|undo|rescind|disregard my (earlier|previous)", re.I)
PARTIAL = re.compile(
    r"\bonly\b|\bjust (the|my)\b|single|one (item|order|photo|card|address)|payment card|saved card|"
    r"(delete|remove|erase) (my|the|this) (old )?(card|address|photo|review|order|comment)", re.I)


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?。])\s*", text) if s]


def text_patterns(reqs):
    out = {}
    for name, rx in [("prompt injection in request_text", INJECTION), ("third-party request", THIRD_PARTY),
                     ("withdrawn / cancelled deletion", WITHDRAWN), ("partial deletion of a single item", PARTIAL)]:
        hits = [(r["case_id"], m.group(0), r["request_text"][:140]) for r in reqs if (m := rx.search(r["request_text"]))]
        out[name] = hits
    return out


def evidence_injection(index):
    hits = []
    for rec in index.records:
        if rec.kind != "activity" and INJECTION.search(rec.text()):
            hits.append((rec.evidence_id, INJECTION.search(rec.text()).group(0)))
    return sorted(set(hits))


def unknown_emails(reqs, ids):
    return [(r["case_id"], r["email"]) for r in reqs if not ids.resolve(r["email"]).resolved]


def cross_user_emails(ds, ids):
    """Emails that are historical for one user and current for another."""
    current = {r["email"].strip().lower(): r["user_id"] for r in ds.identities}
    out = []
    for a in ds.audit_log:
        if a["field_name"] != "email":
            continue
        for v in (a["old_value"], a["new_value"]):
            owner_now = current.get(v.strip().lower())
            if owner_now and owner_now != a["user_id"]:
                out.append((v.lower(), a["user_id"], owner_now, a["audit_id"]))
    multi = [(t.split(":", 1)[1], sorted(u)) for t, u in ids.owners_of.items() if t.startswith("email:") and len(u) > 1]
    return sorted(set(out)), multi


def expired_retention(index, reqs, ids):
    """Retained records whose stated retention end is before the audit_at of a request
    whose subject they reference (and globally)."""
    rx = re.compile(r"retention_until\W+(\d{4}-\d{2}-\d{2}T[\d:]+Z)|retain(?:ed)?[ _]until\W+(\d{4}-\d{2}-\d{2}T[\d:]+Z)", re.I)
    ends = defaultdict(set)
    for rec in index.records:
        for m in rx.finditer(rec.text()):
            ends[rec.resource_ids[0] if rec.resource_ids else rec.evidence_id].add(m.group(1) or m.group(2))
    earliest = min(ts for v in ends.values() for ts in v) if ends else None
    case_hits = []
    for r in reqs:
        s = ids.resolve(r["email"])
        audit_at = parse_ts(r["audit_at"])
        for tok in s.tokens:
            for rec in index.by_token.get(tok, []):
                for rid in rec.resource_ids:
                    for ts in ends.get(rid, ()):
                        if parse_ts(ts) < audit_at:
                            case_hits.append((r["case_id"], rid, ts))
    legal_text = [(rec.evidence_id, rec.text()[:120]) for rec in index.records
                  if rec.kind in ("pdf", "image", "text") and re.search(r"retain until|legal hold", rec.text(), re.I)
                  and rec.scope == "item"]
    return ends, earliest, sorted(set(case_hits)), legal_text


def unblockable_backups(index):
    rx = re.compile(r"cannot be blocked|can ?not block|immutable|worm|object.lock|block(ing)? (is )?not supported"
                    r"|restore.{0,40}(unblockable|irreversible)", re.I)
    return sorted({(rec.evidence_id, rx.search(rec.text()).group(0)) for rec in index.records if rx.search(rec.text())})


def recreated(index, reqs):
    """A resource with a DELETE_RECORD event followed by a write (sync, restore,
    repopulate, index, update) before some request's audit_at."""
    acts = sorted((r for r in index.records if r.kind == "activity"), key=lambda r: r.captured_at)
    deleted_at = {}
    events = []
    writes = {"SYNC_RECORD", "RESTORE_BACKUP", "INDEX_RECORD", "UPDATE_RECORD", "REFRESH_CACHE"}
    for rec in acts:
        c = rec.content
        for rid in rec.resource_ids:
            if c["event_type"] == "DELETE_RECORD" and c["resource_id"] == rid:
                deleted_at[rid] = rec
            elif c["event_type"] in writes and rid in deleted_at:
                events.append((rid, deleted_at[rid].evidence_id, deleted_at[rid].content["reported_result"],
                               rec.evidence_id, c["event_type"], c["details"][:90]))
    repopulate = [(r.evidence_id, r.content["details"][:110]) for r in acts if "repopulat" in r.content["details"].lower()
                  or r.content["event_type"] == "RESTORE_BACKUP"]
    return events, repopulate


def replica_drift(index):
    """SQL snapshots where a resource is in one table but not another table of the same
    file, or the same resource differs between tables."""
    by_file = defaultdict(lambda: defaultdict(dict))
    for rec in index.records:
        if rec.kind == "sql" and isinstance(rec.content.get("row"), dict) and rec.content["row"].get("resource_id"):
            row = dict(rec.content["row"])
            row.pop("updated_at", None)
            by_file[rec.evidence_id][rec.content["table"]][row["resource_id"]] = json.dumps(row, sort_keys=True)
    out = []
    for ev, tables in sorted(by_file.items()):
        groups = defaultdict(list)
        for t in tables:
            base = re.sub(r"_(primary|read_replica|replica_[a-z_]+|reporting_replica|failover_replica)$", "", t)
            groups[base].append(t)
        for base, ts in groups.items():
            if len(ts) < 2:
                continue
            ids = set().union(*(tables[t].keys() for t in ts))
            for rid in sorted(ids):
                present = [t for t in ts if rid in tables[t]]
                if len(present) != len(ts):
                    out.append((ev, rid, "missing from " + ", ".join(sorted(set(ts) - set(present)))))
                else:
                    payloads = {tables[t][rid] for t in ts}
                    if len(payloads) > 1:
                        out.append((ev, rid, "differs across " + ", ".join(ts)))
    return out


def main():
    ds, index, ids = load_dataset(), build_index(), resolver()
    reqs = ds.requests
    lines = ["# Edge-case coverage (518 eval requests + all evidence, no LLM calls)", ""]
    rows = []

    tp = text_patterns(reqs)
    ev_inj = evidence_injection(index)
    unk = unknown_emails(reqs, ids)
    cross, multi = cross_user_emails(ds, ids)
    ends, earliest, expired_cases, legal_text = expired_retention(index, reqs, ids)
    unblock = unblockable_backups(index)
    recre, repop = recreated(index, reqs)
    drift = replica_drift(index)

    def add(name, n, note):
        rows.append(f"| {name} | {n} | {note} |")

    add("prompt injection in request_text", len(tp["prompt injection in request_text"]), "regex over all 518 texts")
    add("prompt injection inside evidence", len(ev_inj), ", ".join(f"{e}:{m}" for e, m in ev_inj[:5]) or "-")
    for name in ("third-party request", "withdrawn / cancelled deletion", "partial deletion of a single item"):
        add(name, len(tp[name]), "; ".join(f"{c} ({m})" for c, m, _ in tp[name][:6]) or "-")
    add("request email matches no identity", len(unk), ", ".join(f"{c}" for c, _ in unk[:8]) or "-")
    add("email historical for one user, current for another", len(cross),
        "; ".join(f"{e}: hist {u1}, current {u2}" for e, u1, u2, _ in cross[:4]) or "-")
    add("retention end before the audit_at of a linked request", len(expired_cases),
        f"earliest stated retention end in evidence: {earliest}")
    add("backup restore stated as not blockable", len(unblock), ", ".join(f"{e}:{m}" for e, m in unblock[:5]) or "-")
    add("deleted then rewritten by sync/index/restore (activity)", len(recre),
        f"{len({r[0] for r in recre})} resources; {len(repop)} 'repopulated'/restore events")
    add("primary vs replica disagreement in one SQL snapshot", len(drift),
        f"{len({d[0] for d in drift})} files, {len({d[1] for d in drift})} resources")

    lines += ["| pattern | occurrences | notes |", "|---|---|---|", *rows, "", "## Details", ""]
    lines += ["### Request-text matches (all)", ""]
    for name, hits in tp.items():
        lines.append(f"**{name}:** " + ("; ".join(f"{c} matched '{m}': {t}" for c, m, t in hits) or "none"))
        lines.append("")
    lines += ["### Emails shared across users", ""]
    lines += [f"- {e}: historical for {u1}, current for {u2} ({a})" for e, u1, u2, a in cross] or ["- none"]
    lines += [f"- token owned by several users: {e} -> {u}" for e, u in multi]
    lines += ["", "### Retention", "",
              f"- records with a stated retention end: {len(ends)}; earliest end: {earliest}",
              *[f"- {c}: {rid} retention ended {ts}" for c, rid, ts in expired_cases[:20]],
              *[f"- retention text in {e}: {t}" for e, t in legal_text[:12]]]
    lines += ["", "### Deleted then rewritten (first 25)", ""]
    lines += [f"- {rid}: delete {d} ({res}) then {w} {et}: {det}" for rid, d, res, w, et, det in recre[:25]]
    lines += ["", "### Repopulate / restore events", ""] + [f"- {e}: {d}" for e, d in repop[:20]]
    lines += ["", "### Replica drift (first 30)", ""] + [f"- {e} {rid}: {why}" for e, rid, why in drift[:30]]
    text = "\n".join(lines) + "\n"
    (EVALUATION_DIR / "results").mkdir(exist_ok=True)
    (EVALUATION_DIR / "results" / "coverage.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
