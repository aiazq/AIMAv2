"""Tests for the live-recording audio source (mic input) alongside file upload.

Focus is on the parts that can silently corrupt a run:
  - source precedence (recording vs upload vs neither)
  - correct MIME for a browser recording (audio/wav, never octet-stream)
  - duration read from the WAV header, so the UI can warn BEFORE dispatching
  - the inline-payload budget guard (Gemini caps the whole request at 20 MB)

Run:  ./.venv/bin/python -m pytest tests/ -q
"""
import io
import os
import struct
import sys
import wave

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_wav(seconds=1.0, rate=16000, channels=1, sampwidth=2, fmt="pcm"):
    """Build a real WAV via the stdlib encoder (deterministic size)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        n = int(rate * seconds)
        w.writeframes(b"\x00" * (n * channels * sampwidth))
    return buf.getvalue()


class FakeUploaded:
    """Duck-type for st.file_uploader / st.audio_input results."""

    def __init__(self, data, name, mime=None, declared_type=None):
        self._data = data
        self.name = name
        self.type = mime if mime is not None else declared_type

    def read(self):
        return self._data

    def getvalue(self):
        return self._data


# ---------------------------------------------------------------------------
# source precedence
# ---------------------------------------------------------------------------
def test_recording_is_used_when_only_recording_present():
    rec = FakeUploaded(make_wav(2.0), "recording.wav", "audio/wav")
    out = app.resolve_audio_source(rec, None)
    assert out is not None
    raw, mime, name = out
    assert len(raw) == len(rec.getvalue())
    assert mime == "audio/wav"


def test_upload_is_used_when_only_upload_present():
    up = FakeUploaded(make_wav(2.0, rate=44100), "meeting.mp3", "audio/mpeg")
    out = app.resolve_audio_source(None, up)
    assert out is not None
    raw, mime, name = out
    assert mime == "audio/mpeg"
    assert name == "meeting.mp3"


def test_recording_wins_when_both_present():
    """A fresh mic take must beat a stale upload sitting in the widget."""
    rec = FakeUploaded(make_wav(1.0, rate=8000), "recording.wav", "audio/wav")
    up = FakeUploaded(make_wav(9.0, rate=44100), "old_upload.mp3", "audio/mpeg")
    out = app.resolve_audio_source(rec, up)
    assert out is not None
    _raw, mime, name = out
    assert mime == "audio/wav"
    assert name == "recording.wav"


def test_returns_none_when_neither_present():
    assert app.resolve_audio_source(None, None) is None


def test_returns_none_for_empty_recording():
    """st.audio_input can yield a zero-length read; must not dispatch it."""
    assert app.resolve_audio_source(FakeUploaded(b"", "recording.wav", "audio/wav"), None) is None


def test_missing_mime_defaults_to_wav_for_recording_and_mp3_for_upload():
    rec = FakeUploaded(make_wav(1.0), "recording.wav", None)
    up = FakeUploaded(b"ID3\x00\x00", "song.bin", None)
    assert app.resolve_audio_source(rec, None)[1] == "audio/wav"
    assert app.resolve_audio_source(None, up)[1] == "audio/mpeg"


# ---------------------------------------------------------------------------
# WAV duration parsing — drives the pre-flight warning
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rate", [8000, 16000, 22050, 44100, 48000])
@pytest.mark.parametrize("seconds", [1.0, 7.5, 60.0])
def test_wav_duration_is_read_from_header(rate, seconds):
    data = make_wav(seconds, rate=rate)
    dur = app.wav_duration_seconds(data)
    assert dur == pytest.approx(seconds, rel=0.02)


def test_wav_duration_handles_stereo_without_doubling_length():
    """Stereo doubles the byte count; duration must stay the same."""
    mono = make_wav(5.0, rate=16000, channels=1)
    stereo = make_wav(5.0, rate=16000, channels=2)
    assert len(stereo) == pytest.approx(len(mono) * 2, rel=0.01)
    assert app.wav_duration_seconds(stereo) == pytest.approx(5.0, rel=0.02)


def test_wav_duration_handles_8bit_and_24bit():
    for width in (1, 3):
        data = make_wav(3.0, rate=16000, sampwidth=width)
        assert app.wav_duration_seconds(data) == pytest.approx(3.0, rel=0.02)


def test_wav_duration_returns_none_for_non_wav():
    assert app.wav_duration_seconds(b"ID3\x04\x00\x00\x00not a wav") is None
    assert app.wav_duration_seconds(b"") is None


def test_wav_duration_survives_odd_extra_chunks():
    """Real browser WAVs carry LIST/fact chunks before 'data'."""
    base = make_wav(2.0, rate=16000)
    # splice a LIST chunk immediately after the RIFF/WAVE header
    extra = b"LIST" + struct.pack("<I", 8) + b"INFOIART"
    patched = base[:12] + extra + base[12:]
    patched = patched[:4] + struct.pack("<I", len(patched) - 8) + patched[8:]
    assert app.wav_duration_seconds(patched) == pytest.approx(2.0, rel=0.02)


# ---------------------------------------------------------------------------
# inline budget guard — the 20 MB hard wall
# ---------------------------------------------------------------------------
def test_inline_budget_matches_base64_inflation():
    """20 MB request cap, minus prompt overhead, inflated by 4/3 → raw budget."""
    raw_mb = app.inline_raw_budget_mb()
    assert 14.0 < raw_mb < 15.0, raw_mb


def test_small_recording_is_within_budget():
    ok, msg = app.check_inline_budget(make_wav(60.0), "audio/wav")
    assert ok is True
    assert msg == ""


def test_long_16k_recording_exceeds_budget():
    """16 kHz mono WAV ≈ 32 KB/s → ~8 min fits inline, 20 min does not."""
    ok, msg = app.check_inline_budget(make_wav(20 * 60.0), "audio/wav")
    assert ok is False
    assert "20" in msg  # names the cap so the user can act on it


def test_budget_boundary_is_measured_in_encoded_body_not_raw_bytes():
    """Raw bytes just under 15 MB pass; the guard must use the *encoded* size."""
    just_under = b"\x00" * int(app.inline_raw_budget_mb() * 1024 * 1024 * 0.98)
    just_over = b"\x00" * int(app.inline_raw_budget_mb() * 1024 * 1024 * 1.05)
    assert app.check_inline_budget(just_under, "audio/wav")[0] is True
    assert app.check_inline_budget(just_over, "audio/wav")[0] is False


def test_budget_message_reports_estimated_minutes_for_wav():
    ok, msg = app.check_inline_budget(make_wav(30 * 60.0, rate=16000), "audio/wav")
    assert ok is False
    assert "MB" in msg
    # A 30-min 16 kHz WAV is ~55 MB raw → ~8 min budget. Guard against the
    # MB/byte unit mix-up that once printed "roughly 0.0 minutes".
    assert "roughly 8" in msg, msg


def test_budget_message_minutes_scale_with_sample_rate():
    """8 kHz doubles the headroom vs 16 kHz; the message must reflect that."""
    _ok, msg16 = app.check_inline_budget(make_wav(20 * 60.0, rate=16000), "audio/wav")
    _ok, msg8 = app.check_inline_budget(make_wav(20 * 60.0, rate=8000), "audio/wav")
    import re

    lim16 = float(re.search(r"roughly ([\d.]+) minutes", msg16).group(1))
    lim8 = float(re.search(r"roughly ([\d.]+) minutes", msg8).group(1))
    assert lim8 == pytest.approx(lim16 * 2, rel=0.05), (lim8, lim16)


# ---------------------------------------------------------------------------
# Max recording duration — pins the numbers we quote to the user.
#
# Streamlit's browser encoder (frontend/lib/src/components/audio/core/
# encodeToWav.ts) always emits MONO, 16-bit PCM WAV with byte_rate =
# sample_rate * 2 and a 44-byte header. So size = seconds*rate*2 + 44, and the
# usable duration is (inline_budget - 44) / (rate * 2). If Streamlit ever
# changes channels or bit depth these tests fail loudly instead of us silently
# over-promising a recording length.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rate, expected_minutes",
    [
        (8000, 16.2),   # telephone quality — longest
        (16000, 8.1),   # widget default, recommended for speech
        (22050, 5.9),
        (44100, 2.9),   # browser default if sample_rate=None
        (48000, 2.7),
    ],
)
def test_max_recording_duration_per_sample_rate(rate, expected_minutes):
    budget_bytes = app.inline_raw_budget_mb() * 1024 * 1024
    bytes_per_second = rate * 2  # mono, 16-bit
    max_seconds = (budget_bytes - 44) / bytes_per_second
    assert max_seconds / 60 == pytest.approx(expected_minutes, abs=0.15)


def test_widget_default_rate_is_set_to_16k_not_browser_default():
    """A 44.1 kHz default would silently cut the limit from 8 min to 3 min."""
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "st.audio_input(" in src
    call = src[src.index("st.audio_input("):]
    call = call[: call.index(")") + 1]
    assert "sample_rate=16000" in call, call


def test_mono_16bit_assumption_holds_at_the_documented_boundary():
    """A recording at the quoted 8-minute limit must fit; just past it must not."""
    budget_bytes = app.inline_raw_budget_mb() * 1024 * 1024
    limit_seconds = (budget_bytes - 44) / 32000
    assert app.check_inline_budget(make_wav(limit_seconds * 0.99), "audio/wav")[0] is True
    assert app.check_inline_budget(make_wav(limit_seconds * 1.01), "audio/wav")[0] is False


# ---------------------------------------------------------------------------
# Compression / transcode — the unlock for long recordings.
#
# st.audio_input ALWAYS emits mono 16-bit PCM WAV, and the recorded UploadedFile
# is subject to server.maxUploadSize. Measured: a 30-min 16 kHz recording is
# 54.9 MB WAV, which needs 73.2 MB base64 and blows the 20 MB inline request cap
# 3.7x over. Transcoding to MP3 @32 kbps makes the SAME recording 6.9 MB
# (base64 9.2 MB) -> fits, and cuts peak server RAM from 220 MB to 34 MB.
# ---------------------------------------------------------------------------
def test_compression_constants_are_sane():
    assert app.COMPRESS_TARGET_KBPS == 64
    # Derived from the bitrate, so it must land near 32 min at 64 kbps.
    assert app.COMPRESS_MAX_SECONDS == pytest.approx(32 * 60, abs=60), app.COMPRESS_MAX_SECONDS


def test_duration_cap_never_exceeds_the_inline_budget():
    """The cap and the bitrate are NOT independent — guard the interaction.

    A 60-minute cap at 64 kbps produces 27.5 MB, which blows the ~15 MB inline
    budget: the user records for an hour and then gets rejected. Whatever the
    chosen bitrate, cap * bitrate must fit inside the budget.
    """
    budget_bytes = app.inline_raw_budget_mb() * 1024 * 1024
    worst_case = app.COMPRESS_MAX_SECONDS * app.COMPRESS_TARGET_KBPS * 1000 / 8
    assert worst_case <= budget_bytes, (worst_case, budget_bytes)


def test_worst_case_compressed_recording_fits_the_transport_cap():
    """A max-length recording must also survive maxUploadSize as raw WAV."""
    raw_wav_mb = app.len_bytes_for_wav(app.COMPRESS_MAX_SECONDS, 16000) / 1048576
    assert raw_wav_mb < app.configured_max_upload_mb(), raw_wav_mb


def test_sixty_minutes_at_64kbps_would_not_fit_so_the_cap_must_be_lower():
    """Documents the conflict that motivated deriving the cap."""
    sixty_min_bytes = 3600 * app.COMPRESS_TARGET_KBPS * 1000 / 8
    assert sixty_min_bytes > app.inline_raw_budget_mb() * 1024 * 1024
    assert app.COMPRESS_MAX_SECONDS < 3600


@pytest.mark.parametrize("kbps", [16, 24, 32, 64])
def test_compressed_duration_math_matches_bitrate(kbps):
    """Compressed bytes/sec = kbps*1000/8, so 60 min @32k = 14.4 MB (< 15 MB)."""
    max_seconds = (app.inline_raw_budget_mb() * 1024 * 1024) / (kbps * 1000 / 8)
    if kbps == 32:
        assert max_seconds / 60 == pytest.approx(66, abs=1.5)


def test_needs_compression_only_when_over_budget():
    small = make_wav(60.0)          # 1 min @16k = 1.9 MB -> fits
    big = make_wav(20 * 60.0)       # 20 min = 38 MB -> does not fit
    assert app.needs_compression(small) is False
    assert app.needs_compression(big) is True


def test_needs_compression_applies_to_oversized_compressed_uploads_too():
    """An oversized uploaded MP3 must have a path forward, not just be rejected.

    A 120 MB MP3 does not fit inline either, so routing it through the same
    transcode (at a lower bitrate) is what rescues it. Deciding purely on MIME
    would strand the user with 'upload a compressed file' when they already had.
    """
    tiny_mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 500
    huge_mp3 = b"\xff\xfb\x90\x00" + b"\x00" * (25 * 1024 * 1024)
    assert app.needs_compression(tiny_mp3, "audio/mpeg") is False
    assert app.needs_compression(huge_mp3, "audio/mpeg") is True


def test_max_upload_size_in_shipped_config_allows_a_full_hour_recording():
    """maxUploadSize governs st.audio_input too, so the SHIPPED config must clear
    60 minutes of WAV (~110 MB at 16 kHz).

    Asserts against .streamlit/config.toml rather than the stub, because that
    file is what actually deploys. A 50 MB cap would silently reject a
    full-length recording at transfer, before compression can ever run.
    """
    import tomllib

    cfg_path = os.path.join(ROOT, ".streamlit", "config.toml")
    assert os.path.exists(cfg_path), "shipped config.toml is missing"
    with open(cfg_path, "rb") as fh:
        cap_mb = tomllib.load(fh)["server"]["maxUploadSize"]

    sixty_min_wav_mb = app.len_bytes_for_wav(3600, 16000) / 1048576
    assert cap_mb > sixty_min_wav_mb, (cap_mb, sixty_min_wav_mb)
    # ...and small enough that the worst case fits the 1 GB Cloud ceiling.
    assert cap_mb <= 200, cap_mb


def test_config_value_is_read_as_a_real_number():
    """Guards the stub: a Mock here would make int(...) == 1 and any cap check
    silently pass/fail for the wrong reason."""
    assert isinstance(app.configured_max_upload_mb(), int)
    assert app.configured_max_upload_mb() > 0


def test_audio_duration_reads_wav_without_ffprobe():
    """WAV duration must come from the header, not a subprocess."""
    assert app.audio_duration_seconds(make_wav(3.0), "audio/wav") == pytest.approx(3.0, abs=0.05)


def test_audio_duration_measures_compressed_inputs():
    """Non-WAV containers have no parseable header here, so ffprobe is needed."""
    import shutil

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    mp3 = app.compress_audio(make_wav(4.0), "audio/wav")
    dur = app.audio_duration_seconds(mp3, "audio/mpeg")
    assert dur == pytest.approx(4.0, abs=0.35), dur


def test_compress_end_to_end_shrinks_and_stays_playable(tmp_path):
    """Real ffmpeg round-trip: output must be smaller and still valid MP3."""
    import shutil

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")

    src = make_wav(5.0)  # 5 s @16 kHz
    mp3 = app.compress_audio(src, "audio/wav", target_kbps=32)

    assert len(mp3) < len(src)
    assert mp3[:3] == b"ID3" or mp3[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"), mp3[:4]
    assert len(mp3) > 1000  # not a truncated stub


def test_compress_rejects_over_policy_cap():
    """A 61-minute recording exceeds the explicit policy cap and must be refused."""
    too_long = make_wav(61 * 60.0)
    with pytest.raises(ValueError, match="exceeds"):
        app.compress_audio(too_long, "audio/wav")


# ---------------------------------------------------------------------------
# Default source must keep the pre-existing flow unaffected
# ---------------------------------------------------------------------------
def test_radio_offers_upload_first_so_it_is_the_default():
    """The existing upload flow must remain the landing state.

    st.radio defaults to the first option, so ordering is the behaviour: with
    "Record live" first, every existing user would land on a mic permission
    prompt instead of the file picker they had before.
    """
    import re

    with open(os.path.join(ROOT, "app.py")) as fh:
        src = fh.read()
    m = re.search(
        r"st\.radio\(\s*\"Audio source:\",\s*\[(.+?)\]", src, re.DOTALL
    )
    assert m, "audio source radio not found"
    options = [o.strip().strip('"') for o in m.group(1).split(",")]
    assert options[0] == "📁 Upload file", options
    assert options[1] == "🎙️ Record live", options
