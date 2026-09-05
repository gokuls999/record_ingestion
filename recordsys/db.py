"""SQLite schema and connection handling."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    value         INTEGER NOT NULL,
    status        TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    ingested_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rejects (
    reject_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id     TEXT,
    source_file   TEXT NOT NULL,
    line_number   INTEGER NOT NULL,
    raw_line      TEXT NOT NULL,
    reason_code   TEXT NOT NULL,
    reason_detail TEXT NOT NULL,
    rejected_at   TEXT NOT NULL,
    run_id        INTEGER NOT NULL REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file      TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    total_lines      INTEGER NOT NULL DEFAULT 0,
    accepted_count   INTEGER NOT NULL DEFAULT 0,
    rejected_count   INTEGER NOT NULL DEFAULT 0,
    reason_breakdown TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_records_source      ON records(source);
CREATE INDEX IF NOT EXISTS idx_records_status      ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_recorded_at ON records(recorded_at);
CREATE INDEX IF NOT EXISTS idx_rejects_reason      ON rejects(reason_code);
CREATE INDEX IF NOT EXISTS idx_rejects_run         ON rejects(run_id);
CREATE INDEX IF NOT EXISTS idx_rejects_record_id   ON rejects(record_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
