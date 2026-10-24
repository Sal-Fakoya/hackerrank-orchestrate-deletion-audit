"""Resolve a request email to the subject's current and historical identifiers."""

from collections import defaultdict
from dataclasses import dataclass
from functools import cache

from .data import load_dataset
from .identifiers import email_hash, email_token, phone_token


@dataclass(frozen=True)
class Subject:
    request_email: str
    user_ids: frozenset[str]
    tokens: frozenset[str]
    names: frozenset[str]
    audit_ids: tuple[str, ...]      # audit rows that contributed historical identifiers

    @property
    def resolved(self) -> bool:
        return bool(self.user_ids)


def _field_tokens(field_name: str, value: str) -> set[str]:
    value = value.strip()
    if not value:
        return set()
    if field_name == "email":
        return {email_token(value), "hash:" + email_hash(value)}
    if field_name == "email_hash":
        return {"hash:" + value.lower()}
    if field_name == "phone":
        return {phone_token(value)}
    return set()


class IdentityResolver:
    """Token -> owning user_ids, built from the identity snapshot and audit log."""

    def __init__(self):
        ds = load_dataset()
        self.user_tokens: dict[str, set[str]] = defaultdict(set)
        self.user_names: dict[str, set[str]] = defaultdict(set)
        self.user_audit: dict[str, list[str]] = defaultdict(list)
        for row in ds.identities:
            uid = row["user_id"]
            self.user_tokens[uid] |= {"user:" + uid}
            for name in ("email", "email_hash", "phone"):
                self.user_tokens[uid] |= _field_tokens(name, row[name])
            self.user_names[uid].add(row["name"].strip().lower())
        for row in ds.audit_log:
            uid = row["user_id"]
            self.user_tokens[uid] |= {"user:" + uid}
            for value in (row["old_value"], row["new_value"]):
                self.user_tokens[uid] |= _field_tokens(row["field_name"], value)
            self.user_audit[uid].append(row["audit_id"])
        self.owners_of: dict[str, set[str]] = defaultdict(set)
        for uid, tokens in self.user_tokens.items():
            for tok in tokens:
                self.owners_of[tok].add(uid)

    def owners(self, tokens) -> set[str]:
        out = set()
        for tok in tokens:
            out |= self.owners_of.get(tok, set())
        return out

    def resolve(self, request_email: str) -> Subject:
        tok = email_token(request_email)
        users = set(self.owners_of.get(tok, set()))
        tokens = {tok, "hash:" + email_hash(request_email)}
        names, audit_ids = set(), []
        for uid in sorted(users):
            tokens |= self.user_tokens[uid]
            names |= self.user_names[uid]
            audit_ids += self.user_audit[uid]
        return Subject(request_email, frozenset(users), frozenset(tokens), frozenset(names), tuple(audit_ids))


@cache
def resolver() -> IdentityResolver:
    return IdentityResolver()
