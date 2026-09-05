"""Reading back what was stored — both the accepted records and the rejects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import connect


def query_records(
    db_path: str | Path,
    source: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Filters are ANDed. date_from/date_to are inclusive, in stored UTC form."""
    clauses: list[str] = []
    params: list[Any] = []

    if source:
        clauses.append("source = ?")
        params.append(source.strip())
    if status:
        clauses.append("status = ?")
        params.append(status.strip().upper())
    if date_from:
        clauses.append("recorded_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("recorded_at <= ?")
        params.append(date_to)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM records{where} ORDER BY recorded_at, id", params
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def query_rejects(
    db_path: str | Path,
    reason: str | None = None,
    record_id: str | None = None,
    run_id: int | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if reason:
        clauses.append("reason_code = ?")
        params.append(reason.strip().upper())
    if record_id:
        clauses.append("record_id = ?")
        params.append(record_id.strip())
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM rejects{where} ORDER BY run_id, line_number", params
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def query_runs(db_path: str | Path) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs ORDER BY run_id").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
