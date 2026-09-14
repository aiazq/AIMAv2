"""How long is the recording?

The transcript's 2-field offsets cannot be interpreted without knowing the
scale, and the scale is decided from the recording's duration (see `timeline`).
This probe feeds that decision.

It is best-effort by design: a container without ffprobe, or a file whose header
is unreadable, must degrade to "unknown duration" — never crash the run and
never report a made-up length, because a wrong duration would pick the wrong
offset scale and shift every entry in the minutes by an hour.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import timeline  # noqa: E402

FIXTURE = os.path.join(ROOT, "private_fixtures", "meeting_clip_1min.ogg")


def test_a_real_recording_reports_its_length():
    """The fixture is a 1-minute clip; ffprobe says 61.488 s."""
    if not timeline.ffprobe_available() or not os.path.exists(FIXTURE):
        pytest.skip("ffprobe or fixture unavailable")
    with open(FIXTURE, "rb") as f:
        raw = f.read()
    secs = timeline.probe_duration(raw, ".ogg")
    assert secs is not None
    assert 60.0 <= secs <= 62.5


def test_the_probe_agrees_with_the_scale_the_fixture_implies():
    """End-to-end wiring: the real clip's duration forces MM:SS, which is what
    makes its 00:59 a 59 s offset rather than a 59 min one."""
    if not timeline.ffprobe_available() or not os.path.exists(FIXTURE):
        pytest.skip("ffprobe or fixture unavailable")
    with open(FIXTURE, "rb") as f:
        raw = f.read()
    secs = timeline.probe_duration(raw, ".ogg")
    stamps = ["00:00", "00:21", "00:46", "00:55", "00:59"]
    assert timeline.resolve_scale(stamps, duration_seconds=secs) == timeline.MMSS


@pytest.mark.parametrize("raw,suffix", [(b"", ".wav"), (b"not audio at all", ".mp3"), (b"RIFFxxxx", ".wav")])
def test_unreadable_input_yields_no_duration_rather_than_a_guess(raw, suffix):
    assert timeline.probe_duration(raw, suffix) is None


def test_a_missing_suffix_still_probes():
    """The uploader's `type` may be empty; a temp file with no extension must
    not be a hard failure."""
    if not timeline.ffprobe_available() or not os.path.exists(FIXTURE):
        pytest.skip("ffprobe or fixture unavailable")
    with open(FIXTURE, "rb") as f:
        raw = f.read()
    assert timeline.probe_duration(raw, "") is not None


def test_ffprobe_availability_is_a_bool():
    assert isinstance(timeline.ffprobe_available(), bool)


def test_no_temp_files_are_left_behind():
    """The probe writes the upload to a temp file; leaking one per run would
    fill the container's disk across a session."""
    import glob
    import tempfile as _tf

    if not timeline.ffprobe_available() or not os.path.exists(FIXTURE):
        pytest.skip("ffprobe or fixture unavailable")
    with open(FIXTURE, "rb") as f:
        raw = f.read()
    before = set(glob.glob(os.path.join(_tf.gettempdir(), "*")))
    timeline.probe_duration(raw, ".ogg")
    after = set(glob.glob(os.path.join(_tf.gettempdir(), "*")))
    assert after - before == set(), f"leaked: {after - before}"
