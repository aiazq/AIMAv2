"""Date/time must reach the DATA and the DOCUMENT, not just the widget.

Requested behaviour (verbatim):
  1. "The meeting date defaults to today: that's fine. it should also carry this
     default to the data and the document that will be downloaded."
  2. "When the date and time are updated, this should be updated across all data
     including the document that will be downloaded. The same as is already done
     for the speakers."
  4. "The Meeting start time should also be displayed up top after the Date"

The trap for (1): the panel already *shows* today, because the widget's default
arg is today. But `result.date` still says "Undated", so the header and the
downloaded .docx say "Undated" too — the widget default never reaches the data.
Showing a date the document does not contain is the defect.
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


def _report(**over):
    base = dict(
        title="Quarterly Review",
        date="2026-09-12",
        meeting_time="10:00",
        attendees=[app.Attendee(name="Ayesha Khan", designation="CFO")],
        executive_summary="Summary.",
    )
    base.update(over)
    return app.MeetingMinutesReport(**base)


def _docx_text(report):
    from docx import Document

    doc = Document(io.BytesIO(app.build_default_docx(report).getvalue()))
    return "\n".join(p.text for p in doc.paragraphs)


# ---------------------------------------------------------------------------
# Item 1 — the "Undated" report must carry today's date into the data
# ---------------------------------------------------------------------------
def test_undated_report_gets_today_written_into_the_data():
    """The whole point of item 1: the default must land in `result.date`."""
    today = datetime.date(2026, 9, 14)
    out = app.ensure_report_date(_report(date="Undated"), today)
    assert out.date == "2026-09-14", out.date


def test_the_defaulted_date_reaches_the_downloaded_document():
    """Otherwise the panel shows a date the .docx contradicts."""
    today = datetime.date(2026, 9, 14)
    out = app.ensure_report_date(_report(date="Undated"), today)
    text = _docx_text(out)
    assert "2026-09-14" in text, text
    assert "Undated" not in text, text


@pytest.mark.parametrize("raw", ["Undated", "", "   ", "TBD", "n/a", "unknown"])
def test_every_empty_ish_date_default_token_is_defaulted(raw):
    out = app.ensure_report_date(_report(date=raw), datetime.date(2026, 9, 14))
    assert out.date == "2026-09-14", (raw, out.date)


def test_a_readable_date_is_left_alone():
    """A real date from the transcript must never be overwritten by today."""
    out = app.ensure_report_date(_report(date="2026-09-12"), datetime.date(2026, 9, 14))
    assert out.date == "2026-09-12", out.date


def test_a_verbose_real_date_is_left_alone_not_reformatted():
    """'12 September 2026' is readable, so it must survive untouched.

    Reformatting it to ISO would be a silent edit of a value the user never
    asked to change.
    """
    out = app.ensure_report_date(_report(date="12 September 2026"), datetime.date(2026, 9, 14))
    assert out.date == "12 September 2026", out.date


def test_ensure_report_date_does_not_mutate_the_original():
    original = _report(date="Undated")
    app.ensure_report_date(original, datetime.date(2026, 9, 14))
    assert original.date == "Undated"


def test_ensure_report_date_preserves_everything_else():
    before = _report(date="Undated")
    after = app.ensure_report_date(before, datetime.date(2026, 9, 14))
    assert after.title == before.title
    assert after.meeting_time == before.meeting_time
    assert [a.name for a in after.attendees] == [a.name for a in before.attendees]


# ---------------------------------------------------------------------------
# Item 2 — both overrides must reach the .docx
# ---------------------------------------------------------------------------
def test_the_editable_time_reaches_the_generated_docx():
    updated = app.apply_datetime_overrides(
        _report(), datetime.date(2026, 10, 1), datetime.time(15, 45)
    )
    text = _docx_text(updated)
    assert "15:45" in text, text


def test_both_date_and_time_reach_the_docx_together():
    updated = app.apply_datetime_overrides(
        _report(), datetime.date(2026, 10, 1), datetime.time(15, 45)
    )
    text = _docx_text(updated)
    assert "2026-10-01" in text, text
    assert "15:45" in text, text


def test_the_editable_time_reaches_a_custom_template_context():
    """Custom .docx templates read {{ meeting_time }} — the same field."""
    updated = app.apply_datetime_overrides(
        _report(), datetime.date(2026, 10, 1), datetime.time(15, 45)
    )
    ctx = app._build_render_context(updated.model_dump())
    assert ctx["date"] == "2026-10-01"
    assert ctx["meeting_time"] == "15:45"


# ---------------------------------------------------------------------------
# Item 4 — the header line shows the start time after the date
# ---------------------------------------------------------------------------
_HEADER_PROBE = r"""
import datetime, json, os, sys
from streamlit.testing.v1 import AppTest

at = AppTest.from_file(sys.argv[1], default_timeout=120)
at.secrets["API_KEY"] = "dummy-not-a-real-secret"
at.run()

os.environ["API_KEY"] = "dummy-not-a-real-secret"
from streamlit.runtime.secrets import Secrets
Secrets._parse = lambda self: {}
sys.path.insert(0, sys.argv[2])
import app as _app

report = _app.MeetingMinutesReport(
    title="Quarterly Review",
    date=os.environ.get("PROBE_DATE", "2026-09-12"),
    meeting_time=os.environ.get("PROBE_TIME", "10:00"),
    attendees=[_app.Attendee(name="Ayesha Khan", designation="CFO")],
    executive_summary="Summary.",
)
at.session_state["meeting_result"] = report
at.run()

print(json.dumps({
    "exceptions": [str(e.value) for e in at.exception],
    "captions": [str(c.value) for c in at.caption],
}))
"""


def _render_header(date="2026-09-12", time="10:00"):
    import json
    import subprocess

    env = dict(os.environ, PROBE_DATE=date, PROBE_TIME=time)
    proc = subprocess.run(
        [sys.executable, "-c", _HEADER_PROBE, os.path.join(ROOT, "app.py"), ROOT],
        capture_output=True, text=True, timeout=420, cwd=ROOT, env=env,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr[-2500:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_header_shows_the_start_time():
    r = _render_header()
    joined = " ".join(r["captions"])
    assert "10:00" in joined, r["captions"]


def test_the_header_shows_the_time_after_the_date():
    """Ordering is the actual ask: 'displayed up top after the Date'."""
    r = _render_header()
    line = next((c for c in r["captions"] if "2026-09-12" in c), None)
    assert line is not None, r["captions"]
    assert line.index("2026-09-12") < line.index("10:00"), line


def test_the_header_omits_the_time_when_there_is_none():
    """An empty time must not render as a dangling label."""
    r = _render_header(time="")
    line = next((c for c in r["captions"] if "2026-09-12" in c), None)
    assert line is not None, r["captions"]
    assert "Time:" not in line, line
