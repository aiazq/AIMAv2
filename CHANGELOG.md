# Changelog

Versioning rule: a **minor** bump (`v0.1` → `v0.2`) is a feature release;
a **patch** bump (`v0.2` → `v0.2.1`) is a fix. The `APP_VERSION` constant in
`app.py` and the git tag must always match — they are bumped in the same commit.

## v0.4 — Transcript entries now show real clock times

### Fixed
- **Meeting minutes no longer read `00:00`.** The model returns **elapsed offsets**
  from the start of the recording (`00:21` = 21 s in), not times of day. The app
  rendered `entry.timestamp` verbatim, so every entry read `00:00`-relative no
  matter when the meeting actually began. Each entry is now anchored to the Start
  Time as a real clock time (`10:00:21`) — which answers "who said what, and at
  what time".
- **The correction reaches every consumer at once.** The screen, the standard
  DOCX, a custom template's `{{ transcript }}` context and the JSON export all
  read the same `entry.timestamp` field, so anchoring it inside
  `apply_datetime_overrides` fixes all four together — the downloadable document
  included. Previously the override touched only `date` / `meeting_time`.

### Added
- **Transcript offset scale detection.** A two-field offset (`02:15`) is
  ambiguous — 2 min 15 s or 2 h 15 min. The scale is now decided per transcript:
  a leading field above 59 forces HH:MM, and otherwise the recording's measured
  duration decides. A 2.5 h meeting's `02:15` lands at 11:15, not 135 s in.
  Multi-part uploads are summed, because their offsets span the whole meeting.
- **`timeline.py`** — the offset→clock arithmetic, Streamlit-free and unit tested
  (`tests/test_transcript_timeline.py`, `tests/test_audio_duration.py`).
- **Warnings instead of silent guesses.** When the transcript yields no readable
  start time the entries keep the model's raw offsets, and the panel explains
  why — defaulting to 09:00 would stamp a fabricated clock time across the whole
  minutes.

### Notes
- Changing the Start Time moves every entry without drifting: each entry's
  original offset is remembered, so re-saving recomputes from the original
  rather than adding the offset a second time.
- `tests/falsify_v031.sh` re-introduces 13 known bugs one at a time and confirms
  the suite catches every one. Each test in this release has been observed to
  fail for the right reason.

## v0.3 — Editable attendee roster, corrected date handling

### Added
- **Editable Attendee Roster.** The Attendees tab was a read-only grid; it is now
  an editor — rename, correct designations, add and delete rows, then save. Edits
  write through to the report data, so they reach the on-screen header, the
  standard DOCX, and a custom template's `{{ attendee.name }}` /
  `{{ attendee.designation }}` placeholders.
- **Meeting start time in the header.** The results header now reads
  `📅 Date: … | 🕐 Time: … | 👥 Attendees: …`. The time is omitted entirely when
  the transcript yielded none, rather than leaving a dangling `Time:` label.

### Fixed
- **Renaming a speaker no longer leaves the old label in the attendee list.**
  `apply_speaker_replacements` only *appended* the confirmed name, so a report
  whose roster read `Speaker 1, Speaker 2` ended up as
  `Speaker 1, Speaker 2, Ayesha Khan, …` — the exact labels the user had just
  replaced. The roster row is now renamed **in place**, which also carries the
  existing designation across instead of resetting it to `Participant`, and
  duplicates created by renaming to a name already on the roster are collapsed.
- **The date default now reaches the data and the download.** The Date & Start
  Time panel *showed* today for an undated report, but that was only the widget's
  default — `report.date` still said `Undated`, so the header and the downloaded
  `.docx` contradicted the panel. Today is now written into the report data. A
  date the transcript genuinely supplied (including a verbose `12 September
  2026`) is left byte-for-byte untouched.

## v0.2.1 — Editable meeting date & start time

### Added
- **Meeting Date & Start Time panel**, above the Speaker Identity Mapping &
  Verification panel. The model infers a date and time from the conversation,
  and it cannot always be right — a meeting held on the 14th but discussed as
  "last Tuesday" misleads it, and a recording with no spoken date yields
  "Undated". Both can now be corrected, and the change reaches the on-screen
  header, the standard DOCX, and a custom template's `{{ date }}` /
  `{{ meeting_time }}`.

### Fixed
- **An unreadable date no longer crashes the results page.** `date` is free text
  from the model and may be `Undated` (the schema default), `12 September 2026`,
  or `2026-09-12 (Saturday)`; `st.date_input` accepts none of those, and a naive
  `date.fromisoformat(...)` raises on the very common `Undated`. Dates and times
  are now parsed defensively across the formats the model actually emits, and a
  value that cannot be read falls back to today **with an explicit warning**
  rather than silently guessing.
- `meeting_time` is stored as a range (`10:00 - 11:30`); the start is now taken
  as the start time instead of the whole range failing to parse.

## v0.2 — Multi-file meetings

The release that makes AIMA usable on meetings recorded in parts.

### Added
- **Multi-file upload.** Upload up to 3 recordings of the same meeting; each is
  individually playable so you can confirm you have the right parts.
- **Call ordering.** Each part takes an `Order` number, seeded from a natural
  filename sort so `part_2` lands before `part_10`.
- **Single stitched output.** All parts are sent to the model in **one request,
  in order**, and come back as one transcript and one set of minutes — not
  three reports to merge by hand.
- **Audio compression.** Parts are transcoded to mono 16 kHz before sending.
  Three 90 MB recordings otherwise peak around 988 MB against Community Cloud's
  1024 MB ceiling — it does not slow down, it dies.
- **Pre-flight size gate.** An over-budget batch is refused up front with a clear
  message, instead of transferring, encoding, and failing minutes later with 413.
- **Version stamp** at the foot of the page.
- `packages.txt` declaring `ffmpeg` for Community Cloud.

### Fixed
- **A single incomplete transcript segment no longer discards the whole report.**
  A 43-segment response with `translated_text` missing on one segment was
  rejected outright by Pydantic —
  `1 validation error for MeetingMinutesReport: transcript.42.translated_text
  Field required` — throwing away a completed transcription and translation.
  Every schema field is now optional or coerced: a missing translation falls
  back to the original text so the segment still renders, an explicit `null`
  becomes `""`, and a bare string where a list is expected becomes a one-item
  list. A repair is logged as a `WARN` naming the affected segment indices, so a
  degraded response stays visible instead of silently papered over.
- The same fragility is fixed across `Attendee`, `ActionItem`, `AgendaItem`,
  `DetectedSpeaker` and `MeetingMinutesReport`. The missing `translated_text` was
  the first key to trip it, not the only one.

### Verified
- 99 tests pass. The audio and parsing paths are exercised against synthesised
  provider responses; the fix was falsified against the pre-fix schema, which
  reproduces the reported error verbatim.

## v0.1 — Baseline

The hackathon release: single-file upload, transcription, diarization,
translation, summarisation, and DOCX export from a custom Jinja-tagged template.
