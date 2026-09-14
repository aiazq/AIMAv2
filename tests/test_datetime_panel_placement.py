"""The Date & Start Time panel is rendered ABOVE the Speaker Identity panel.

Presence is not placement. A panel that renders below the speaker mapping does
not satisfy the request, and nothing in a normal unit test would notice — so
this asserts on render ORDER via AppTest, in a subprocess because tests/ststub.py
installs a MagicMock as sys.modules["streamlit"], which breaks AppTest in-process.

The result block only renders when `st.session_state["meeting_result"]` is set,
so the probe seeds one and re-runs.
"""
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_PROBE = r"""
import datetime, json, os, sys
from streamlit.testing.v1 import AppTest

at = AppTest.from_file(sys.argv[1], default_timeout=120)
at.secrets["API_KEY"] = "dummy-not-a-real-secret"
at.run()

# Seed a finished report so the result block renders. `app` reads st.secrets at
# MODULE level, which raises outside a script context, so neutralise the secrets
# file lookup before importing it.
os.environ["API_KEY"] = "dummy-not-a-real-secret"
from streamlit.runtime.secrets import Secrets
Secrets._parse = lambda self: {}

sys.path.insert(0, sys.argv[2])
import app as _app

report = _app.MeetingMinutesReport(
    title="Quarterly Review",
    date="Undated",
    meeting_time="",
    attendees=[_app.Attendee(name="Ayesha Khan", designation="CFO")],
    detected_speakers=[_app.DetectedSpeaker(speaker_id="Speaker 1", inferred_name="Ayesha Khan")],
    executive_summary="Summary.",
    agenda_and_decisions=[_app.AgendaItem(topic="Budget", discussion_summary="Discussed.")],
    action_items=[_app.ActionItem(owner="Ayesha Khan", task="Send figures")],
    transcript=[_app.TranscriptEntry(speaker="Speaker 1", timestamp="00:01",
                                     original_text="Assalam o alaikum.", translated_text="Hello.")],
)
at.session_state["meeting_result"] = report
at.run()

def label_of(e):
    try:
        return str(e.label)
    except Exception:
        return ""

expander_labels = [label_of(e) for e in at.expander]

print(json.dumps({
    "exceptions": [str(e.value) for e in at.exception],
    "warnings": [str(w.value) for w in at.warning],
    "expander_labels": expander_labels,
    "date_inputs": [(str(d.label), str(d.value)) for d in at.date_input],
    "time_inputs": [(str(t.label), str(t.value)) for t in at.time_input],
    "buttons": [str(b.label) for b in at.button],
}))
"""


def _render_with_report():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, os.path.join(ROOT, "app.py"), ROOT],
        capture_output=True, text=True, timeout=420, cwd=ROOT,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr[-2500:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _index_of(labels, needle):
    for i, label in enumerate(labels):
        if needle.lower() in label.lower():
            return i
    return -1


def test_the_panel_renders_and_the_page_has_no_exceptions():
    r = _render_with_report()
    assert r["exceptions"] == [], r["exceptions"]


def test_both_widgets_are_present():
    r = _render_with_report()
    assert len(r["date_inputs"]) == 1, r["date_inputs"]
    assert len(r["time_inputs"]) == 1, r["time_inputs"]
    assert r["date_inputs"][0][0] == "Meeting Date", r["date_inputs"]
    assert r["time_inputs"][0][0] == "Meeting Start Time", r["time_inputs"]


def test_the_panel_sits_above_the_speaker_panel():
    """The actual requirement: ORDER, not mere presence."""
    r = _render_with_report()
    labels = r["expander_labels"]
    dt_i = _index_of(labels, "Date & Start Time")
    spk_i = _index_of(labels, "Speaker Identity Mapping")
    assert dt_i != -1, f"datetime panel not found in expanders: {labels}"
    assert spk_i != -1, f"speaker panel not found in expanders: {labels}"
    assert dt_i < spk_i, f"datetime panel (index {dt_i}) must precede speaker panel (index {spk_i}): {labels}"


def test_save_button_is_present():
    r = _render_with_report()
    assert any("Save Date & Time" in b for b in r["buttons"]), r["buttons"]


# ---------------------------------------------------------------------------
# The unreadable-date path, which is the case that would otherwise crash
# ---------------------------------------------------------------------------
def test_an_undated_report_renders_without_crashing():
    """'Undated' is the schema default — the panel must survive it."""
    r = _render_with_report()
    assert r["exceptions"] == [], r["exceptions"]
    # date_input must hold a real date, not "Undated".
    value = r["date_inputs"][0][1]
    assert re.match(r"\d{4}-\d{2}-\d{2}", value), value


def test_an_undated_report_warns_rather_than_silently_guessing():
    """Falling back to today is a guess; the user must know it was a guess."""
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "Defaulted to today" in src, "expected an explicit warning on unreadable dates"
    assert re.search(r"if _date_unreadable:", src), "the warning must be conditional"
