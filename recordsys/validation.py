"""Rules that decide whether a single incoming record is usable.

Every rejection carries a machine-readable code (for grouping in reports) and a
human-readable detail (for answering "why did record X never show up?").
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

REQUIRED_FIELDS = ("id", "source", "recordedAt", "value", "status")
VALID_STATUSES = ("OK", "WARN", "FAIL")
MIN_VALUE = 0
MAX_VALUE = 100

# Reason codes. One record gets exactly one code, assigned in the order the
# checks below run, so accepted + rejected always partitions the input.
PARSE_ERROR = "PARSE_ERROR"
MISSING_FIELD = "MISSING_FIELD"
INVALID_ID = "INVALID_ID"
INVALID_SOURCE = "INVALID_SOURCE"
INVALID_DATE = "INVALID_DATE"
INVALID_VALUE = "INVALID_VALUE"
INVALID_STATUS = "INVALID_STATUS"
DUPLICATE_ID = "DUPLICATE_ID"

# Tried in order, after datetime.fromisoformat has had a go.
DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d-%b-%Y %H:%M:%S",
    "%Y-%m-%d",
)


class RejectionError(Exception):
    """Raised when a record fails a rule. Carries the code and the detail."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ValidRecord:
    id: str
    source: str
    recorded_at: str
    value: int
    status: str


def format_utc(dt: datetime) -> str:
    """Normalise to a fixed-width UTC string.

    Fixed width matters: it makes lexicographic string comparison equal
    chronological comparison, so SQL range filters and "which is newer" both
    work on the stored text without a date type.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_line(raw_line: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        raise RejectionError(PARSE_ERROR, f"line is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise RejectionError(PARSE_ERROR, f"expected a JSON object, got {type(obj).__name__}")
    return obj


def parse_datetime(raw: Any) -> datetime:
    """Accept the several shapes upstream systems actually send.

    A date with no timezone is read as UTC rather than local time, so results
    do not depend on which machine ran the ingest.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise RejectionError(INVALID_DATE, f"expected a date string, got {raw!r}")

    text = raw.strip()
    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso_candidate)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    raise RejectionError(INVALID_DATE, f"unrecognised date format: {text!r}")


def validate_record(obj: dict[str, Any]) -> ValidRecord:
    missing = [f for f in REQUIRED_FIELDS if obj.get(f) is None]
    if missing:
        raise RejectionError(MISSING_FIELD, f"missing or null: {', '.join(missing)}")

    raw_id = obj["id"]
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise RejectionError(INVALID_ID, f"id must be a non-empty string, got {raw_id!r}")

    raw_source = obj["source"]
    if not isinstance(raw_source, str) or not raw_source.strip():
        raise RejectionError(INVALID_SOURCE, f"source must be a non-empty string, got {raw_source!r}")

    recorded_at = parse_datetime(obj["recordedAt"])

    value = obj["value"]
    # bool is a subclass of int in Python, so it has to be rejected explicitly.
    if isinstance(value, bool):
        raise RejectionError(INVALID_VALUE, f"value must be an integer, got boolean {value!r}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise RejectionError(INVALID_VALUE, f"value must be an integer, got {value!r}")
    if not MIN_VALUE <= value <= MAX_VALUE:
        raise RejectionError(INVALID_VALUE, f"value {value} outside [{MIN_VALUE}, {MAX_VALUE}]")

    raw_status = obj["status"]
    if not isinstance(raw_status, str):
        raise RejectionError(INVALID_STATUS, f"status must be a string, got {raw_status!r}")
    status = raw_status.strip().upper()
    if status not in VALID_STATUSES:
        raise RejectionError(INVALID_STATUS, f"status {raw_status!r} is not one of {list(VALID_STATUSES)}")

    return ValidRecord(
        id=raw_id.strip(),
        source=raw_source.strip(),
        recorded_at=format_utc(recorded_at),
        value=value,
        status=status,
    )
