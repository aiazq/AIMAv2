"""Multi-part dispatch: all parts in ONE request, in the user's order.

This is Enhancement 1's core claim — three recordings of one meeting must reach
the model as a single ordered request, not three independent ones. An ordering
bug here produces a plausible-looking but silently mis-sequenced transcript.

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


VALID_REPORT = {
    "title": "Test Meeting",
    "date": "Undated",
    "attendees": [{"name": "A", "designation": ""}],
    "executive_summary": "Summary.",
    "agenda_and_decisions": [],
    "action_items": [],
    "transcript": [],
}


def _fake_response(payload_capture):
    def _dispatch(endpoint_url, headers, payload):
        payload_capture.append(payload)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(VALID_REPORT)}]}}
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2,
                              "totalTokenCount": 3},
        }
        return resp

    return _dispatch


def _widgets():
    pb = MagicMock()
    st = MagicMock()
    return pb, st, MagicMock(), []


def _run(monkeypatch, captured, parts_calls):
    """Invoke the pipeline with a stubbed compressor so ordering is observable."""
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))

    order = []

    def _fake_compress(raw, suffix, kbps=32):
        # Echo back a marker so we can prove the emitted order exactly.
        order.append(raw)
        return raw

    monkeypatch.setattr(app.media_pipeline, "compress_audio", _fake_compress)

    pb, st_text, log_c, logs = _widgets()
    first = b"PART-ONE"
    extras = [(b"PART-TWO", "audio/mp3"), (b"PART-THREE", "audio/mp3")]

    report, stats = app.analyze_meeting_audio_rest(
        audio_file_bytes=first,
        mime_type="audio/mp3",
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-3.6-flash",
        progress_bar=pb,
        status_text=st_text,
        log_container=log_c,
        logs_list=logs,
        extra_parts=extras,
    )
    parts_calls.extend(order)
    return report, stats, captured


def test_three_parts_produce_exactly_one_request(monkeypatch):
    captured = []
    _run(monkeypatch, captured, [])
    assert len(captured) == 1, "parts must NOT be sent as separate requests"


def test_all_three_audio_parts_appear_in_that_single_request(monkeypatch):
    captured = []
    _run(monkeypatch, captured, [])
    media = [
        p for p in captured[0]["contents"][0]["parts"] if "inline_data" in p
    ]
    assert len(media) == 3


def test_parts_are_ordered_first_to_last(monkeypatch):
    """The transcript is stitched in this order — getting it wrong is silent."""
    import base64

    captured = []
    _run(monkeypatch, captured, [])
    decoded = [
        base64.b64decode(p["inline_data"]["data"])
        for p in captured[0]["contents"][0]["parts"]
        if "inline_data" in p
    ]
    assert decoded == [b"PART-ONE", b"PART-TWO", b"PART-THREE"]


def test_prompt_asks_for_a_single_continuous_meeting(monkeypatch):
    captured = []
    _run(monkeypatch, captured, [])
    text = captured[0]["contents"][0]["parts"][0]["text"]
    assert "ONE continuous meeting" in text
    assert "CONSISTENT" in text          # speaker identity across parts
    assert "00:00" in text               # no per-part timestamp reset


def test_single_part_request_carries_no_multi_meeting_instructions(monkeypatch):
    """A one-file upload must behave exactly as before — no extra prompt noise."""
    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
    monkeypatch.setattr(app.media_pipeline, "compress_audio",
                        lambda raw, suffix, kbps=32: raw)
    pb, st_text, log_c, logs = _widgets()

    app.analyze_meeting_audio_rest(
        audio_file_bytes=b"ONLY", mime_type="audio/mp3", api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-3.6-flash", progress_bar=pb, status_text=st_text,
        log_container=log_c, logs_list=logs,
    )
    parts = captured[0]["contents"][0]["parts"]
    assert len([p for p in parts if "inline_data" in p]) == 1
    assert "ONE continuous meeting" not in parts[0]["text"]


def test_openai_compatible_endpoint_emits_multiple_input_audio_items(monkeypatch):
    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
    monkeypatch.setattr(app.media_pipeline, "compress_audio",
                        lambda raw, suffix, kbps=32: raw)
    pb, st_text, log_c, logs = _widgets()

    app.analyze_meeting_audio_rest(
        audio_file_bytes=b"A", mime_type="audio/mp3", api_key="k",
        base_url="https://gateway.internal/v1", model_name="m",
        progress_bar=pb, status_text=st_text, log_container=log_c, logs_list=logs,
        extra_parts=[(b"B", "audio/mp3")],
    )
    items = [c for c in captured[0]["messages"][0]["content"] if c["type"] == "input_audio"]
    assert len(items) == 2
    assert captured[0]["stream"] is False


def test_over_budget_batch_is_refused_before_dispatch(monkeypatch):
    """Fails fast and legibly instead of uploading everything then timing out."""
    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
    monkeypatch.setattr(app.media_pipeline, "compress_audio",
                        lambda raw, suffix, kbps=32: raw)
    pb, st_text, log_c, logs = _widgets()

    huge = b"x" * (300 * 1024 * 1024)
    with pytest.raises(RuntimeError, match="over the"):
        app.analyze_meeting_audio_rest(
            audio_file_bytes=huge, mime_type="audio/mp3", api_key="k",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model_name="gemini-3.6-flash", progress_bar=pb, status_text=st_text,
            log_container=log_c, logs_list=logs,
        )
    assert captured == [], "must not dispatch an over-budget batch"
