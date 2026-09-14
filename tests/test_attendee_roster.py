"""Items 3 and 5 — the Attendee Roster.

Item 3 (a BUG): renaming a speaker left the stale diarization label in the roster.
`apply_speaker_replacements` only *appended* the confirmed name, so a report whose
attendees were ["Speaker 1", "Speaker 2"] ended up as
["Speaker 1", "Speaker 2", "Ayesha Khan", "Bilal"] — the labels the user had just
replaced were still on the page, exactly as reported.

Item 5: the roster must be editable, with edits reaching the data and the download.
"""
import io
import os
import json
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402


def _report(attendees=None, transcript=None):
    return app.MeetingMinutesReport(
        title="Quarterly Review",
        date="2026-09-12",
        attendees=attendees if attendees is not None else [],
        transcript=transcript if transcript is not None else [],
    )


def _labels():
    return [
        app.Attendee(name="Speaker 1", designation="Participant"),
        app.Attendee(name="Speaker 2", designation="Participant"),
    ]


# ---------------------------------------------------------------------------
# Item 3 — renaming speakers must not leave the old label behind
# ---------------------------------------------------------------------------
def test_renaming_a_speaker_replaces_the_stale_attendee_label():
    """The reported bug, verbatim: roster still shows Speaker 1 / Speaker 2."""
    before = _report(
        attendees=_labels(),
        transcript=[app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
    )
    after = app.apply_speaker_replacements(before, {"Speaker 1": "Ayesha Khan"})

    names = [a.name for a in after.attendees]
    assert "Speaker 1" not in names, f"stale label survived: {names}"
    assert "Ayesha Khan" in names, names


def test_renaming_does_not_duplicate_the_attendee():
    before = _report(
        attendees=_labels(),
        transcript=[app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
    )
    after = app.apply_speaker_replacements(before, {"Speaker 1": "Ayesha Khan"})
    names = [a.name for a in after.attendees]
    assert names.count("Ayesha Khan") == 1, names


def test_the_renamed_attendee_keeps_its_designation():
    """The label is renamed, not replaced by a fresh 'Participant' row."""
    before = _report(
        attendees=[app.Attendee(name="Speaker 1", designation="Chief Financial Officer")],
        transcript=[app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
    )
    after = app.apply_speaker_replacements(before, {"Speaker 1": "Ayesha Khan"})
    assert after.attendees[0].designation == "Chief Financial Officer", after.attendees[0]


def test_renaming_all_speakers_clears_every_label():
    before = _report(
        attendees=_labels(),
        transcript=[
            app.TranscriptEntry(speaker="Speaker 1", translated_text="a"),
            app.TranscriptEntry(speaker="Speaker 2", translated_text="b"),
        ],
    )
    after = app.apply_speaker_replacements(
        before, {"Speaker 1": "Ayesha Khan", "Speaker 2": "Bilal Ahmed"}
    )
    names = [a.name for a in after.attendees]
    assert not any(re.fullmatch(r"Speaker \d+", n) for n in names), names
    assert "Ayesha Khan" in names and "Bilal Ahmed" in names, names


def test_a_speaker_with_no_existing_attendee_is_still_added():
    """The original append behaviour must survive the fix."""
    before = _report(
        attendees=[app.Attendee(name="Ayesha Khan", designation="CFO")],
        transcript=[app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
    )
    after = app.apply_speaker_replacements(before, {"Speaker 1": "Bilal Ahmed"})
    names = [a.name for a in after.attendees]
    assert "Bilal Ahmed" in names, names
    assert "Ayesha Khan" in names, names


def test_an_already_correct_attendee_is_not_duplicated():
    """Rename to a name already on the roster must collapse, not add."""
    before = _report(
        attendees=[
            app.Attendee(name="Speaker 1", designation="Participant"),
            app.Attendee(name="Ayesha Khan", designation="CFO"),
        ],
        transcript=[app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
    )
    after = app.apply_speaker_replacements(before, {"Speaker 1": "Ayesha Khan"})
    names = [a.name for a in after.attendees]
    assert names.count("Ayesha Khan") == 1, names


def test_a_real_attendee_not_named_like_a_speaker_is_untouched():
    before = _report(
        attendees=[app.Attendee(name="Sana Tariq", designation="Legal")],
        transcript=[app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
    )
    after = app.apply_speaker_replacements(before, {"Speaker 1": "Ayesha Khan"})
    assert "Sana Tariq" in [a.name for a in after.attendees]


def test_the_rename_still_reaches_the_transcript_and_owners():
    """Guard the pre-existing behaviour the fix must not break."""
    before = _report(
        attendees=_labels(),
        transcript=[app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
    )
    before.action_items = [app.ActionItem(owner="Speaker 1", task="Send figures")]
    after = app.apply_speaker_replacements(before, {"Speaker 1": "Ayesha Khan"})
    assert after.transcript[0].speaker == "Ayesha Khan"
    assert after.action_items[0].owner == "Ayesha Khan"


def test_speaker_replacement_does_not_mutate_the_original():
    original = _report(attendees=_labels())
    app.apply_speaker_replacements(original, {"Speaker 1": "Ayesha Khan"})
    assert [a.name for a in original.attendees] == ["Speaker 1", "Speaker 2"]


# ---------------------------------------------------------------------------
# Item 5 — the roster is editable and edits reach the data
# ---------------------------------------------------------------------------
def test_the_roster_can_be_replaced():
    out = app.apply_attendee_roster(
        _report(attendees=_labels()),
        [{"name": "Ayesha Khan", "designation": "CFO"}],
    )
    assert [(a.name, a.designation) for a in out.attendees] == [("Ayesha Khan", "CFO")]


def test_a_renamed_attendee_reaches_the_downloaded_document():
    """Otherwise the editor is cosmetic — same class of defect as item 1."""
    from docx import Document

    out = app.apply_attendee_roster(
        _report(attendees=[app.Attendee(name="Speaker 1", designation="Participant")]),
        [{"name": "Ayesha Khan", "designation": "Chief Financial Officer"}],
    )
    doc = Document(io.BytesIO(app.build_default_docx(out).getvalue()))
    text = "\n".join(p.text for p in doc.paragraphs)
    table_text = "\n".join(
        c.text for t in doc.tables for r in t.rows for c in r.cells
    )
    combined = text + table_text
    assert "Ayesha Khan" in combined, combined
    assert "Speaker 1" not in combined, combined


def test_an_added_attendee_reaches_the_downloaded_document():
    from docx import Document

    out = app.apply_attendee_roster(
        _report(attendees=[app.Attendee(name="Ayesha Khan", designation="CFO")]),
        [
            {"name": "Ayesha Khan", "designation": "CFO"},
            {"name": "Bilal Ahmed", "designation": "Head of Ops"},
        ],
    )
    doc = Document(io.BytesIO(app.build_default_docx(out).getvalue()))
    cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
    assert any("Bilal Ahmed" in c for c in cells), cells


def test_an_edited_roster_reaches_a_custom_template_context():
    out = app.apply_attendee_roster(
        _report(attendees=[app.Attendee(name="Speaker 1", designation="Participant")]),
        [{"name": "Ayesha Khan", "designation": "CFO"}],
    )
    ctx = app._build_render_context(out.model_dump())
    assert ctx["attendees"][0]["name"] == "Ayesha Khan"
    assert ctx["attendees"][0]["designation"] == "CFO"


def test_blank_rows_added_by_the_editor_are_dropped():
    """`num_rows="dynamic"` leaves a trailing empty row; a nameless attendee
    must never reach the minutes."""
    out = app.apply_attendee_roster(
        _report(attendees=_labels()),
        [
            {"name": "Ayesha Khan", "designation": "CFO"},
            {"name": None, "designation": None},
            {"name": "", "designation": ""},
        ],
    )
    assert [(a.name, a.designation) for a in out.attendees] == [("Ayesha Khan", "CFO")]


def test_a_nan_cell_does_not_become_the_word_nan():
    """A cleared grid cell arrives as NaN, and str(nan) == 'nan' — it would be
    printed into the downloaded minutes."""
    out = app.apply_attendee_roster(
        _report(attendees=_labels()),
        [{"name": "Ayesha Khan", "designation": float("nan")}],
    )
    assert out.attendees[0].designation == "", out.attendees[0]


@pytest.mark.parametrize("token", ["nan", "NaN", "NAN", "<NA>", "None", "  <NA>  "])
def test_stringified_null_tokens_are_scrubbed_too(token):
    """The second half of the scrub.

    NaN is not always a float by the time it arrives: if anything stringifies the
    frame first (`df.astype(str)`), pandas hands over the *literal text* "nan" /
    "<NA>" / "None" / "NaT". Falsification showed this branch had no coverage at
    all — removing it left every test green — so it is pinned here.
    """
    out = app.apply_attendee_roster(
        _report(attendees=_labels()),
        [{"name": "Ayesha Khan", "designation": token}],
    )
    assert out.attendees[0].designation == "", (token, out.attendees[0])


def test_a_genuine_designation_containing_nan_substring_is_kept():
    """Scrub must match the WHOLE cell, not a substring."""
    out = app.apply_attendee_roster(
        _report(attendees=_labels()),
        [{"name": "Ayesha Khan", "designation": "Finance Manager (Nanotechnology)"}],
    )
    assert "Nanotechnology" in out.attendees[0].designation, out.attendees[0]


def test_a_partial_row_is_kept():
    """A name with no designation is still a valid attendee."""
    out = app.apply_attendee_roster(
        _report(attendees=[]),
        [{"name": "Ayesha Khan", "designation": ""}],
    )
    assert [(a.name, a.designation) for a in out.attendees] == [("Ayesha Khan", "")]


def test_cells_are_trimmed():
    out = app.apply_attendee_roster(
        _report(attendees=[]),
        [{"name": "  Ayesha Khan  ", "designation": "  CFO  "}],
    )
    assert (out.attendees[0].name, out.attendees[0].designation) == ("Ayesha Khan", "CFO")


def test_an_empty_roster_is_allowed():
    """Deleting every row must clear the roster, not be silently ignored."""
    out = app.apply_attendee_roster(_report(attendees=_labels()), [])
    assert out.attendees == []


def test_applying_a_roster_does_not_mutate_the_original():
    original = _report(attendees=_labels())
    app.apply_attendee_roster(original, [{"name": "X", "designation": "Y"}])
    assert [a.name for a in original.attendees] == ["Speaker 1", "Speaker 2"]


def test_applying_a_roster_preserves_everything_else():
    before = _report(attendees=_labels())
    before.action_items = [app.ActionItem(owner="A", task="T")]
    after = app.apply_attendee_roster(before, [{"name": "X", "designation": "Y"}])
    assert after.title == before.title
    assert after.date == before.date
    assert len(after.action_items) == 1


# ---------------------------------------------------------------------------
# Item 5 — render level: the roster tab must be an EDITOR, not a read-only grid
# ---------------------------------------------------------------------------
_EDITOR_PROBE = r"""
import json, os, sys
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
    date="2026-09-12",
    attendees=[_app.Attendee(name="Speaker 1", designation="Participant")],
    transcript=[_app.TranscriptEntry(speaker="Speaker 1", translated_text="Hi")],
)
at.session_state["meeting_result"] = report
at.run()

# READ_ONLY=0, FIXED=1 (st.dataframe), DYNAMIC=2 (st.data_editor)
modes = [int(e.proto.editing_mode) for e in at.get("dataframe")]
print(json.dumps({
    "exceptions": [str(e.value) for e in at.exception],
    "editing_modes": modes,
    "buttons": [str(b.label) for b in at.button],
}))
"""


def _render_editor():
    proc = subprocess.run(
        [sys.executable, "-c", _EDITOR_PROBE, os.path.join(ROOT, "app.py"), ROOT],
        capture_output=True, text=True, timeout=420, cwd=ROOT,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr[-2500:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_roster_renders_without_exceptions():
    r = _render_editor()
    assert r["exceptions"] == [], r["exceptions"]


def test_the_roster_tab_is_editable_not_read_only():
    """DYNAMIC editing mode is the difference between a viewer and an editor."""
    r = _render_editor()
    assert 2 in r["editing_modes"], (
        "no editable grid rendered — the roster is still read-only: "
        f"{r['editing_modes']}"
    )


def test_there_is_a_way_to_commit_the_roster_edits():
    r = _render_editor()
    assert any("Attendee" in b or "Roster" in b for b in r["buttons"]), r["buttons"]
