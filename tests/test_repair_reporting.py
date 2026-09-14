"""The repair log line must fire — a silent fallback would hide degradation.

Run:  ./.venv/bin/python -m pytest tests/ -q
"""
import json
import os
import sys
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402


def _report(transcript):
    return {
        "title": "T", "date": "Undated", "attendees": [], "detected_speakers": [],
        "executive_summary": "S", "agenda_and_decisions": [], "action_items": [],
        "transcript": transcript,
    }


def _run(monkeypatch, transcript, logs):
    def _dispatch(endpoint_url, headers, payload):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [
                {"text": json.dumps(_report(transcript))}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2,
                              "totalTokenCount": 3},
        }
        return resp

    monkeypatch.setattr(app, "dispatch_http_request", _dispatch)
    monkeypatch.setattr(app.media_pipeline, "compress_audio",
                        lambda raw, suffix, kbps=32: raw)
    monkeypatch.setattr(app.time, "sleep", lambda *_: None)

    report, _ = app.analyze_meeting_audio_rest(
        audio_file_bytes=b"A", mime_type="audio/mp3", api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-3.6-flash", progress_bar=MagicMock(),
        status_text=MagicMock(), log_container=MagicMock(), logs_list=logs,
    )
    return report


def test_incomplete_segment_produces_a_warning_log(monkeypatch):
    logs = []
    _run(monkeypatch, [
        {"speaker": "S1", "timestamp": "00:01", "original_text": "a", "translated_text": "b"},
        {"speaker": "S2", "timestamp": "00:02", "original_text": "boundary"},
    ], logs)
    joined = " ".join(logs)
    assert "Repaired 1 transcript segment" in joined
    assert "log-warn" in joined or "[WARN]" in joined


def test_a_clean_response_logs_no_repair_warning(monkeypatch):
    logs = []
    _run(monkeypatch, [
        {"speaker": "S1", "timestamp": "00:01", "original_text": "a", "translated_text": "b"},
    ], logs)
    assert "Repaired" not in " ".join(logs)
