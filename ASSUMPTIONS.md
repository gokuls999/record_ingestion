# Assumptions

Every decision below is one the specification did not make for me. Each entry
states what was open, what I chose, and what that choice costs — because most of
them have a defensible alternative that I rejected rather than missed.

---

## 1. Identity and duplicates

### 1.1 What makes two records "the same record"

**Open:** R3 says a repeat run must not duplicate anything, and asks me to
decide what "the same record" means.

**Decided:** `id` alone. Two records with the same `id` are the same record,
whatever else differs.

**Why:** The brief states plainly that `id` is unique. Treating it as the
natural key takes the spec at its word.

**Cost:** If two upstream systems ever share an id space, this silently merges
two unrelated records. A composite key of `(source, id)` would be safer against
that, but it would contradict the stated rule and would mean a record that moved
between sources becomes two records. If I learned that ids are only unique per
source, this is a one-line change to the key and one migration.

### 1.2 Which version of a duplicated record wins

**Open:** This is the worked example in section 6 — same id, two different
values. First? Last? Newest? Neither?

**Decided:** **The highest `recordedAt` wins.** If two records tie on
`recordedAt`, the one later in the file wins.

**Why:** `recordedAt` is the only field that carries any statement about when
the record was true. File order says only what the sender happened to write
first, which is an accident of their implementation, not information about the
data. Choosing "last line wins" would give a different answer purely because a
sender changed how they batch — and if the same file were ever re-sent in a
different order, the stored answer would change. Newest-timestamp-wins is stable
under reordering, which means the same set of records always converges on the
same result no matter how they arrive or how many files they are split across.

**Cost:** It trusts `recordedAt`, which is upstream data I have already seen be
wrong elsewhere in this file. A record with a clock-skewed future timestamp will
win and then never be displaced by anything real. "Last one seen wins" would be
immune to that but would instead be at the mercy of ordering. I chose the
failure mode that is visible — a wrong timestamp can be found and queried — over
the one that is invisible.

The tie-break on file order matters more than it looks: two records with the
same id and same timestamp but different values are a genuine contradiction in
the source data, and there is no principled winner. I take the later line so the
outcome is at least deterministic, and log the loser so the contradiction is
visible rather than silently resolved. See `dup-0002` in the sample file.

### 1.3 Rejection or update?

**Open:** Section 6 asks explicitly whether a duplicate is a rejection or an
update.

**Decided:** **Both, depending on where the loser came from.**

- Losing duplicates *within a single file* are **rejections**, logged with
  reason `DUPLICATE_ID` and a detail naming the line that beat them.
- A newer version arriving in a *later run* is an **update**: the stored row is
  overwritten in place, and the run reports it as accepted.

**Why:** These look like the same event but answer to different readers. Inside
one file, two versions of a record are a data quality problem — somebody should
be able to see that the sender contradicted itself, so both versions need to be
inspectable. Across runs, a newer version is the system working as intended:
yesterday's file said 40, today's says 77, and the current value is 77. Treating
that as a rejection would mean the store could never absorb a correction.

**Cost:** The store keeps only the current version. There is no history of what
a record used to say, so "what did r-0042 read last Tuesday?" is unanswerable.
The `rejects` table gives a partial audit trail, and `first_seen_at` vs
`ingested_at` reveals *that* a record was updated, but not its previous value.
Full history would mean an append-only design with validity ranges; that is a
materially bigger system and the brief asks for a modest one. I note it in the
README as the first thing I would add.

### 1.4 How a repeat run is made harmless

**Open:** R3 requires a repeat run not to duplicate anything. The mechanism was
left to me.

**Decided:** No separate mechanism. The rule in 1.2 is applied against stored
data as well as within the file. An incoming record must be **strictly newer**
than the stored one to replace it; equal timestamps lose.

**Why:** Re-running the same file means every record ties the version already
stored, so nothing is written. Idempotency falls out of the deduplication rule
rather than being bolted on, which means there is one rule to understand and one
place it can be wrong. The alternatives — hashing the file and refusing to
process it twice, or a "seen ids" table — add a second concept and would also
refuse a file that had been legitimately corrected and re-sent.

**Cost:** A repeat run writes a `DUPLICATE_ID` reject row for every record it
declines, so the rejects table grows on every re-run. That is deliberate: R2
says nothing disappears silently, and a re-run declining 229 records is a fact
worth being able to see. But it does mean rejects need a retention policy in
production — see 5.3.

A real consequence worth stating: a genuine correction that keeps the same
`recordedAt` but changes `value` will be **refused**, because it is not strictly
newer. Corrections must carry a new timestamp. Accepting equal-timestamp
replacements would make re-running a file rewrite every row and break
idempotency, so this is the direct price of 1.4 and I would rather state it than
have it surprise someone.

