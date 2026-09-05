"""Query filters, including the boundaries of the date range."""

from __future__ import annotations

from recordsys.ingest import ingest_file
from recordsys.query import query_records, query_rejects


def ids(rows):
    return {row["id"] for row in rows}


def populated(db_path, write_file, record):
    path = write_file(
        [
            record(id="a-ok", source="alpha", status="OK", recordedAt="2026-03-01T00:00:00Z"),
            record(id="a-warn", source="alpha", status="WARN", recordedAt="2026-03-10T00:00:00Z"),
            record(id="b-ok", source="beta", status="OK", recordedAt="2026-03-20T00:00:00Z"),
            record(id="b-fail", source="beta", status="FAIL", recordedAt="2026-03-30T00:00:00Z"),
        ]
    )
    ingest_file(db_path, path)
    return db_path


def test_no_filters_returns_everything(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    assert len(query_records(db)) == 4


def test_filter_by_source(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    assert ids(query_records(db, source="alpha")) == {"a-ok", "a-warn"}


def test_filter_by_status(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    assert ids(query_records(db, status="OK")) == {"a-ok", "b-ok"}


def test_status_filter_is_case_insensitive(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    assert ids(query_records(db, status="ok")) == {"a-ok", "b-ok"}


def test_filters_combine(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    assert ids(query_records(db, source="beta", status="OK")) == {"b-ok"}


def test_date_range_bounds_are_inclusive(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    rows = query_records(
        db,
        date_from="2026-03-10T00:00:00.000000Z",
        date_to="2026-03-20T00:00:00.000000Z",
    )
    assert ids(rows) == {"a-warn", "b-ok"}


def test_open_ended_range(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    assert ids(query_records(db, date_from="2026-03-20T00:00:00.000000Z")) == {"b-ok", "b-fail"}


def test_unmatched_filter_returns_nothing(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    assert query_records(db, source="does-not-exist") == []


def test_results_are_ordered_by_time(db_path, write_file, record):
    db = populated(db_path, write_file, record)
    rows = query_records(db)
    assert [r["id"] for r in rows] == ["a-ok", "a-warn", "b-ok", "b-fail"]


def test_rejects_can_be_found_by_record_id(db_path, write_file, record):
    """The question this system exists to answer: why is record X not here?"""
    path = write_file([record(id="missing-one", value=500)])
    ingest_file(db_path, path)

    rejects = query_rejects(db_path, record_id="missing-one")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "INVALID_VALUE"
