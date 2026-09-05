"""Duplicate resolution and repeat runs.

Both are the same rule: for a given id, the highest recordedAt wins.
"""

from __future__ import annotations

from recordsys import validation as v
from recordsys.ingest import ingest_file
from recordsys.query import query_records, query_rejects


def stored(db_path, record_id):
    return next(r for r in query_records(db_path) if r["id"] == record_id)


def test_newest_timestamp_wins_within_a_file(db_path, write_file, record):
    path = write_file(
        [
            record(id="d-1", recordedAt="2026-03-14T09:00:00Z", value=10),
            record(id="d-1", recordedAt="2026-03-14T11:00:00Z", value=20),
        ]
    )
    report = ingest_file(db_path, path)

    assert report.accepted == 1
    assert stored(db_path, "d-1")["value"] == 20


def test_newest_wins_even_when_it_appears_first(db_path, write_file, record):
    """The rule is 'newest recordedAt', not 'last line in the file'."""
    path = write_file(
        [
            record(id="d-1", recordedAt="2026-03-14T11:00:00Z", value=20),
            record(id="d-1", recordedAt="2026-03-14T09:00:00Z", value=10),
        ]
    )
    ingest_file(db_path, path)

    assert stored(db_path, "d-1")["value"] == 20


def test_identical_timestamps_are_broken_by_file_order(db_path, write_file, record):
    path = write_file(
        [
            record(id="d-1", recordedAt="2026-03-14T09:00:00Z", value=10),
            record(id="d-1", recordedAt="2026-03-14T09:00:00Z", value=20),
        ]
    )
    ingest_file(db_path, path)

    assert stored(db_path, "d-1")["value"] == 20


def test_byte_identical_duplicates_store_one_and_log_the_other(db_path, write_file, record):
    path = write_file([record(id="d-1"), record(id="d-1")])
    report = ingest_file(db_path, path)

    assert report.accepted == 1
    assert report.reasons == {v.DUPLICATE_ID: 1}


def test_the_losing_duplicate_says_what_beat_it(db_path, write_file, record):
    path = write_file(
        [
            record(id="d-1", recordedAt="2026-03-14T09:00:00Z", value=10),
            record(id="d-1", recordedAt="2026-03-14T11:00:00Z", value=20),
        ]
    )
    ingest_file(db_path, path)

    reject = query_rejects(db_path, reason=v.DUPLICATE_ID)[0]
    assert reject["record_id"] == "d-1"
    assert reject["line_number"] == 1
    assert "superseded" in reject["reason_detail"]
    assert "line 2" in reject["reason_detail"]


def test_a_record_that_fails_validation_never_reaches_dedup(db_path, write_file, record):
    """A broken record does not displace a good one that shares its id."""
    path = write_file(
        [
            record(id="d-1", recordedAt="2026-03-14T09:00:00Z", value=10),
            record(id="d-1", recordedAt="2026-03-14T11:00:00Z", value=500),
        ]
    )
    ingest_file(db_path, path)

    assert stored(db_path, "d-1")["value"] == 10
    assert query_rejects(db_path)[0]["reason_code"] == v.INVALID_VALUE


def test_running_the_same_file_twice_stores_nothing_new(db_path, write_file, record):
    path = write_file([record(id=f"r-{n}") for n in range(5)])

    first = ingest_file(db_path, path)
    second = ingest_file(db_path, path)

    assert first.accepted == 5
    assert second.accepted == 0
    assert len(query_records(db_path)) == 5


def test_a_repeat_run_explains_itself_rather_than_dropping_silently(db_path, write_file, record):
    path = write_file([record(id="r-1")])
    ingest_file(db_path, path)
    second = ingest_file(db_path, path)

    assert second.reasons == {v.DUPLICATE_ID: 1}
    reject = query_rejects(db_path, run_id=2)[0]
    assert "already stored" in reject["reason_detail"]


def test_a_newer_version_in_a_later_file_updates_the_record(db_path, write_file, record):
    first_file = write_file([record(id="r-1", value=10, recordedAt="2026-03-14T09:00:00Z")], "day1.jsonl")
    second_file = write_file([record(id="r-1", value=20, recordedAt="2026-03-15T09:00:00Z")], "day2.jsonl")

    ingest_file(db_path, first_file)
    ingest_file(db_path, second_file)

    assert len(query_records(db_path)) == 1
    assert stored(db_path, "r-1")["value"] == 20


def test_an_older_version_in_a_later_file_is_refused(db_path, write_file, record):
    first_file = write_file([record(id="r-1", value=20, recordedAt="2026-03-15T09:00:00Z")], "day2.jsonl")
    second_file = write_file([record(id="r-1", value=10, recordedAt="2026-03-14T09:00:00Z")], "day1.jsonl")

    ingest_file(db_path, first_file)
    ingest_file(db_path, second_file)

    assert stored(db_path, "r-1")["value"] == 20


def test_an_update_keeps_the_original_first_seen_time(db_path, write_file, record):
    first_file = write_file([record(id="r-1", value=10, recordedAt="2026-03-14T09:00:00Z")], "day1.jsonl")
    second_file = write_file([record(id="r-1", value=20, recordedAt="2026-03-15T09:00:00Z")], "day2.jsonl")

    ingest_file(db_path, first_file)
    original = stored(db_path, "r-1")["first_seen_at"]
    ingest_file(db_path, second_file)

    assert stored(db_path, "r-1")["first_seen_at"] == original
