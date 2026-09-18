# Changelog

Versioning rule: a **minor** bump (`v0.1` → `v0.2`) is a feature release;
a **patch** bump (`v0.2` → `v0.2.1`) is a fix. The `APP_VERSION` constant in
`app.py` and the git tag must always match — they are bumped in the same commit.

## v0.6.1 — A busy provider no longer discards the run, and is reported at launch

### Fixed
- **A transient provider overload (HTTP 503 `UNAVAILABLE`, "high demand") no
  longer throws away a completed upload.** The processing path dispatched once
  and failed on the first non-200, so a condition the provider itself describes
  as temporary cost the user the whole upload and dispatch. Retryable statuses
  (429, 5xx, timeouts) are now retried with exponential backoff before giving up;
  a definite rejection (400/401/403) still fails immediately, because retrying it
  would only delay the message and hide the cause.
- **A model that is listed but currently overloaded is no longer reported as
  ready at launch.** The launch check previously stopped at the provider's model
  catalogue, which proves a model *exists* — not that the endpoint will *serve*
  it. A 503 was therefore invisible to it: the app announced the model ready and
  the user's first real request came back `UNAVAILABLE`. A catalogue hit is now
  confirmed with the same capped one-token probe already used when the catalogue
  cannot answer, so the cost is unchanged.

### Notes
- **An overloaded provider is reported as a warning, never as an error, and
  never points at Settings.** Nothing in the app's configuration causes it and
  nothing in Settings fixes it, so the console says the provider is busy and that
  retrying shortly normally works. Definite misconfigurations — the model is
  absent from the catalogue, or the key was rejected — still read as errors and
  still name the model, key and endpoint in ⚙️ Settings.
- Verification still costs at most one token per launch. The conclusive negative
  (the model is not in a catalogue that was read successfully) still spends
  nothing at all.

## v0.6 — The selected model is validated at launch

### Added
- **The app now verifies the selected model can be reached before you do any
  work.** Previously a wrong model name, a bad API key or a mistyped endpoint
  only surfaced after uploading a recording and waiting for a dispatch to fail.
  The check runs as the app starts and reports into the **Execution Console**.
  On failure it names the model that could not be used and points you at
  **⚙️ Settings**, where all three things it depends on — the model, the API key
  and the provider endpoint — can be changed.

### Notes
- **Verification costs nothing when your setup is correct.** The check reads the
  provider's own model catalogue first (a metadata call, no tokens). Only if that
  catalogue cannot be read does it escalate to a single completion capped at one
  output token. A working model therefore costs zero tokens to confirm, and a
  broken one costs one.
- **An uncertain answer is a warning, never an error.** If the endpoint is merely
  slow or unreachable — a proxy that hides `/models`, a network hiccup — the
  console says so without implying your configuration is wrong. Only a definite
  answer (the model is absent from the catalogue, or the provider rejected the
  key) is presented as something to fix.
- **The check never blocks a run.** It reports and gets out of the way.
- It re-validates exactly when the model, key or endpoint changes, so a fix is
  confirmed rather than left stale. The stored fingerprint is a SHA-256 digest of
  those settings — the API key itself is never copied into session state.
- Set `AIMA_SKIP_MODEL_CHECK=1` to disable the check (the test suite uses this so
  unit tests never touch the network).

## v0.5.2 — Transcript offsets past the hour are no longer misread

### Fixed
- **Timestamps no longer collapse to the first minute (or jump hours ahead) in
  meetings longer than an hour.** The offset-scale decision treated a 2-field
  offset whose leading field exceeded 59 as proof of `HH:MM`, on the reasoning
  that `MM:SS` "caps at 59 minutes". That reasoning is wrong: in a recording
  longer than an hour the MINUTE count is what grows past 59. So `75:10` — a
  normal 75 min 10 s offset — was read as **75 hours 10 minutes**, and a
  90-minute meeting's final entry landed at `04:00:00` instead of `11:30:00`.
  The scale is now decided purely by which reading is consistent with the
  recording's measured length.
- **A rounded final timestamp no longer discards the whole reading.** The model
  rounds to the minute while ffprobe measures the container exactly, so the last
  entry of a 60-minute meeting can read `01:00` against a measured 3599.4 s. The
  old check rejected `HH:MM` for overrunning by 0.6 s and fell back to `MM:SS`,
  placing all four entries inside the first minute (`10:01`). A small slack now
  absorbs rounding without admitting a genuinely over-long reading, which would
  be wrong by a factor of 60.
