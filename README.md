# Record Ingestion & Reporting

Reads a file of records, stores the ones that are usable, and keeps every one it
rejects together with the reason — so "why did that record never show up?" has
an answer.

Python 3.10+. No third-party packages are needed to run it; `pytest` is needed
only to run the tests.

## Running it

```bash
git clone <repo> && cd <repo>

python -m recordsys ingest sample_records.jsonl   # ingest the sample file
python -m recordsys query --source alpha          # query what was stored
python -m recordsys rejects --reason INVALID_DATE # see what was rejected, and why
```

The first command creates `records.db` in the working directory and prints the
run report:

```
Run #1 - sample_records.jsonl
  records read: 229
  accepted:     207
  rejected:     22
  rejected by reason:
    INVALID_VALUE   5
    DUPLICATE_ID    4
    INVALID_DATE    3
    MISSING_FIELD   3
    INVALID_SOURCE  2
    INVALID_STATUS  2
    PARSE_ERROR     2
    INVALID_ID      1
```

Run it a second time and it accepts nothing — every record ties the version
already stored, and each declined record is logged rather than dropped.

### Querying

```bash
python -m recordsys query --source alpha --status OK
python -m recordsys query --from 2026-03-01 --to 2026-03-15
python -m recordsys query --source beta --status FAIL --from "01/03/2026 00:00:00" --json
```

Filters combine, date bounds are inclusive, and `--from` / `--to` accept any
date format the ingest accepts. `--json` on any command emits JSON instead of a
table.

### Finding a record that never showed up

```bash
python -m recordsys rejects --id val-0001      # why was this one dropped?
python -m recordsys rejects --reason DUPLICATE_ID
python -m recordsys runs                       # history of every ingest
```

### Tests, and regenerating the sample

```bash
pip install pytest && python -m pytest      # 92 tests, ~40s
python scripts/generate_sample.py           # rewrites sample_records.jsonl
```

Use `--db <path>` on any command to work against a different database.

## Design

```
recordsys/
  validation.py   rules for a single record — one reason code per rejection
  ingest.py       the two-stage run: validate, then reconcile by id
  db.py           SQLite schema
  query.py        read-side functions, no CLI types in their signatures
  __main__.py     argument parsing and output formatting
```

**Ingest runs in two stages.** Each line is validated on its own first; anything
that fails a field rule is rejected there and never goes further. The survivors
are then reconciled by `id` — first against each other, because one file can
carry the same id more than once, then against what is already stored.

**Both reconciliations use one rule: the highest `recordedAt` wins.** Ties go to
the later line in the file. This is the question section 6 of the brief asks,
and the reasoning behind the answer — plus what it costs — is in
[ASSUMPTIONS.md](ASSUMPTIONS.md#12-which-version-of-a-duplicated-record-wins).
The short version: `recordedAt` is the only field that says anything about when
a record was true, while file order is an accident of how the sender batched it.

**Repeat runs are safe because of that same rule, not a separate mechanism.**
Re-running a file means every record ties what is already stored, and a tie
loses, so nothing is written. One rule to understand, one place it can be wrong.

**Storage is SQLite** — `records` for current state (one row per id), `rejects`
as an append-only log with the raw line and reason, `runs` for the history of
ingests. The query requirements are what SQL is good at, the `id` primary key
makes uniqueness the database's problem rather than mine, and it is in the
standard library so a clean checkout runs with nothing installed. A run is a
single transaction: a crash mid-file stores nothing at all, rather than leaving
a half-ingested state that no file describes.

**Rejections carry a code and a detail.** The code (`INVALID_DATE`,
`DUPLICATE_ID`, …) is what the report groups by; the detail is the sentence a
human needs — a losing duplicate names the line that beat it and both
timestamps. Each rejected record gets exactly one code, so accepted + rejected
always equals records read.

## The sample file

`sample_records.jsonl` is 229 records, generated deterministically by
`scripts/generate_sample.py`. The awkward ones have descriptive ids so each path
can be found by name — `python -m recordsys rejects --id val-0001`:

| id             | what it exercises                                                              |
| -------------- | ------------------------------------------------------------------------------ |
| `dup-0001`     | same id three times: two identical, one newer with a different value           |
| `dup-0002`     | same id, same timestamp, different values — a genuine tie                      |
| `dup-0003`     | the newer version appears _first_, proving the rule is not "last line wins"    |
| `miss-000*`    | a missing field, and a `null` one                                              |
| `date-000*`    | unparseable, blank, and impossible dates                                       |
| `val-000*`     | out of range, non-integer, string, and boolean values                          |
| `status-000*`  | an unlisted status, and an empty one                                           |
| `blank-000*`   | empty and whitespace-only text                                                 |
| `lenient-000*` | accepted only because of a deliberate leniency (see ASSUMPTIONS 2.4, 2.5, 2.7) |
| `broken-0001`  | a truncated line, and JSON that is not a record                                |

Records also arrive in four date formats — ISO with `Z`, ISO with a `+05:30`
offset, `YYYY-MM-DD HH:MM:SS` and `DD/MM/YYYY HH:MM:SS`.

## Testing

92 tests, weighted towards where the risk is rather than spread for coverage.
The bulk sit on deduplication and repeat runs, because a wrong winner is silent
in a way a rejected record is not. Dates are tested by asserting every accepted
format lands on the _same instant_. Two tests defend design claims rather than
behaviour: that a crash mid-run leaves nothing behind, and that normalised
timestamps sort chronologically as plain strings. Reasoning in
[ASSUMPTIONS.md](ASSUMPTIONS.md#6-testing).

## What I would do differently with more time

**Keep record history.** The store holds only the current version of a record,
so an update overwrites what was there before. `first_seen_at` vs `ingested_at`
shows _that_ something changed but not what it was. An append-only table with
validity ranges would answer "what did this record say last Tuesday?", which is
the first question I would expect after this ships.

**Collect all validation failures, not just the first.** A record with a bad
date and a bad status currently reports only the date, so fixing it upstream
takes two rounds. A small change to `validate_record` — accumulate rather than
raise — with one reason still nominated as primary so the R5 report keeps
adding up.

**Accept a JSON array as well as JSON Lines.** JSON Lines was chosen so one
corrupt record does not cost the whole file, but the brief never specified a
format. Sniffing the first character and handling both is a small change and
removes the biggest assumption in the project.

**Flag improbable timestamps.** Records far in the future are accepted, and
through the newest-wins rule a clock-skewed record can win and never be
displaced. I would not reject them — that would make results depend on when the
file was processed — but a warning in the run report would surface it.

**Structured logging and per-run metrics** rather than a printed summary, once
this runs unattended and nobody is reading the terminal.

**Postgres over SQLite** if this became a service with concurrent writers. The
SQL is close enough that the change is mostly the connection layer.
