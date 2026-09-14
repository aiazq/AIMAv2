"""Schema robustness for provider responses.

A 43-segment transcript arrived with `translated_text` absent on ONE segment and
Pydantic rejected the ENTIRE report. The provider call had already succeeded, so
minutes of audio processing were discarded over a single missing key.

Run:  ./.venv/bin/python -m pytest tests/ -q
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402


def _base_report(**overrides):
    report = {
        "title": "Test Meeting",
        "date": "Undated",
        "attendees": [{"name": "A", "designation": ""}],
        "detected_speakers": [],
        "executive_summary": "Summary.",
        "agenda_and_decisions": [],
        "action_items": [],
        "transcript": [],
    }
    report.update(overrides)
    return report


def test_the_reported_failure_is_reproduced_by_a_missing_translated_text():
    """Pins the exact defect: one segment without translated_text."""
    segment = {"speaker": "Speaker 1", "timestamp": "01:23", "original_text": "ذیل میں"}
    report = _base_report(transcript=[segment])
    # Documents the pre-fix behaviour (Pydantic rejected the whole report).
    assert "translated_text" not in segment
    parsed = app.MeetingMinutesReport(**report)
    # Falls back to the original text so the segment still renders.
    assert parsed.transcript[0].translated_text == "ذیل میں"


def test_a_lone_bad_segment_does_not_discard_the_other_42():
    """The whole point: 42 good segments must survive one bad one."""
    segments = [
        {"speaker": "Speaker 1", "timestamp": f"0{i}:00",
         "original_text": f"orig {i}", "translated_text": f"trans {i}"}
        for i in range(42)
    ]
    segments.append({"speaker": "Speaker 1", "timestamp": "42:00",
                     "original_text": "last one", "translated_text": "in one meeting tracker."})
    # The exact shape from the field report: index 42 lacks translated_text.
    segments[42] = {"speaker": "Speaker 1", "timestamp": "42:00",
                    "original_text": "last one"}

    parsed = app.MeetingMinutesReport(**_base_report(transcript=segments))
    assert len(parsed.transcript) == 43
    assert parsed.transcript[41].translated_text == "trans 41"
    assert parsed.transcript[42].translated_text == "last one"


def test_missing_translated_text_falls_back_to_original_text():
    """An untranslated segment should render, not vanish."""
    report = _base_report(
        transcript=[{"speaker": "S", "timestamp": "00:01", "original_text": "السلام علیکم"}]
    )
    parsed = app.MeetingMinutesReport(**report)
    assert parsed.transcript[0].translated_text == "السلام علیکم"


def test_missing_original_text_falls_back_to_translated_text():
    report = _base_report(
        transcript=[{"speaker": "S", "timestamp": "00:01", "translated_text": "Hello"}]
    )
    parsed = app.MeetingMinutesReport(**report)
    assert parsed.transcript[0].original_text == "Hello"


def test_both_text_fields_missing_yields_empty_strings_not_an_error():
    """Pydantic v2 rejects None for a `str` field — a null must be coerced."""
    report = _base_report(
        transcript=[{"speaker": "S", "timestamp": "00:01",
                     "original_text": None, "translated_text": None}]
    )
    parsed = app.MeetingMinutesReport(**report)
    assert parsed.transcript[0].original_text == ""
    assert parsed.transcript[0].translated_text == ""


def test_speaker_and_timestamp_tolerate_omission():
    """A segment missing its label must not be fatal either."""
    report = _base_report(transcript=[{"original_text": "x", "translated_text": "y"}])
    parsed = app.MeetingMinutesReport(**report)
    assert parsed.transcript[0].speaker == "Unknown speaker"
    assert parsed.transcript[0].timestamp == ""


def test_complete_segments_pass_through_untouched():
    """Robustness must not mutate a well-formed response."""
    seg = {"speaker": "Speaker 2", "timestamp": "12:34",
           "original_text": "متن", "translated_text": "text"}
    parsed = app.MeetingMinutesReport(**_base_report(transcript=[seg]))
    assert parsed.transcript[0].speaker == "Speaker 2"
    assert parsed.transcript[0].timestamp == "12:34"
    assert parsed.transcript[0].original_text == "متن"
    assert parsed.transcript[0].translated_text == "text"


def test_render_paths_are_safe_for_a_repaired_segment():
    """The render code interpolates these fields directly — must not be None."""
    parsed = app.MeetingMinutesReport(
        **_base_report(transcript=[{"speaker": "S", "timestamp": ""}])
    )
    e = parsed.transcript[0]
    assert isinstance(e.original_text, str) and isinstance(e.translated_text, str)
    assert f"{e.speaker}: {e.translated_text}" == "S: "


def test_end_to_end_dispatch_survives_a_missing_translated_text(monkeypatch):
    """The provider call already succeeded — parsing must not throw it away."""
    captured = []

    def _dispatch(endpoint_url, headers, payload):
        captured.append(payload)
        segments = [
            {"speaker": "Speaker 1", "timestamp": f"0{i}:00",
             "original_text": f"o{i}", "translated_text": f"t{i}"}
            for i in range(5)
        ]
        # A later part boundary segment arrives without translated_text.
        segments.append({"speaker": "Speaker 2", "timestamp": "06:00",
                         "original_text": "boundary"})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [
                {"text": json.dumps(_base_report(transcript=segments))}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2,
                              "totalTokenCount": 3},
        }
        return resp

    monkeypatch.setattr(app, "dispatch_http_request", _dispatch)
    monkeypatch.setattr(app.media_pipeline, "compress_audio",
                        lambda raw, suffix, kbps=32: raw)
    monkeypatch.setattr(app.time, "sleep", lambda *_: None)

    report, stats = app.analyze_meeting_audio_rest(
        audio_file_bytes=b"ONE", mime_type="audio/mp3", api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-3.6-flash",
        progress_bar=MagicMock(), status_text=MagicMock(),
        log_container=MagicMock(), logs_list=[],
        extra_parts=[(b"TWO", "audio/mp3")],
    )
    assert report is not None, "a single malformed segment must not lose the report"
    assert len(report.transcript) == 6
    assert report.transcript[5].translated_text == "boundary"
