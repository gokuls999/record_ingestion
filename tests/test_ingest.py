"""Whole-run behaviour: what gets stored, what gets logged, what the report says."""

from __future__ import annotations

from recordsys import validation as v
from recordsys.ingest import ingest_file
from recordsys.query import query_records, query_rejects, query_runs


def test_valid_records_are_stored(db_path, write_file, record):
    path = write_file([record(id="r-1"), record(id="r-2"), record(id="r-3")])
    report = ingest_file(db_path, path)

    assert report.accepted == 3
    assert report.rejected == 0
    assert {r["id"] for r in query_records(db_path)} == {"r-1", "r-2", "r-3"}


def test_accepted_plus_rejected_accounts_for_every_record(db_path, write_file, record):
    path = write_file(
        [
            record(id="r-1"),
            record(id="r-2", value=500),
            record(id="r-3", status="PENDING"),
            '{"broken":',
        ]
    )
    report = ingest_file(db_path, path)

    assert report.total_lines == 4
    assert report.accepted + report.rejected == report.total_lines


def test_blank_lines_are_not_counted_as_records(db_path, write_file, record):
    path = write_file([record(id="r-1"), "", "   ", record(id="r-2")])
    report = ingest_file(db_path, path)

    assert report.total_lines == 2
    assert report.accepted == 2


def test_every_rejection_is_recoverable_with_its_reason(db_path, write_file, record):
    path = write_file([record(id="bad-1", value=500)])
    ingest_file(db_path, path)

    rejects = query_rejects(db_path)
    assert len(rejects) == 1
    assert rejects[0]["record_id"] == "bad-1"
    assert rejects[0]["reason_code"] == v.INVALID_VALUE
    assert "500" in rejects[0]["reason_detail"]
    assert rejects[0]["line_number"] == 1


def test_rejected_record_keeps_its_original_line(db_path, write_file, record):
    """The raw text is kept so a rejected record can be fixed and resent."""
    path = write_file([record(id="bad-1", value=500)])
    ingest_file(db_path, path)

    assert '"value": 500' in query_rejects(db_path)[0]["raw_line"]


def test_unparseable_line_is_still_logged(db_path, write_file):
    path = write_file(['{"id": "broken",'])
    ingest_file(db_path, path)

    reject = query_rejects(db_path)[0]
    assert reject["reason_code"] == v.PARSE_ERROR
    assert reject["record_id"] is None
    assert reject["raw_line"] == '{"id": "broken",'


def test_report_groups_rejections_by_reason(db_path, write_file, record):
    path = write_file(
        [
            record(id="r-1"),
            record(id="r-2", value=500),
            record(id="r-3", value=-1),
            record(id="r-4", status="PENDING"),
        ]
    )
    report = ingest_file(db_path, path)

    assert report.accepted == 1
    assert report.reasons == {v.INVALID_VALUE: 2, v.INVALID_STATUS: 1}


def test_run_summary_is_persisted_for_later(db_path, write_file, record):
    path = write_file([record(id="r-1"), record(id="r-2", value=500)])
    ingest_file(db_path, path)

    run = query_runs(db_path)[0]
    assert run["accepted_count"] == 1
    assert run["rejected_count"] == 1
    assert run["finished_at"] is not None


def test_a_bad_record_does_not_stop_the_run(db_path, write_file, record):
    path = write_file([record(id="r-1"), "not json", record(id="r-2")])
    report = ingest_file(db_path, path)

    assert report.accepted == 2


def test_a_crash_mid_run_leaves_nothing_behind(db_path, write_file, record, monkeypatch):
    """A run is all-or-nothing, so a failed run cannot be mistaken for a short one."""
    import recordsys.ingest as ingest_module

    real = ingest_module.validate_record
    calls = {"n": 0}

    def exploding(obj):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk fell over")
        return real(obj)

    monkeypatch.setattr(ingest_module, "validate_record", exploding)
    path = write_file([record(id=f"r-{n}") for n in range(5)])

    try:
        ingest_file(db_path, path)
    except RuntimeError:
        pass

    assert query_records(db_path) == []
    assert query_runs(db_path) == []