### 1.5 Invalid records never take part in deduplication

**Open:** What happens when a record that fails validation shares an id with a
valid one?

**Decided:** Field validation runs first, and a record that fails it is rejected
on that basis and never enters the duplicate comparison.

**Why:** A record with a value of 500 is not a candidate to be anyone's newest
version. If it competed, a broken record with a late timestamp could displace a
good one and leave the store worse than if the bad record had never been sent.

**Cost:** A record can be rejected as `INVALID_VALUE` while an older version of
the same id sits happily in the store, which means the stored record is stale
and nothing about the stored record says so. The rejects table is where you find
out. Tested in `test_a_record_that_fails_validation_never_reaches_dedup`.

---

## 2. What counts as a valid record

### 2.1 One reason per rejection

**Open:** R5 wants rejections grouped by reason. A record can break several
rules at once.

**Decided:** Each rejected record gets exactly one reason — the first rule it
breaks, in this fixed order:

```
PARSE_ERROR → MISSING_FIELD → INVALID_ID → INVALID_SOURCE
            → INVALID_DATE → INVALID_VALUE → INVALID_STATUS → DUPLICATE_ID
```

**Why:** It makes the report a true partition: accepted + rejected equals the
number of records read, and the reason counts sum to the rejected count. If a
record could carry three reasons, "grouped by reason" would need to explain
whether it is counting records or reasons, and the totals would stop adding up.
The order runs from structural problems to field problems, and duplicates last
because that check needs an otherwise-valid record to compare.

**Cost:** A record with a bad date *and* a bad status reports only the date. Fix
it, resend, and it fails again on the status — annoying for whoever is cleaning
data upstream. Collecting all reasons and reporting a primary one would be
better; it is a small change to `validate_record` (accumulate rather than raise)
and I would do it if the rejects table were being handed to another team.

### 2.2 Absent and null are the same thing

**Decided:** A field that is missing and a field explicitly set to `null` are
both `MISSING_FIELD`.

**Why:** They mean the same thing to a consumer — there is no value here — and
distinguishing them would tell the reader something about the sender's JSON
serialiser rather than about the data.

### 2.3 Empty and whitespace-only text is invalid, not missing

**Decided:** `""` and `"   "` for `id` or `source` are rejected as
`INVALID_ID` / `INVALID_SOURCE`, kept distinct from `MISSING_FIELD`.

**Why:** The brief lists them as a separate category of dirt, and they are a
different upstream bug: a missing field usually means a mapping was never
written, an empty string usually means one ran and produced nothing. The person
fixing it needs to know which.

### 2.4 `value` must be a whole number in `[0, 100]`

**Decided:** Bounds inclusive. `42.0` is accepted as `42`. `42.5` is rejected.
The string `"42"` is rejected. `true` is rejected.

**Why:** "Between 0 and 100" is ambiguous at the ends; inclusive is the ordinary
reading and the sample file exercises both 0 and 100. Whole floats are accepted
because plenty of senders serialise integers through a float type and mean no
harm by it — the value is not in doubt. Numeric *strings* are refused because
the sender has told me the wrong type, and once I start coercing `"42"` I have
to decide about `" 42 "`, `"4.2e1"` and `"forty-two"`; the line is cleaner
before that. `true` is refused explicitly because Python's `bool` is a subclass
of `int` and would otherwise slip through as `1` — a real trap, tested for.

**Cost:** The string/float asymmetry is a judgement call, not a principle. A
sender emitting `"42"` for everything gets every record rejected, and the fix is
a one-line coercion. I would rather that show up loudly in the rejects report
than be quietly absorbed.

### 2.5 `status` is case-insensitive and trimmed; the list is closed

**Decided:** `" ok "` is accepted as `OK`. `PENDING` is rejected.

**Why:** Case and padding are formatting, and the brief already tells me to
expect formatting variation in dates, so it would be odd to be strict here. But
an unlisted status is a *value* I have no meaning for — storing it would let a
status the consumer has never heard of leak through into their filters.

**Cost:** If `pending` is a real state the sender uses, every one of those
records is rejected. That is the intended behaviour: it surfaces in the report
as a large `INVALID_STATUS` count on day one, which is exactly the conversation
worth having.

### 2.6 `source` has no length limit

**Decided:** `source` must be a non-empty string after trimming, and is stored
trimmed. "Short" is not enforced.

**Why:** The brief calls it "a short string" but never says how short. Inventing
a limit would reject valid data on a number I made up. Non-empty is the only
part I can actually justify.

### 2.7 Unknown fields are ignored

**Decided:** Extra fields are dropped, not rejected. See `lenient-0004` in the
sample file.

**Why:** A sender adding a field should not break a consumer. Rejecting on
unknown fields would make every upstream addition an outage.

