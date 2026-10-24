"""Identifier extraction and normalisation.

Identifiers are represented as typed tokens so different kinds never collide:
    email:<lowercased address>
    hash:<64 hex chars>          (email_hash = sha256(lowercased email))
    phone:<digits only>
    user:<user_id>
"""

import hashlib
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
PHONE_RE = re.compile(r"\+\d[\d \-().]{5,22}\d")
MIN_PHONE_DIGITS = 8


def email_token(email: str) -> str:
    return "email:" + email.strip().lower()


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def phone_token(phone: str) -> str:
    return "phone:" + phone_digits(phone)


def extract_tokens(text: str) -> set[str]:
    """All identifier tokens found anywhere in a piece of text."""
    tokens = {email_token(m) for m in EMAIL_RE.findall(text)}
    tokens |= {"user:" + m for m in USER_RE.findall(text)}
    tokens |= {"hash:" + m for m in HASH_RE.findall(text.lower())}
    for m in PHONE_RE.findall(text):
        digits = phone_digits(m)
        if len(digits) >= MIN_PHONE_DIGITS:
            tokens.add("phone:" + digits)
    return tokens


def resource_regex(prefixes: set[str]) -> re.Pattern:
    """Regex matching resource ids such as account_4248 or data_flow_9522."""
    alternation = "|".join(sorted((re.escape(p) for p in prefixes), key=len, reverse=True))
    return re.compile(rf"\b(?:{alternation})_\d+\b")
