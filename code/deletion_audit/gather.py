"""Deterministic candidate gathering for one case.

Only records usable at the case's audit_at are considered (manifest captured_at
or activity event_time <= audit_at, inclusive). A record never links a resource
to the subject if its identifiers also belong to a different known user.

The same person can file several requests. A record captured inside another
request's [requested_at, audit_at] window for the same subject, and outside this
case's window, belongs to that other request's episode and is not used here.

Channels, each recorded on the candidate as provenance:
    direct         record carries one of the subject's identifier tokens
    name           record text contains the subject's full name
    resource_link  record ties an already-linked resource to another resource
                   (e.g. a search document's source_resource_id, or a sync
                   activity naming the downstream resource it repopulated)
    batch_window   deletion-worker activity on a resource inside the case's
                   request -> report window that no other user is linked to
    batch_run      narrower variant: only the last run of deletion-worker
                   activity (events <= RUN_GAP apart) starting before
                   reported_at + REPORT_GRACE, i.e. the run whose result was
                   reported; same no-other-user filter
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .data import load_dataset, parse_ts
from .evidence_index import EvidenceIndex, Record, build_index
from .identity import IdentityResolver, Subject, resolver

ALL_CHANNELS = ("direct", "name", "resource_link", "batch_run")
MAX_RESOURCES_PER_LINK = 4      # records naming more resources are listings, not links
RESOURCE_LINK_HOPS = 2
WORKFLOW_SOURCE = "deletion_worker"
RUN_GAP = timedelta(minutes=30)
REPORT_GRACE = timedelta(minutes=5)


@dataclass
class Link:
    channel: str
    evidence_id: str
    record_id: str
    via: str = ""


@dataclass
class Candidate:
    resource_id: str
    system_id: str | None
    links: list[Link] = field(default_factory=list)
    # other requests whose reported deletion-worker run contains the same event
    shared_with: set[str] = field(default_factory=set)

    @property
    def channels(self) -> set[str]:
        return {link.channel for link in self.links}


@dataclass
class Gathered:
    case_id: str
    subject: Subject
    audit_at: datetime
    candidates: dict[str, Candidate]


def gather(request: dict, channels=ALL_CHANNELS, index: EvidenceIndex | None = None,
           ids: IdentityResolver | None = None) -> Gathered:
    index = index or build_index()
    ids = ids or resolver()
    subject = ids.resolve(request["email"])
    audit_at = parse_ts(request["audit_at"])
    requested_at = parse_ts(request["requested_at"])
    candidates: dict[str, Candidate] = {}
    other_episodes = [(parse_ts(r["requested_at"]), parse_ts(r["audit_at"]))
                      for r in other_requests(request, subject, ids)]

    def usable(rec: Record) -> bool:
        if rec.captured_at > audit_at:
            return False
        if requested_at <= rec.captured_at:
            return True
        return not any(start <= rec.captured_at <= end for start, end in other_episodes)

    def foreign(rec: Record) -> bool:
        return bool(ids.owners(rec.tokens) - subject.user_ids)

    def add(rid: str, link: Link):
        cand = candidates.setdefault(rid, Candidate(rid, index.system_of(rid)))
        if all((l.channel, l.record_id) != (link.channel, link.record_id) for l in cand.links):
            cand.links.append(link)

    if "direct" in channels:
        for tok in sorted(subject.tokens):
            for rec in index.by_token.get(tok, []):
                if usable(rec) and not foreign(rec):
                    for rid in rec.resource_ids:
                        add(rid, Link("direct", rec.evidence_id, rec.record_id, tok))

    if "name" in channels and subject.names:
        patterns = [(n, re.compile(rf"(?<!\w){re.escape(n)}(?!\w)")) for n in sorted(subject.names)]
        for rec in index.records:
            if rec.kind == "activity" or not usable(rec) or foreign(rec):
                continue
            text = rec.text().lower()
            hit = next((n for n, p in patterns if p.search(text)), None)
            if hit:
                for rid in rec.resource_ids:
                    add(rid, Link("name", rec.evidence_id, rec.record_id, hit))

    if "resource_link" in channels:
        frontier = set(candidates)
        for _ in range(RESOURCE_LINK_HOPS):
            new = set()
            for rid in sorted(frontier):
                for rec in index.by_resource.get(rid, []):
                    if (not usable(rec) or foreign(rec)
                            or len(rec.resource_ids) > MAX_RESOURCES_PER_LINK):
                        continue
                    for other in rec.resource_ids:
                        if other != rid:
                            if other not in candidates:
                                new.add(other)
                            add(other, Link("resource_link", rec.evidence_id, rec.record_id, rid))
            frontier = new

    if "batch_window" in channels:
        start = parse_ts(request["requested_at"])
        end = min(parse_ts(request["reported_at"]), audit_at)
        for rec in index.records:
            if rec.kind != "activity" or not (start <= rec.captured_at <= end):
                continue
            if rec.content["source_system"] != WORKFLOW_SOURCE:
                continue
            rid = rec.content["resource_id"]
            if _linked_to_other_user(rid, index, ids, subject, audit_at):
                continue
            add(rid, Link("batch_window", rec.evidence_id, rec.record_id))

    if "batch_run" in channels:
        members = _run_members(id(index))
        same = {r["case_id"] for r in _all_requests() if same_episode(r, request)}
        run = _case_run(index, request)
        # Sharing is a property of the whole run: if any event in it also belongs to
        # another request's reported run, every identity-free record in it is ambiguous.
        run_shared = set().union(*(members.get(rec.record_id, set()) for rec in run)) - same if run else set()
        for rec in run:
            rid = rec.content["resource_id"]
            if not _linked_to_other_user(rid, index, ids, subject, audit_at):
                add(rid, Link("batch_run", rec.evidence_id, rec.record_id))
                candidates[rid].shared_with |= run_shared

    return Gathered(request["case_id"], subject, audit_at, candidates)


def _all_requests() -> list[dict]:
    ds = load_dataset()
    return ds.sample_requests + ds.requests


def same_episode(a: dict, b: dict) -> bool:
    """Two submissions with the same email and requested_at are one request episode
    (a resubmission), not two competing requests."""
    return (a["case_id"] == b["case_id"]
            or (a["email"].strip().lower() == b["email"].strip().lower()
                and a["requested_at"] == b["requested_at"]))


def other_requests(request: dict, subject: Subject, ids: IdentityResolver) -> list[dict]:
    """Other request episodes filed by the same subject."""
    if not subject.resolved:
        return []
    return [r for r in _all_requests()
            if not same_episode(r, request) and ids.resolve(r["email"]).user_ids & subject.user_ids]


_WORKFLOW_CACHE: dict[int, list[Record]] = {}
_MEMBERS_CACHE: dict[int, dict[str, set[str]]] = {}


def _workflow_events(index: EvidenceIndex) -> list[Record]:
    key = id(index)
    if key not in _WORKFLOW_CACHE:
        _WORKFLOW_CACHE[key] = sorted(
            (r for r in index.records if r.kind == "activity" and r.content["source_system"] == WORKFLOW_SOURCE),
            key=lambda r: r.captured_at)
    return _WORKFLOW_CACHE[key]


def _case_run(index: EvidenceIndex, request: dict) -> list[Record]:
    requested_at = parse_ts(request["requested_at"])
    run = _reported_run(index, parse_ts(request["reported_at"]), parse_ts(request["audit_at"]))
    return [r for r in run if r.captured_at >= requested_at]


def _run_members(index_key: int) -> dict[str, set[str]]:
    """activity record_id -> case_ids whose reported run contains it (all requests)."""
    if index_key not in _MEMBERS_CACHE:
        index = build_index()
        members: dict[str, set[str]] = {}
        for req in _all_requests():
            for rec in _case_run(index, req):
                members.setdefault(rec.record_id, set()).add(req["case_id"])
        _MEMBERS_CACHE[index_key] = members
    return _MEMBERS_CACHE[index_key]


def _reported_run(index: EvidenceIndex, reported_at: datetime, audit_at: datetime) -> list[Record]:
    """The run of deletion-worker events whose outcome the report describes."""
    limit = min(reported_at + REPORT_GRACE, audit_at)
    events = [r for r in _workflow_events(index) if r.captured_at <= limit]
    if not events:
        return []
    run = [events[-1]]
    for rec in reversed(events[:-1]):
        if run[-1].captured_at - rec.captured_at > RUN_GAP:
            break
        run.append(rec)
    return run[::-1]


def _linked_to_other_user(rid, index, ids, subject, audit_at) -> bool:
    for rec in index.by_resource.get(rid, []):
        if rec.captured_at <= audit_at and len(rec.resource_ids) <= MAX_RESOURCES_PER_LINK:
            if ids.owners(rec.tokens) - subject.user_ids:
                return True
    return False
