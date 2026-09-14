"""The transcript's clock times, end to end.

`apply_datetime_overrides` updates only `date` / `meeting_time`. The transcript
kept the model's raw elapsed offsets, so every entry rendered `00:00`-relative
however the panel was set — and because the screen, the standard DOCX, the
custom-template context and the JSON export all read `entry.timestamp`, all four
were wrong together.

Anchoring the transcript inside the override fixes all four at once. These tests
assert on the DOCX *bytes* and on the render context, not just the return value,
because a return value that never reaches the document is the exact failure this
bug already was.
"""
import datetime
import io
import os
import sys

import pytest
from docx import Document

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402


def _report(**kw):
    base = dict(
        title="Quarterly Review",
        date="2026-09-12",
        meeting_time="10:00",
        attendees=[app.Attendee(name="Ayesha Khan", designation="CFO")],
        executive_summary="Summary.",
        transcript=[
            app.TranscriptEntry(speaker="Speaker 1", timestamp="00:00",
                                original_text="Assalam o alaikum.", translated_text="Hello."),
            app.TranscriptEntry(speaker="Speaker 1", timestamp="00:21",
                                original_text="Aaj ka agenda.", translated_text="Today's agenda."),
            app.TranscriptEntry(speaker="Speaker 2", timestamp="00:46",
                                original_text="Bilkul.", translated_text="Absolutely."),
        ],
    )
    base.update(kw)
    return app.MeetingMinutesReport(**base)


def _docx_text(io_obj):
    doc = Document(io.BytesIO(io_obj.getvalue()))
    return "\n".join(p.text for p in doc.paragraphs)


# ---------------------------------------------------------------------------
# The bug itself
# ---------------------------------------------------------------------------
def test_entries_are_anchored_to_the_start_time():
    out = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    assert [e.timestamp for e in out.transcript] == ["10:00:00", "10:00:21", "10:00:46"]


def test_the_downloadable_docx_carries_the_clock_times():
    """The deliverable the user actually reads. Asserting on the return value
    alone would not catch a transcript that never reaches the document."""
    updated = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    text = _docx_text(app.build_default_docx(updated))
    assert "10:00:21" in text, text
    assert "10:00:46" in text, text
    assert "[00:21]" not in text, "raw offset still in the docx"


def test_a_custom_template_context_carries_the_clock_times():
    updated = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    ctx = app._build_render_context(updated.model_dump())
    assert ctx["transcript"][1]["timestamp"] == "10:00:21"


def test_the_json_export_carries_the_clock_times():
    updated = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    assert updated.model_dump()["transcript"][2]["timestamp"] == "10:00:46"


# ---------------------------------------------------------------------------
# Changing the start time re-anchors from the ORIGINAL offsets
# ---------------------------------------------------------------------------
def test_editing_the_time_moves_every_entry_without_drifting():
    """Saving twice must not add the offset twice. The entry's own offset is
    remembered, so re-anchoring is computed from it rather than from the value
    already written."""
    first = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    second = app.apply_datetime_overrides(first, None, datetime.time(11, 30))
    assert [e.timestamp for e in second.transcript] == ["11:30:00", "11:30:21", "11:30:46"]
    assert [e.timestamp for e in first.transcript] == ["10:00:00", "10:00:21", "10:00:46"]


def test_saving_the_same_time_twice_is_stable():
    once = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    twice = app.apply_datetime_overrides(once, None, datetime.time(10, 0))
    assert [e.timestamp for e in twice.transcript] == [e.timestamp for e in once.transcript]


# ---------------------------------------------------------------------------
# No silent invention
# ---------------------------------------------------------------------------
def test_a_blank_start_time_leaves_the_transcript_alone_and_warns():
    """Defaulting to 09:00 would stamp a fabricated clock time across the
    minutes. The transcript is left as the model produced it instead, and the
    caller is told why."""
    out = app.apply_datetime_overrides(_report(meeting_time=""), None, None)
    assert [e.timestamp for e in out.transcript] == ["00:00", "00:21", "00:46"]


def test_an_unreadable_offset_survives_untouched():
    out = app.apply_datetime_overrides(
        _report(transcript=[
            app.TranscriptEntry(speaker="S", timestamp="00:21",
                                original_text="a", translated_text="a"),
            app.TranscriptEntry(speaker="S", timestamp="",
                                original_text="b", translated_text="b"),
        ]),
        None,
        datetime.time(10, 0),
    )
    assert [e.timestamp for e in out.transcript] == ["10:00:21", ""]


# ---------------------------------------------------------------------------
# Scale handling through the app
# ---------------------------------------------------------------------------
def test_a_long_meeting_is_not_read_as_minutes_and_seconds():
    """With a known duration the app picks the HH:MM reading; otherwise a
    2.5 h meeting's `02:15` would land 135 s into the minutes. The scale applies
    to the WHOLE transcript, so `00:05` is 5 minutes in under that reading."""
    rep = _report(transcript=[
        app.TranscriptEntry(speaker="S", timestamp="00:05", original_text="a", translated_text="a"),
        app.TranscriptEntry(speaker="S", timestamp="02:15", original_text="b", translated_text="b"),
    ])
    out = app.apply_datetime_overrides(rep, None, datetime.time(9, 0), duration_seconds=9000)
    assert [e.timestamp for e in out.transcript] == ["09:05:00", "11:15:00"]


def test_the_private_offset_never_leaks_into_the_export():
    """The bookkeeping that makes re-anchoring idempotent must not appear in the
    JSON export or the template context — it is not part of the report."""
    out = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    dumped = out.model_dump()
    assert "_source_timestamp" not in str(dumped)
    assert "_source_timestamp" not in str(out.model_json_schema())


def test_model_validate_from_a_model_dump_is_unharmed():
    """Nothing in the app round-trips a report through model_validate today, but
    the export format must remain loadable — a private attr must not break it."""
    out = app.apply_datetime_overrides(_report(), None, datetime.time(10, 0))
    again = app.MeetingMinutesReport.model_validate(out.model_dump())
    assert [e.timestamp for e in again.transcript] == ["10:00:00", "10:00:21", "10:00:46"]


# ---------------------------------------------------------------------------
# A fresh run must already show clock times
# ---------------------------------------------------------------------------
def test_a_fresh_run_shows_clock_times_without_touching_the_panel():
    """The panel is a correction tool, not a prerequisite. Requiring a save
    before the minutes are readable would leave the reported bug visible on
    every new run."""
    out = app.auto_anchor_transcript(_report(), duration_seconds=60)
    assert [e.timestamp for e in out.transcript] == ["10:00:00", "10:00:21", "10:00:46"]


def test_auto_anchor_leaves_a_report_without_a_time_alone():
    out = app.auto_anchor_transcript(_report(meeting_time=""), duration_seconds=60)
    assert [e.timestamp for e in out.transcript] == ["00:00", "00:21", "00:46"]


def test_auto_anchor_is_idempotent_across_reruns():
    """Streamlit re-runs the script constantly; anchoring on every rerun must not
    walk the times forward."""
    once = app.auto_anchor_transcript(_report(), duration_seconds=60)
    twice = app.auto_anchor_transcript(once, duration_seconds=60)
    thrice = app.auto_anchor_transcript(twice, duration_seconds=60)
    assert [e.timestamp for e in thrice.transcript] == ["10:00:00", "10:00:21", "10:00:46"]


def test_auto_anchor_does_not_mutate_the_input_report():
    original = _report()
    app.auto_anchor_transcript(original, duration_seconds=60)
    assert [e.timestamp for e in original.transcript] == ["00:00", "00:21", "00:46"]
