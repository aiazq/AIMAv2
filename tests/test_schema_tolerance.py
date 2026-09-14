"""The whole class of schema fragility, not just the reported instance.

The reported bug was one missing `translated_text`. But the same shape recurs:
a required field on ANY nested model, omitted on one item, discards the entire
report after the provider call has already succeeded. This file pins that whole
class so a single omission can never again cost the run.

Run:  ./.venv/bin/python -m pytest tests/ -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402


def test_minimal_report_parses():
    """Even a near-empty response must yield a usable report, not an exception."""
    report = app.MeetingMinutesReport(
        title="T", date="Undated", attendees=[], executive_summary="S",
        agenda_and_decisions=[], action_items=[], transcript=[],
    )
    assert report.title == "T"
    assert report.transcript == []


def test_report_with_only_a_title_parses():
    """Every container field is optional — one omission must not be fatal."""
    report = app.MeetingMinutesReport(title="Only a title")
    assert report.attendees == []
    assert report.transcript == []
    assert report.action_items == []
    assert report.agenda_and_decisions == []
    assert report.detected_speakers == []
    assert report.date == "Undated"
    assert report.executive_summary == ""


def test_action_item_missing_owner_does_not_kill_the_report():
    report = app.MeetingMinutesReport(
        title="T", attendees=[], agenda_and_decisions=[],
        action_items=[{"task": "Send the tracker", "deadline": "Friday"}],
        transcript=[],
    )
    assert report.action_items[0].task == "Send the tracker"
    assert report.action_items[0].owner == ""


def test_action_item_missing_deadline_does_not_kill_the_report():
    report = app.MeetingMinutesReport(
        title="T", attendees=[], agenda_and_decisions=[],
        action_items=[{"task": "X", "owner": "Y"}], transcript=[],
    )
    assert report.action_items[0].deadline == ""


def test_agenda_item_partially_filled_parses():
    report = app.MeetingMinutesReport(
        title="T", attendees=[], action_items=[], transcript=[],
        agenda_and_decisions=[{"topic": "Budget"}],
    )
    assert report.agenda_and_decisions[0].topic == "Budget"
    assert report.agenda_and_decisions[0].decisions_made == []


def test_attendee_missing_name_parses():
    report = app.MeetingMinutesReport(
        title="T", agenda_and_decisions=[], action_items=[],
        attendees=[{"designation": "Chair"}], transcript=[],
    )
    assert report.attendees[0].name == ""
    assert report.attendees[0].designation == "Chair"


def test_detected_speaker_partially_filled_parses():
    report = app.MeetingMinutesReport(
        title="T", attendees=[], agenda_and_decisions=[], action_items=[],
        transcript=[], detected_speakers=[{"speaker_id": "Speaker 1"}],
    )
    assert report.detected_speakers[0].speaker_id == "Speaker 1"
    assert report.detected_speakers[0].inferred_name == ""


def test_explicit_nulls_everywhere_still_parse():
    """Providers emit null where a string is expected — coerce, don't reject."""
    report = app.MeetingMinutesReport(
        title=None, date=None, executive_summary=None, minute_taker=None,
        attendees=[{"name": None, "designation": None}],
        agenda_and_decisions=[{"topic": None, "discussion_summary": None,
                               "decisions_made": None}],
        action_items=[{"task": None, "owner": None, "deadline": None}],
        transcript=[{"speaker": None, "timestamp": None,
                     "original_text": None, "translated_text": None}],
    )
    assert isinstance(report.title, str)
    assert report.attendees[0].name == ""
    assert report.agenda_and_decisions[0].decisions_made == []
    assert report.action_items[0].owner == ""
    assert report.transcript[0].translated_text == ""


def test_decisions_made_accepts_a_bare_string_from_the_model():
    """A single decision often arrives as a string rather than a list."""
    report = app.MeetingMinutesReport(
        title="T", attendees=[], action_items=[], transcript=[],
        agenda_and_decisions=[{"topic": "X", "decisions_made": "Approved"}],
    )
    assert report.agenda_and_decisions[0].decisions_made == ["Approved"]


def test_transcript_hole_in_the_middle_is_preserved_in_order():
    """Ordering and count must survive a repaired segment."""
    segments = []
    for i in range(6):
        seg = {"speaker": f"S{i}", "timestamp": f"0{i}:00",
               "original_text": f"o{i}", "translated_text": f"t{i}"}
        if i == 3:
            del seg["translated_text"]
        segments.append(seg)

    report = app.MeetingMinutesReport(
        title="T", attendees=[], agenda_and_decisions=[], action_items=[],
        transcript=segments,
    )
    assert len(report.transcript) == 6
    assert [e.speaker for e in report.transcript] == [f"S{i}" for i in range(6)]
    assert report.transcript[3].translated_text == "o3"


def test_model_dump_round_trips_for_export():
    """The JSON export and docx render consume model_dump() — must be complete."""
    report = app.MeetingMinutesReport(
        title="T", attendees=[], agenda_and_decisions=[], action_items=[],
        transcript=[{"speaker": "S", "timestamp": "00:01", "original_text": "x"}],
    )
    dumped = report.model_dump()
    assert dumped["transcript"][0]["translated_text"] == "x"
    assert dumped["transcript"][0]["speaker"] == "S"
