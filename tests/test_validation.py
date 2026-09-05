"""Field-level rules, tested at the boundaries and on the shapes that bite."""

from __future__ import annotations

import pytest

from recordsys import validation as v


def test_valid_record_is_normalised(record):
    result = v.validate_record(record())
    assert result.id == "r-0001"
    assert result.value == 42
    assert result.status == "OK"
    assert result.recorded_at == "2026-03-14T09:12:00.000000Z"


@pytest.mark.parametrize("field", ["id", "source", "recordedAt", "value", "status"])
def test_absent_field_is_rejected(record, field):
    payload = record()
    del payload[field]
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(payload)
    assert exc.value.code == v.MISSING_FIELD
    assert field in exc.value.detail


@pytest.mark.parametrize("field", ["id", "source", "recordedAt", "value", "status"])
def test_null_field_is_treated_as_absent(record, field):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(**{field: None}))
    assert exc.value.code == v.MISSING_FIELD


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_id_is_rejected(record, blank):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(id=blank))
    assert exc.value.code == v.INVALID_ID


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_source_is_rejected(record, blank):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(source=blank))
    assert exc.value.code == v.INVALID_SOURCE


@pytest.mark.parametrize("value", [0, 100, 50])
def test_values_inside_the_range_are_kept(record, value):
    assert v.validate_record(record(value=value)).value == value


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_values_outside_the_range_are_rejected(record, value):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(value=value))
    assert exc.value.code == v.INVALID_VALUE


def test_whole_float_is_accepted_as_integer(record):
    assert v.validate_record(record(value=42.0)).value == 42


@pytest.mark.parametrize("value", [42.5, "42", True, False, [42], None])
def test_non_integer_values_are_rejected(record, value):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(value=value))
    # None is caught by the missing-field check, everything else by the value rule.
    assert exc.value.code in (v.INVALID_VALUE, v.MISSING_FIELD)


def test_boolean_is_not_smuggled_in_as_an_integer(record):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(value=True))
    assert exc.value.code == v.INVALID_VALUE


@pytest.mark.parametrize("status", ["OK", "WARN", "FAIL"])
def test_listed_statuses_are_kept(record, status):
    assert v.validate_record(record(status=status)).status == status


@pytest.mark.parametrize("status", ["ok", " warn ", "Fail"])
def test_status_case_and_padding_are_normalised(record, status):
    assert v.validate_record(record(status=status)).status == status.strip().upper()


@pytest.mark.parametrize("status", ["PENDING", "", "   ", "OKAY", "0"])
def test_unlisted_status_is_rejected(record, status):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(status=status))
    assert exc.value.code == v.INVALID_STATUS


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-03-14T09:12:00Z", "2026-03-14T09:12:00.000000Z"),
        ("2026-03-14T09:12:00.500Z", "2026-03-14T09:12:00.500000Z"),
        ("2026-03-14T14:42:00+05:30", "2026-03-14T09:12:00.000000Z"),
        ("2026-03-14 09:12:00", "2026-03-14T09:12:00.000000Z"),
        ("14/03/2026 09:12:00", "2026-03-14T09:12:00.000000Z"),
        ("2026-03-14", "2026-03-14T00:00:00.000000Z"),
    ],
)
def test_accepted_date_formats_land_on_the_same_instant(record, raw, expected):
    assert v.validate_record(record(recordedAt=raw)).recorded_at == expected


@pytest.mark.parametrize("raw", ["14th of March, 2026", "   ", "2026-13-45T99:99:99Z", "yesterday", 1773478320])
def test_unusable_dates_are_rejected(record, raw):
    with pytest.raises(v.RejectionError) as exc:
        v.validate_record(record(recordedAt=raw))
    assert exc.value.code == v.INVALID_DATE


def test_naive_dates_are_read_as_utc_not_local_time(record):
    # Otherwise the same file would ingest differently on different machines.
    assert v.validate_record(record(recordedAt="2026-03-14 09:12:00")).recorded_at.endswith("09:12:00.000000Z")


def test_normalised_dates_sort_chronologically_as_strings(record):
    earlier = v.validate_record(record(recordedAt="2026-03-14T09:12:00Z")).recorded_at
    later = v.validate_record(record(recordedAt="2026-03-14T09:12:00.500Z")).recorded_at
    assert earlier < later


def test_unknown_fields_are_ignored(record):
    assert v.validate_record(record(region="EMEA")).value == 42


@pytest.mark.parametrize("line", ['{"id": "broken",', "not json at all", ""])
def test_unparseable_lines_are_rejected(line):
    with pytest.raises(v.RejectionError) as exc:
        v.parse_line(line)
    assert exc.value.code == v.PARSE_ERROR


@pytest.mark.parametrize("line", ['["not", "an", "object"]', "42", '"a string"'])
def test_json_that_is_not_a_record_is_rejected(line):
    with pytest.raises(v.RejectionError) as exc:
        v.parse_line(line)
    assert exc.value.code == v.PARSE_ERROR