**Cost:** A typo'd field name — `statuss` — is silently discarded, and shows up
only as a `MISSING_FIELD` on the field it was meant to be. That is a reasonable
trade, but if the senders were internal I would rather log unknown field names
once per run than drop them without a word.

### 2.8 Dates: formats accepted, and the one that is genuinely ambiguous

**Decided:** ISO 8601 with `Z` or an offset, `YYYY-MM-DD HH:MM:SS`,
`YYYY/MM/DD HH:MM:SS`, `DD/MM/YYYY HH:MM:SS`, `DD-Mon-YYYY HH:MM:SS`, and a bare
`YYYY-MM-DD` (read as midnight). Everything else is rejected rather than guessed
at.

Two sub-decisions inside that:

**A date with no timezone is read as UTC**, not as local time. Otherwise the
same file ingested on a laptop in Kochi and a server in Dubai would produce
different stored instants and, through 1.2, potentially different winners.
Reading naive timestamps as UTC is arguably wrong about the sender's intent, but
it is at least *consistently* wrong and does not depend on where the code runs.

**`05/03/2026` is read as 5 March, not 3 May.** This is the assumption in this
document I am least comfortable with. A day-first and a month-first reading are
both valid, both produce a real date, and nothing in the record says which the
sender meant — so there is no way to detect the mistake. Between 1 and 12 of
every month, silently wrong dates enter the store and can then win or lose a
deduplication contest on 1.2. I chose day-first because it is the convention in
the regions this data would plausibly come from, but this is exactly the
question I would email about rather than guess at on a real system. The safe
alternative is to reject ambiguous `NN/NN/YYYY` outright and make the sender
disambiguate.

**A bare date becomes midnight UTC**, which means a record dated `2026-03-14`
loses a tie-break against any timestamped record on the same day. Acceptable,
and noted because it interacts with 1.2.

**No upper bound on dates.** A record timestamped in 2099 is accepted. I
considered rejecting future dates, but "the future" depends on when the file is
processed, so a re-run of an old file could produce different results than the
original — which would break the property 1.4 depends on. Clock skew is real,
though, so a `recordedAt` far ahead of ingest time is worth *reporting* even if
it is not worth rejecting.

### 2.9 Times are stored as fixed-width UTC strings

**Decided:** Everything normalises to `YYYY-MM-DDTHH:MM:SS.ffffffZ`.

**Why:** Fixed width is the point. It makes string comparison identical to
chronological comparison, so `recorded_at > stored_recorded_at` in Python and
`recorded_at >= ?` in SQL are both correct without a date type, which SQLite
does not have. Variable-width would break this: `...:00Z` and `...:00.5Z` do not
string-compare in the order you want.

**Cost:** Microsecond precision on data that mostly has none, and the original
format and offset are lost. The raw line is preserved in `rejects` but not for
accepted records, so if it later mattered that a sender used `+05:30`, that is
gone. Storing the original alongside the normalised value would fix it cheaply.

### 2.10 Blank lines are not records

**Decided:** Empty and whitespace-only lines are skipped and not counted in the
totals. A line with any content that is not valid JSON is a `PARSE_ERROR`.

**Why:** A trailing newline is a property of files, not a record the sender
meant to send, and counting it would make the totals wrong by one in a way that
looks like a bug.

---

## 3. The shape of the input

### 3.1 JSON Lines, not a JSON array

**Open:** The brief shows one record and calls the input "a file".

**Decided:** JSON Lines — one record per line, `sample_records.jsonl`.

**Why:** Fault isolation. In a single JSON array, one malformed record makes the
*entire file* unparseable, and R2 becomes impossible to satisfy for any of it —
you cannot report per-record reasons for a file you could not parse. With JSON
Lines a corrupt line is one `PARSE_ERROR` and the other 228 records ingest
normally. Given the brief's whole premise is that the file is not clean, a
format where one bad byte costs everything is the wrong choice. It also means
line numbers are stable references, so a reject can point at "line 47" and
someone can go look at line 47. And it streams, so file size is bounded by disk
rather than memory.

**Cost:** It is an assumption about a format the brief did not specify, and if
the real sender emits a JSON array this reads none of it. Supporting both is
maybe fifteen lines — sniff the first non-whitespace character, and if it is `[`
parse the whole array and index by position instead of line. I left it out
because guessing at a second format the sender may not use is speculative, but
this is the single most likely thing to be wrong about the input.

### 3.2 The file is read as UTF-8

**Decided:** UTF-8, and a file that is not valid UTF-8 fails the run outright
rather than per-record.

**Cost:** A single bad byte anywhere costs the whole run, which sits awkwardly
next to 3.1's fault-isolation argument. Reading with `errors="replace"` would
degrade per-record instead. I would change this the moment a real sender
produced anything other than UTF-8.

---

## 4. Storage

### 4.1 SQLite

