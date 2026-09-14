"""User-editable Meeting Date and Start Time.

The model derives a date and time from the conversation. That guess cannot
always be right — a meeting held on the 14th but discussed as "last Tuesday"
misleads it, and a recording with no spoken date yields "Undated". This panel
lets the user correct both, ABOVE the Speaker Identity Mapping panel.

The awkward part is the round trip. `result.date` is a free-text model output
("2026-09-12", "Undated", "12 September 2026"), but `st.date_input` demands a
real `datetime.date`. A naive `datetime.date.fromisoformat(result.date)` raises
on "Undated" — which is exactly the common case — so parsing must degrade to a
sensible default rather than crash the render.
"""
import datetime
import io
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402


# ---------------------------------------------------------------------------
# parse_report_date
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("2026-09-12", datetime.date(2026, 9, 12)),
    ("2026-09-12 ", datetime.date(2026, 9, 12)),
    ("12 September 2026", datetime.date(2026, 9, 12)),
    ("September 12, 2026", datetime.date(2026, 9, 12)),
    ("2026/09/12", datetime.date(2026, 9, 12)),
    ("12-09-2026", datetime.date(2026, 9, 12)),
    ("2026-09-12 (Saturday)", datetime.date(2026, 9, 12)),
])
def test_parse_report_date_reads_the_formats_a_model_actually_emits(raw, expected):
    assert app.parse_report_date(raw, fallback=datetime.date(2000, 1, 1)) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "Undated", "undated", "TBD", "N/A", "unknown", "---"])
def test_parse_report_date_degrades_to_the_fallback(raw):
    """'Undated' is the schema DEFAULT, so it must never raise."""
    fb = datetime.date(2000, 1, 1)
    assert app.parse_report_date(raw, fallback=fb) == fb


def test_parse_report_date_never_raises_on_garbage():
    fb = datetime.date(2000, 1, 1)
    for junk in ["%$#@!", "9999-99-99", "30 February 2026", "x" * 500, "2026-13-45"]:
        assert app.parse_report_date(junk, fallback=fb) == fb


# ---------------------------------------------------------------------------
# parse_report_time
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("10:00", datetime.time(10, 0)),
    ("10:00:00", datetime.time(10, 0)),
    ("14:30", datetime.time(14, 30)),
    ("10:00 AM", datetime.time(10, 0)),
    ("2:30 PM", datetime.time(14, 30)),
    ("2:30pm", datetime.time(14, 30)),
    ("12:00 AM", datetime.time(0, 0)),
    ("12:30 PM", datetime.time(12, 30)),
])
def test_parse_report_time_reads_the_formats_a_model_actually_emits(raw, expected):
    assert app.parse_report_time(raw, fallback=datetime.time(9, 0)) == expected


def test_parse_report_time_takes_the_start_of_a_range():
    """The schema says 'Meeting time range if mentioned' — the START is the start time."""
    assert app.parse_report_time("10:00 - 11:30", fallback=datetime.time(9, 0)) == datetime.time(10, 0)
    assert app.parse_report_time("10:00 to 11:30", fallback=datetime.time(9, 0)) == datetime.time(10, 0)


@pytest.mark.parametrize("raw", ["", "   ", None, "TBD", "N/A", "morning", "---"])
def test_parse_report_time_degrades_to_the_fallback(raw):
    fb = datetime.time(9, 0)
    assert app.parse_report_time(raw, fallback=fb) == fb


# ---------------------------------------------------------------------------
# apply_datetime_overrides
# ---------------------------------------------------------------------------
def _report(**kw):
    base = dict(
        title="Quarterly Review",
        date="2026-09-12",
        meeting_time="10:00",
        attendees=[app.Attendee(name="Ayesha Khan", designation="CFO")],
        detected_speakers=[app.DetectedSpeaker(speaker_id="Speaker 1", inferred_name="Ayesha Khan")],
        executive_summary="Summary text.",
        agenda_and_decisions=[app.AgendaItem(topic="Budget", discussion_summary="Discussed.")],
        action_items=[app.ActionItem(owner="Ayesha Khan", task="Send figures")],
        transcript=[
            app.TranscriptEntry(speaker="Speaker 1", timestamp="00:01",
                                original_text="Assalam o alaikum.", translated_text="Hello."),
        ],
    )
    base.update(kw)
    return app.MeetingMinutesReport(**base)


def test_apply_datetime_overrides_sets_both_fields():
    out = app.apply_datetime_overrides(_report(), datetime.date(2026, 10, 1), datetime.time(15, 45))
    assert out.date == "2026-10-01"
    assert out.meeting_time == "15:45"


def test_apply_datetime_overrides_does_not_mutate_the_original():
    original = _report()
    app.apply_datetime_overrides(original, datetime.date(2026, 10, 1), datetime.time(15, 45))
    assert original.date == "2026-09-12"
    assert original.meeting_time == "10:00"


def test_apply_datetime_overrides_preserves_everything_else():
    before = _report()
    after = app.apply_datetime_overrides(before, datetime.date(2026, 10, 1), datetime.time(15, 45))
    assert after.title == before.title
    assert after.executive_summary == before.executive_summary
    assert [a.name for a in after.attendees] == [a.name for a in before.attendees]
    assert [t.translated_text for t in after.transcript] == [t.translated_text for t in before.transcript]
    assert after.next_meeting_date == before.next_meeting_date


def test_apply_datetime_overrides_accepts_either_field_being_unchanged():
    r = _report()
    assert app.apply_datetime_overrides(r, None, datetime.time(8, 5)).meeting_time == "08:05"
    assert app.apply_datetime_overrides(r, None, datetime.time(8, 5)).date == "2026-09-12"
    assert app.apply_datetime_overrides(r, datetime.date(2026, 1, 2), None).date == "2026-01-02"
    assert app.apply_datetime_overrides(r, datetime.date(2026, 1, 2), None).meeting_time == "10:00"


# ---------------------------------------------------------------------------
# The override must reach the exported document — otherwise the panel is cosmetic
# ---------------------------------------------------------------------------
def test_the_override_reaches_the_generated_docx():
    from docx import Document

    updated = app.apply_datetime_overrides(_report(), datetime.date(2026, 10, 1), datetime.time(15, 45))
    doc = Document(io.BytesIO(app.build_default_docx(updated).getvalue()))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "2026-10-01" in text, text
    assert "15:45" in text, text


def test_the_override_reaches_the_template_render_context():
    """A custom Jinja template reads {{ date }} / {{ meeting_time }}."""
    updated = app.apply_datetime_overrides(_report(), datetime.date(2026, 10, 1), datetime.time(15, 45))
    ctx = app._build_render_context(updated.model_dump())
    assert ctx["date"] == "2026-10-01"
    assert ctx["meeting_time"] == "15:45"


# ---------------------------------------------------------------------------
# Formatting back to the string the report stores
# ---------------------------------------------------------------------------
def test_format_helpers_emit_iso_and_24h():
    assert app.format_report_date(datetime.date(2026, 10, 1)) == "2026-10-01"
    assert app.format_report_time(datetime.time(15, 45)) == "15:45"
    assert app.format_report_time(datetime.time(0, 0)) == "00:00"
