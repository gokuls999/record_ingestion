"""Generate the sample records file.

Deterministic (fixed seed) so the committed file can be regenerated and diffed.
The awkward records are given descriptive ids — dup-*, miss-*, date-*, val-*,
status-*, blank-*, broken-* — so each rejection path can be found by name:

    python -m recordsys rejects --id val-0001

Run: python scripts/generate_sample.py
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260314
CLEAN_COUNT = 200
OUTPUT = Path(__file__).resolve().parent.parent / "sample_records.jsonl"

SOURCES = ("alpha", "beta", "gamma", "delta")
STATUSES = ("OK", "WARN", "FAIL")
EPOCH = datetime(2026, 3, 1, tzinfo=timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_offset(dt: datetime) -> str:
    shifted = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return shifted.strftime("%Y-%m-%dT%H:%M:%S+05:30")


def spaced(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def day_first(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y %H:%M:%S")


DATE_WRITERS = (iso_z, iso_offset, spaced, day_first)


def clean_records(rng: random.Random) -> list[str]:
    lines = []
    for n in range(1, CLEAN_COUNT + 1):
        moment = EPOCH + timedelta(minutes=rng.randrange(0, 60 * 24 * 30))
        writer = DATE_WRITERS[n % len(DATE_WRITERS)]
        lines.append(
            json.dumps(
                {
                    "id": f"r-{n:04d}",
                    "source": rng.choice(SOURCES),
                    "recordedAt": writer(moment),
                    "value": rng.randrange(0, 101),
                    "status": rng.choice(STATUSES),
                }
            )
        )
    return lines


def awkward_records() -> list[str]:
    """Every category the brief asks for, plus a few of my own."""
    base = EPOCH + timedelta(days=10, hours=9)
    rows: list[str] = []

    def add(**fields) -> None:
        rows.append(json.dumps(fields))

    # Same id, byte-identical, twice over. Expect: one stored, one DUPLICATE_ID.
    for _ in range(2):
        add(id="dup-0001", source="alpha", recordedAt=iso_z(base), value=40, status="OK")
    # Same id again, newer timestamp and a different value. Expect: this one wins.
    add(id="dup-0001", source="alpha", recordedAt=iso_z(base + timedelta(hours=2)), value=77, status="WARN")

    # Same id, same timestamp, different values — a genuine tie. Later line wins.
    add(id="dup-0002", source="beta", recordedAt=iso_z(base), value=10, status="OK")
    add(id="dup-0002", source="beta", recordedAt=iso_z(base), value=90, status="FAIL")

    # Newer record appears FIRST in the file. Proves the rule is "newest
    # recordedAt", not "last line seen".
    add(id="dup-0003", source="gamma", recordedAt=iso_z(base + timedelta(days=1)), value=55, status="OK")
    add(id="dup-0003", source="gamma", recordedAt=iso_z(base), value=22, status="FAIL")

    # Missing fields, and a null that means the same thing.
    add(id="miss-0001", source="alpha", recordedAt=iso_z(base), value=30)
    add(id="miss-0002", source="beta", recordedAt=iso_z(base), status="OK")
    rows.append(json.dumps({"id": "miss-0003", "source": None, "recordedAt": iso_z(base), "value": 5, "status": "OK"}))

    # Unusable dates.
    add(id="date-0001", source="alpha", recordedAt="14th of March, 2026", value=12, status="OK")
    add(id="date-0002", source="alpha", recordedAt="   ", value=12, status="OK")
    add(id="date-0003", source="alpha", recordedAt="2026-13-45T99:99:99Z", value=12, status="OK")

    # Values that are not integers in [0, 100].
    add(id="val-0001", source="alpha", recordedAt=iso_z(base), value=150, status="OK")
    add(id="val-0002", source="beta", recordedAt=iso_z(base), value=-5, status="OK")
    add(id="val-0003", source="gamma", recordedAt=iso_z(base), value=42.5, status="OK")
    add(id="val-0004", source="delta", recordedAt=iso_z(base), value="42", status="OK")
    add(id="val-0005", source="alpha", recordedAt=iso_z(base), value=True, status="OK")

    # Statuses off the list.
    add(id="status-0001", source="alpha", recordedAt=iso_z(base), value=20, status="PENDING")
    add(id="status-0002", source="beta", recordedAt=iso_z(base), value=20, status="")

    # Empty and whitespace-only text.
    add(id="   ", source="alpha", recordedAt=iso_z(base), value=20, status="OK")
    add(id="blank-0001", source="", recordedAt=iso_z(base), value=20, status="OK")
    add(id="blank-0002", source="   ", recordedAt=iso_z(base), value=20, status="OK")

    # Accepted, but only because of a deliberate leniency — see ASSUMPTIONS.md.
    add(id="lenient-0001", source="alpha", recordedAt=iso_z(base), value=33, status="ok")
    add(id="lenient-0002", source="  beta  ", recordedAt=iso_z(base), value=44, status=" WARN ")
    add(id="lenient-0003", source="alpha", recordedAt=iso_z(base), value=55.0, status="OK")
    add(id="lenient-0004", source="alpha", recordedAt=iso_z(base), value=66, status="OK", region="EMEA")

    # Not JSON at all, and JSON that is not a record.
    rows.append('{"id": "broken-0001", "source": "alpha", "recordedAt": ')
    rows.append('["not", "an", "object"]')

    return rows


def main() -> None:
    rng = random.Random(SEED)
    lines = clean_records(rng)
    for row in awkward_records():
        lines.insert(rng.randrange(0, len(lines) + 1), row)

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} lines to {OUTPUT.name}")


if __name__ == "__main__":
    main()