**Open:** Any storage, but be ready to explain it.

**Decided:** SQLite, one file, created on first run.

**Why:** The query requirements (R4: filter by source, by status, by date range,
combined) are exactly what a relational engine is for — with a flat file I would
be hand-writing filtering and sorting that `WHERE` and `ORDER BY` already do
correctly. It gives a real `PRIMARY KEY` on `id`, which makes 1.1 enforced by
the database rather than by my care, and real transactions, which make 4.3
possible. And it is in the standard library, so a clean checkout needs no server
and no `pip install` to run the program at all.

**Cost:** One writer at a time, and no network access — a second ingest running
concurrently would block. For a batch file drop that is not a constraint that
binds. If this became a service with concurrent writers or a dataset too large
for one disk, Postgres is the move, and the SQL involved is close enough that
the change is mostly the connection layer.

### 4.2 Three tables

`records` holds the current accepted state, one row per id. `rejects` is an
append-only log — a row per rejection per run, with the raw line, the reason
code, the detail, the line number and the run. `runs` holds one row per ingest
with the counts and the reason breakdown as JSON.

`runs` is not required by the brief. I added it because the scenario's stated
pain is somebody asking why a record never showed up, and answering that needs
to survive the terminal scrollback of the run that dropped it. It also makes the
R5 report reproducible after the fact via `python -m recordsys runs`.

The reason breakdown is stored as a JSON blob rather than a fourth table because
nothing queries across it; if that changed it should become rows.

### 4.3 A run is all or nothing

**Decided:** One transaction per run, committed at the end. A crash mid-file
stores nothing at all — not even the `runs` row.

**Why:** A half-ingested file is the worst outcome, because the store then holds
a state that no file describes and nothing records that it happened. All-or-
nothing means the failure is obvious and the fix is to run it again.

**Cost:** No incremental progress on a large file, and peak memory holds one
candidate per unique id. At 229 records this is irrelevant; at 50 million it
would need batched commits and a resumable design.

### 4.4 The database defaults to `records.db` in the working directory

Overridable with `--db`. Gitignored, so a clean checkout starts empty and the
committed sample is the only input. Tests use a temp path each.

---

## 5. Interface and reporting

### 5.1 A CLI rather than an HTTP API

**Open:** R4 explicitly offers the choice.

**Decided:** CLI — `ingest`, `query`, `rejects`, `runs`.

**Why:** The consumer described in the scenario is another team reading a file
drop, not a browser. A CLI needs no server lifecycle, no port, no framework
dependency, and no separate "is it running?" step in the instructions — which is
what keeps the setup inside half a page. The query logic in `query.py` is plain
functions taking arguments and returning dicts, with no CLI types in the
signatures, so putting HTTP in front of it later is a thin layer rather than a
rewrite.

**Cost:** Nothing remote can query it, and there is no auth story. Both would
matter the moment a second team needed the data, and neither is in the brief.

### 5.2 Rejects are queryable by id, not just by reason

R2 only asks that rejections be recoverable with a reason. I added
`--id` because the scenario's actual question is about a *specific* record —
"why did r-0042 never show up?" — and answering that by reading a reason-grouped
list is the wrong shape:

```
python -m recordsys rejects --id val-0001
```

Records rejected before their id could be trusted (`PARSE_ERROR`, blank `id`)
store `NULL` and are only findable by reason or by run. That is unavoidable: if
the id is unusable there is nothing to search on. The raw line is kept so they
can still be identified by eye.

### 5.3 Rejects are kept forever

There is no retention or cleanup. Every run appends, and repeat runs append a
lot (1.4). For an exercise this is right — nothing disappears. In production
this table grows without bound and needs a retention window, which is a policy
question for whoever owns the data rather than one I should invent.

---

## 6. Testing

**Open:** The brief deliberately does not say what to test.

**Decided:** 92 tests, weighted by where I thought the risk was rather than
spread evenly for coverage.

The heaviest coverage is on deduplication and repeat runs (`test_dedup.py`),
because that logic is the least obvious, has the most branches, and is where a
silent wrong answer would be most damaging — a wrong winner is invisible in a
way a rejected record is not. Date handling is tested by asserting that all
accepted formats land on the *same instant*, which is the property that actually
matters, rather than that each parses. Boundaries are tested on both sides
(`0`, `100`, `-1`, `101`). The type traps are tested explicitly because they are
the ones I would expect to regress: `true` as a value, `42.0` versus `42.5`,
`null` versus absent.

Two tests exist to defend claims made in this document rather than to check
behaviour: that a crash mid-run leaves nothing behind (4.3), and that normalised
timestamps sort chronologically as strings (2.9). Both are properties the design
depends on and neither is visible from reading a single function.

Not tested: the CLI argument parsing and table formatting, which are thin
wrappers over tested functions, and SQLite itself.
