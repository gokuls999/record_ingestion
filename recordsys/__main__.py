"""Command-line entry point: ingest, query, rejects, runs."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .ingest import ingest_file
from .query import query_records, query_rejects, query_runs
from .validation import RejectionError, format_utc, parse_datetime

DEFAULT_DB = "records.db"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recordsys", description="Record ingestion and reporting")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file to use (default: {DEFAULT_DB})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="read a records file and store what passes")
    ingest.add_argument("file", help="path to a JSON Lines file")

    query = subparsers.add_parser("query", help="search stored records")
    query.add_argument("--source")
    query.add_argument("--status", help="OK, WARN or FAIL")
    query.add_argument("--from", dest="date_from", metavar="DATE", help="inclusive lower bound on recordedAt")
    query.add_argument("--to", dest="date_to", metavar="DATE", help="inclusive upper bound on recordedAt")
    query.add_argument("--json", action="store_true", help="emit JSON instead of a table")

    rejects = subparsers.add_parser("rejects", help="search rejected records and their reasons")
    rejects.add_argument("--reason", help="e.g. INVALID_DATE, DUPLICATE_ID")
    rejects.add_argument("--id", dest="record_id", help="the id of the record you are looking for")
    rejects.add_argument("--run-id", type=int)
    rejects.add_argument("--json", action="store_true")

    runs = subparsers.add_parser("runs", help="show the history of ingest runs")
    runs.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "query":
        return _cmd_query(args)
    if args.command == "rejects":
        return _cmd_rejects(args)
    return _cmd_runs(args)


def _cmd_ingest(args: argparse.Namespace) -> int:
    try:
        report = ingest_file(args.db, args.file)
    except FileNotFoundError:
        print(f"error: no such file: {args.file}", file=sys.stderr)
        return 2
    print(report.format())
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    try:
        date_from = _normalise_bound(args.date_from)
        date_to = _normalise_bound(args.date_to)
    except RejectionError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 2

    rows = query_records(args.db, args.source, args.status, date_from, date_to)
    _emit(rows, ["id", "source", "recorded_at", "value", "status"], args.json)
    return 0


def _cmd_rejects(args: argparse.Namespace) -> int:
    rows = query_rejects(args.db, args.reason, args.record_id, args.run_id)
    _emit(rows, ["run_id", "line_number", "record_id", "reason_code", "reason_detail"], args.json)
    return 0


def _cmd_runs(args: argparse.Namespace) -> int:
    rows = query_runs(args.db)
    _emit(rows, ["run_id", "source_file", "started_at", "total_lines", "accepted_count", "rejected_count"], args.json)
    return 0


def _normalise_bound(raw: str | None) -> str | None:
    """Query bounds accept the same date formats the ingest does."""
    return None if raw is None else format_utc(parse_datetime(raw))


def _emit(rows: list[dict[str, Any]], columns: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("(no matching rows)")
        return

    widths = {c: max(len(c), max(len(str(row.get(c, ""))) for row in rows)) for c in columns}
    print("  ".join(c.upper().ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))
    print(f"\n{len(rows)} row(s)")


if __name__ == "__main__":
    raise SystemExit(main())
