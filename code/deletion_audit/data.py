"""Loaders for the participant-facing CSV files."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache

from .paths import DATASET_DIR


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp such as 2026-09-15T12:00:00Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def read_csv(name: str) -> list[dict]:
    with (DATASET_DIR / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass(frozen=True)
class Dataset:
    requests: list[dict]
    sample_requests: list[dict]
    sample_output: list[dict]
    identities: list[dict]
    audit_log: list[dict]
    activity: list[dict]
    rules: list[dict]
    manifest: list[dict]

    def request(self, case_id: str) -> dict:
        for row in self.requests + self.sample_requests:
            if row["case_id"] == case_id:
                return row
        raise KeyError(case_id)


@cache
def load_dataset() -> Dataset:
    return Dataset(
        requests=read_csv("deletion_requests.csv"),
        sample_requests=read_csv("sample_deletion_requests.csv"),
        sample_output=read_csv("sample_output.csv"),
        identities=read_csv("user_identities.csv"),
        audit_log=read_csv("user_audit_log.csv"),
        activity=read_csv("system_activity.csv"),
        rules=read_csv("deletion_rules.csv"),
        manifest=read_csv("evidence_files.csv"),
    )
