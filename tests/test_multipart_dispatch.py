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


def _run(monkeypatch, captured):
    """Invoke the pipeline and capture the single request it emits."""
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))

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
    return report, stats, captured


def test_three_parts_produce_exactly_one_request(monkeypatch):
    captured = []
    _run(monkeypatch, captured)
    assert len(captured) == 1, "parts must NOT be sent as separate requests"


def test_all_three_audio_parts_appear_in_that_single_request(monkeypatch):
    captured = []
    _run(monkeypatch, captured)
    media = [
        p for p in captured[0]["contents"][0]["parts"] if "inline_data" in p
    ]
    assert len(media) == 3


def test_parts_are_ordered_first_to_last(monkeypatch):
    """The transcript is stitched in this order — getting it wrong is silent."""
    import base64

    captured = []
    _run(monkeypatch, captured)
    decoded = [
        base64.b64decode(p["inline_data"]["data"])
        for p in captured[0]["contents"][0]["parts"]
        if "inline_data" in p
    ]
    assert decoded == [b"PART-ONE", b"PART-TWO", b"PART-THREE"]


def test_prompt_asks_for_a_single_continuous_meeting(monkeypatch):
    captured = []
    _run(monkeypatch, captured)
    text = captured[0]["contents"][0]["parts"][0]["text"]
    assert "ONE continuous meeting" in text
    assert "CONSISTENT" in text          # speaker identity across parts
    assert "00:00" in text               # no per-part timestamp reset


def test_single_part_request_carries_no_multi_meeting_instructions(monkeypatch):
    """A one-file upload must behave exactly as before — no extra prompt noise."""
    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
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


def test_audio_bytes_reach_the_model_bit_for_bit(monkeypatch):
    """Quality guarantee: NO transcode/resample/downmix on the send path.

    The whole point of v0.5. An earlier stage forced every part to mono /
    16 kHz / 32 kbps and capped the signal below 8 kHz — a real loss for
    recognition. If a future refactor reintroduces encoding here, this fails.
    Comparing decoded bytes (not lengths) is what makes it a bit-exactness
    check rather than a "looks about right" check.
    """
    import base64

    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
    pb, st_text, log_c, logs = _widgets()

    # A header that any resample/re-encode would rewrite.
    original = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(range(256)) * 8
    app.analyze_meeting_audio_rest(
        audio_file_bytes=original, mime_type="audio/mp3", api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-3.6-flash", progress_bar=pb, status_text=st_text,
        log_container=log_c, logs_list=logs,
    )
    sent = [
        base64.b64decode(p["inline_data"]["data"])
        for p in captured[0]["contents"][0]["parts"]
        if "inline_data" in p
    ]
    assert sent == [original], "audio must be forwarded unaltered"
    assert len(sent[0]) == len(original)


def test_safari_m4a_mime_is_mapped_to_an_accepted_type(monkeypatch):
    """Safari sends `audio/x-m4a`, which the API does not accept.

    Pre-v0.5 this was masked because the transcoder relabelled everything as
    audio/mp3. With raw bytes the label travels as-is, so it must be mapped.
    """
    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
    pb, st_text, log_c, logs = _widgets()

    app.analyze_meeting_audio_rest(
        audio_file_bytes=b"m4a-bytes", mime_type="audio/x-m4a", api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-3.6-flash", progress_bar=pb, status_text=st_text,
        log_container=log_c, logs_list=logs,
    )
    parts = [p for p in captured[0]["contents"][0]["parts"] if "inline_data" in p]
    assert parts[0]["inline_data"]["mime_type"] == "audio/mp4"


def test_openai_path_never_emits_an_unknown_format_hint(monkeypatch):
    """Raw bytes can arrive as m4a/mp4 — the decode hint must stay mp3/wav."""
    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
    pb, st_text, log_c, logs = _widgets()

    app.analyze_meeting_audio_rest(
        audio_file_bytes=b"A", mime_type="audio/x-m4a", api_key="k",
        base_url="https://gateway.internal/v1", model_name="m",
        progress_bar=pb, status_text=st_text, log_container=log_c, logs_list=logs,
    )
    items = [c for c in captured[0]["messages"][0]["content"]
             if c["type"] == "input_audio"]
    assert items[0]["input_audio"]["format"] == "mp3"


def test_openai_compatible_endpoint_emits_multiple_input_audio_items(monkeypatch):
    captured = []
    monkeypatch.setattr(app, "dispatch_http_request", _fake_response(captured))
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
    pb, st_text, log_c, logs = _widgets()

    # Just over the batch ceiling. Derived from the constant so this test tracks
    # the limit rather than hardcoding a number that silently stops being "over".
    over = int((app.media_pipeline.MAX_TOTAL_MB + 1) * 1024 * 1024)
    huge = b"x" * over
    with pytest.raises(RuntimeError, match="over the"):
        app.analyze_meeting_audio_rest(
            audio_file_bytes=huge, mime_type="audio/mp3", api_key="k",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model_name="gemini-3.6-flash", progress_bar=pb, status_text=st_text,
            log_container=log_c, logs_list=logs,
        )
    assert captured == [], "must not dispatch an over-budget batch"
