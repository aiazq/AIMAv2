"""Tests for multi-file audio ingestion — Enhancement 1.

The failure modes that matter for a multi-part run, none of which raise an error:
  - wrong ORDER        -> transcript silently stitched out of sequence
  - no total-size gate -> OOM / late timeout after the whole upload finished
  - no compression     -> ~3.7x base64+JSON amplification blows the 1 GB container

Run:  ./.venv/bin/python -m pytest tests/ -q
"""
import io
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import media_pipeline as mp  # noqa: E402

MB = 1024 * 1024


def _parts(*names):
    return [mp.Part(key=n, name=n, size=1000) for n in names]


# --------------------------------------------------------------------- ordering
def test_natural_sort_orders_embedded_numbers_numerically():
    names = ["m_part_10.mp3", "m_part_2.mp3", "m_part_1.mp3"]
    assert sorted(names, key=mp.natural_key) == [
        "m_part_1.mp3",
        "m_part_2.mp3",
        "m_part_10.mp3",
    ]


def test_natural_sort_genuinely_differs_from_lexicographic():
    """Documents WHY natural_key exists: plain sort puts 10 before 2."""
    names = ["m_part_10.mp3", "m_part_2.mp3"]
    assert sorted(names) == ["m_part_10.mp3", "m_part_2.mp3"]  # lexicographic
    assert sorted(names, key=mp.natural_key) == ["m_part_2.mp3", "m_part_10.mp3"]


def test_order_parts_defaults_to_natural_filename_order():
    got = mp.order_parts(_parts("p10.mp3", "p2.mp3", "p1.mp3"), None)
    assert [p.name for p in got] == ["p1.mp3", "p2.mp3", "p10.mp3"]


def test_order_parts_applies_explicit_user_order():
    got = mp.order_parts(_parts("a.mp3", "b.mp3", "c.mp3"),
                         {"c.mp3": 1, "a.mp3": 2, "b.mp3": 3})
    assert [p.name for p in got] == ["c.mp3", "a.mp3", "b.mp3"]


def test_order_parts_pairs_by_key_not_row_position():
    """The silent-corruption guard.

    Two files with the SAME display name but different keys/payloads. An
    index-based implementation pairs the wrong payload with the wrong slot and
    never raises — the merged transcript is just wrong.
    """
    parts = [
        mp.Part(key="k1", name="same.mp3", size=11),
        mp.Part(key="k2", name="same.mp3", size=22),
    ]
    got = mp.order_parts(parts, {"k2": 1, "k1": 2})
    assert [p.key for p in got] == ["k2", "k1"]
    assert [p.size for p in got] == [22, 11]


def test_order_parts_ignores_order_keys_that_match_no_part():
    got = mp.order_parts(_parts("a.mp3", "b.mp3"), {"gone.mp3": 1})
    assert [p.name for p in got] == ["a.mp3", "b.mp3"]


# ----------------------------------------------------------------- size budget
def test_total_bytes_sums_parts():
    assert mp.total_bytes(_parts("a", "b", "c")) == 3000


def test_format_size_reports_mb():
    assert mp.format_size(400 * MB) == "400.0 MB"


def test_validate_batch_accepts_a_batch_within_budget():
    ok, _ = mp.validate_batch([100 * MB, 100 * MB], max_total_mb=260.0)
    assert ok


def test_validate_batch_rejects_oversize_and_names_both_numbers():
    """A late failure is the bug: upload finishes, THEN the API rejects it."""
    ok, msg = mp.validate_batch([200 * MB, 200 * MB], max_total_mb=260.0)
    assert not ok
    assert "400.0 MB" in msg  # what the user actually gave us
    assert "260" in msg       # the limit they hit


def test_validate_batch_accepts_exactly_at_the_limit():
    ok, _ = mp.validate_batch([260 * MB], max_total_mb=260.0)
    assert ok


def test_derived_duration_cap_never_exceeds_its_own_budget():
    """Bitrate and duration multiply to a size bound — derive one, don't pin both."""
    secs = mp.derive_max_seconds(kbps=32, budget_mb=15.0)
    worst = secs * 32 * 1000 / 8
    assert worst <= 15.0 * MB
    assert secs > 3600  # ~65 min at 32 kbps in a 15 MB budget


def test_derive_max_seconds_tracks_bitrate():
    """Halving the bitrate must double the allowable duration."""
    a = mp.derive_max_seconds(kbps=32, budget_mb=15.0)
    b = mp.derive_max_seconds(kbps=64, budget_mb=15.0)
    assert abs(a - 2 * b) <= 2


# ---------------------------------------------------------------- compression
def _wav_bytes(seconds=3, rate=16000, freq=220.0):
    """Speech-ish: a tone is enough to prove bytes shrink and stay decodable."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


@pytest.mark.skipif(not mp.ffmpeg_available(), reason="ffmpeg not installed")
def test_compress_shrinks_a_wav():
    raw = _wav_bytes(3)
    out = mp.compress_audio(raw, ".wav", kbps=32)
    assert len(out) < len(raw)


@pytest.mark.skipif(not mp.ffmpeg_available(), reason="ffmpeg not installed")
def test_compress_output_is_decodable_audio_of_the_same_duration():
    """Shrinking is worthless if the result isn't playable — decode it."""
    raw = _wav_bytes(3)
    out = mp.compress_audio(raw, ".wav", kbps=32)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
        fh.write(out)
        tmp = fh.name
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=format_name,duration", "-of", "default=nw=1", tmp],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        os.unlink(tmp)

    assert probe.returncode == 0, probe.stderr
    assert "mp3" in probe.stdout.lower()
    duration = float([l for l in probe.stdout.splitlines() if l.startswith("duration=")][0]
                     .split("=")[1])
    assert abs(duration - 3.0) < 0.4


@pytest.mark.skipif(not mp.ffmpeg_available(), reason="ffmpeg not installed")
def test_compress_preserves_a_compressed_input_too():
    """An already-MP3 upload must also be transcodeable, not just WAV."""
    raw = mp.compress_audio(_wav_bytes(3), ".wav", kbps=64)
    out = mp.compress_audio(raw, ".mp3", kbps=32)
    assert len(out) < len(raw)  # 64 kbps -> 32 kbps


def test_ffmpeg_available_returns_a_bool():
    assert isinstance(mp.ffmpeg_available(), bool)