- When both readings are consistent with the duration, the one that spans more
  of the recording now wins — previously a `MM:SS` reading could win by being
  merely small, describing a transcript of the recording's opening seconds.

### Notes
- The `>59 ⇒ HH:MM` rule is retained nowhere; `_MMSS_MAX_MINUTES` and
  `_COVERAGE_THRESHOLD` are gone, replaced by a single duration-consistency
  check (`_ROUNDING_SLACK_SECONDS`).
- Two tests asserted the old, incorrect rule (`75:10` → `HH:MM`,
  `60:10` → `HH:MM`); they are replaced by tests pinning the corrected
  behaviour, including the rounded-final-stamp case that caused the regression.

## v0.5.1 — Upload limit raised to 400 MB

### Changed
- **`st.file_uploader` now accepts files up to 400 MB** (was Streamlit's 200 MB
  built-in default) via `.streamlit/config.toml` in the app root — the per-project
  config file that governs a deployed app.
- Worth noting: 200 MB was never a value in our code. It is Streamlit's default,
  so raising it means adding a config file, not editing a constant. A
  `config.toml` capped at 120 MB previously existed here but was deleted in the
  live-microphone revert (`e35d7d8`), leaving the app on the bare default.
- 400 is chosen against the ~1 GB container, not the API: audio is sent
  unaltered (v0.5), so one upload costs roughly 2.4x its size at peak — raw
  bytes resident while the ~1.37x base64 body is built. Batches remain gated in
  code by `media_pipeline.MAX_TOTAL_MB`.

## v0.5 — Audio is sent to the model unaltered

### Changed
- **The transcode stage is gone: uploaded audio now reaches the model
  byte-for-byte, exactly as the hackathon build sent it.** v0.2 forced every
  part through ffmpeg to mono / 16 kHz / 32 kbps before upload. That silently
  traded recognition quality for bytes — it collapsed the stereo image and cut
  the signal at 8 kHz, discarding the band where sibilants and consonant detail
  live, and the provider was going to downsample regardless. Nothing is
  resampled, downmixed or re-encoded now.
- **Batch ceiling retuned for raw audio.** Sending raw bytes means size is no
  longer absorbed by compression, so the whole-batch limit moves from 260 MB to
  2 GB to track the assumed API ceiling.
  ⚠️ The container is now the tighter constraint, not the API: raw bytes are
  base64'd into the request body (~1.37x), and this app runs on a ~1 GB
  Community Cloud instance. A batch well under 2 GB can still exhaust memory.

### Fixed
- **Safari `.m4a` uploads no longer arrive with an unsupported type label.**
  Safari reports `audio/x-m4a`, which the API does not accept. This was
  previously invisible because the transcoder relabelled every part as
  `audio/mp3`. With raw passthrough the declared type travels as-is, so MIME is
  now normalised (`audio/x-m4a` → `audio/mp4`, plus wav/mpeg/ogg variants),
  falling back to the file extension.
- **OpenAI-compatible endpoints no longer receive unknown decode hints.** The
  `input_audio.format` field was derived from the MIME subtype and could emit
  `mp4`/`x-m4a`. It is now mapped onto the accepted `mp3`/`wav` hints.

### Notes
- `media_pipeline.compress_audio` is retained as an unused, tested utility and
  as the contract of record for the removed behaviour. It is no longer called
  from the send path.
- The bit-exactness of the passthrough is pinned by test
  (`test_audio_bytes_reach_the_model_bit_for_bit`), so a future refactor cannot
  quietly reintroduce encoding here.

## v0.4.1 — Transcript clock times appear on a fresh run

### Fixed
- **The minutes no longer need a panel save to show clock times.** v0.4 anchored
  the transcript inside `apply_datetime_overrides`, which only runs when the user
  opens the Date & Start Time panel and saves. A fresh run therefore still
  displayed the model's raw elapsed offsets until that happened — the reported
  bug, one click away. The render path now anchors on its own via
  `auto_anchor_transcript`, using the report's own start time. The panel is a
  correction tool, not a prerequisite for readable minutes.
- Anchoring on every rerun stays idempotent, so Streamlit's constant
  re-execution cannot walk the times forward.

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
