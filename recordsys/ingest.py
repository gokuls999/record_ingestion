"""Reading a file, deciding what is usable, and storing the result.

The run happens in two stages:

1. Each line is validated on its own. Anything that fails a field rule is
   rejected immediately and never reaches stage 2.
2. Survivors are reconciled by id — first against each other (the same file can
   carry the same id more than once), then against what is already stored.

Both reconciliations use the same rule: the highest recordedAt wins. Applying it
against stored data is also what makes a second run over the same file a no-op,
since every incoming record ties the one already there.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect
from .validation import DUPLICATE_ID, RejectionError, ValidRecord, parse_line, validate_record


@dataclass(frozen=True)
class Candidate:
    """A record that passed field validation, with where it came from."""

    record: ValidRecord
    line_number: int
    raw_line: str


@dataclass
class IngestReport:
    run_id: int
    source_file: str
    total_lines: int
    accepted: int
    rejected: int
    reasons: dict[str, int]

    def format(self) -> str:
        lines = [
            f"Run #{self.run_id} - {self.source_file}",
            f"  records read: {self.total_lines}",
            f"  accepted:     {self.accepted}",
            f"  rejected:     {self.rejected}",
        ]
        if self.reasons:
            lines.append("  rejected by reason:")
            width = max(len(code) for code in self.reasons)
            for code, count in sorted(self.reasons.items(), key=lambda kv: (-kv[1], kv[0])):
                lines.append(f"    {code.ljust(width)}  {count}")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ingest_file(db_path: str | Path, file_path: str | Path) -> IngestReport:
    source_file = str(file_path)
    conn = connect(db_path)
    try:
        return _run(conn, source_file)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, source_file: str) -> IngestReport:
    cursor = conn.execute(
        "INSERT INTO runs (source_file, started_at) VALUES (?, ?)",
        (source_file, _now()),
    )
    run_id = cursor.lastrowid
    reasons: dict[str, int] = defaultdict(int)

    def reject(line_number: int, raw_line: str, code: str, detail: str, record_id: str | None) -> None:
        reasons[code] += 1
        conn.execute(
            """INSERT INTO rejects
               (record_id, source_file, line_number, raw_line, reason_code, reason_detail, rejected_at, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (record_id, source_file, line_number, raw_line, code, detail, _now(), run_id),
        )

    candidates: dict[str, Candidate] = {}
    total_lines = 0

    with open(source_file, encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            total_lines += 1

            parsed: Any = None
            try:
                parsed = parse_line(line)
                record = validate_record(parsed)
            except RejectionError as exc:
                reject(line_number, line, exc.code, exc.detail, _id_hint(parsed))
                continue

            seen = candidates.get(record.id)
            if seen is None:
                candidates[record.id] = Candidate(record, line_number, line)
            elif record.recorded_at >= seen.record.recorded_at:
                # Later line wins ties, so the last version in the file survives.
                reject(
                    seen.line_number,
                    seen.raw_line,
                    DUPLICATE_ID,
                    f"superseded within {Path(source_file).name} by line {line_number} "
                    f"(recordedAt {record.recorded_at} >= {seen.record.recorded_at})",
                    seen.record.id,
                )
                candidates[record.id] = Candidate(record, line_number, line)
            else:
                reject(
                    line_number,
                    line,
                    DUPLICATE_ID,
                    f"superseded within {Path(source_file).name} by line {seen.line_number} "
                    f"(recordedAt {seen.record.recorded_at} > {record.recorded_at})",
                    record.id,
                )

    accepted = 0
    for candidate in candidates.values():
        record = candidate.record
        stored = conn.execute(
            "SELECT recorded_at, first_seen_at FROM records WHERE id = ?", (record.id,)
        ).fetchone()
        timestamp = _now()

        if stored is None:
            conn.execute(
                """INSERT INTO records (id, source, recorded_at, value, status, first_seen_at, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.source, record.recorded_at, record.value, record.status, timestamp, timestamp),
            )
            accepted += 1
        elif record.recorded_at > stored["recorded_at"]:
            conn.execute(
                """UPDATE records SET source = ?, recorded_at = ?, value = ?, status = ?, ingested_at = ?
                   WHERE id = ?""",
                (record.source, record.recorded_at, record.value, record.status, timestamp, record.id),
            )
            accepted += 1
        else:
            reject(
                candidate.line_number,
                candidate.raw_line,
                DUPLICATE_ID,
                f"already stored with recordedAt {stored['recorded_at']}, "
                f"incoming {record.recorded_at} is not newer",
                record.id,
            )

    rejected = sum(reasons.values())
    conn.execute(
        """UPDATE runs SET finished_at = ?, total_lines = ?, accepted_count = ?,
                           rejected_count = ?, reason_breakdown = ?
           WHERE run_id = ?""",
        (_now(), total_lines, accepted, rejected, json.dumps(dict(reasons), sort_keys=True), run_id),
    )
    conn.commit()

    return IngestReport(
        run_id=run_id,
        source_file=source_file,
        total_lines=total_lines,
        accepted=accepted,
        rejected=rejected,
        reasons=dict(reasons),
    )


def _id_hint(parsed: Any) -> str | None:
    """Best-effort id for a rejected record, so rejects stay searchable by id."""
    if isinstance(parsed, dict):
        candidate = parsed.get("id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None
