"""Transcript offsets -> clock times.

Pure logic, Streamlit-free (same convention as `media_pipeline`), so the
arithmetic is unit-testable without a runtime.

The model returns ELAPSED OFFSETS from the start of the recording, not times of
day: a one-minute clip yields `00:00, 00:21, 00:46, 00:55`. The app rendered
`entry.timestamp` verbatim into the screen, the DOCX and the JSON export, so
every entry read `00:00` however the meeting actually began. Anchoring
`clock = start_time + offset` fixes all consumers at once because they all read
the same field.
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import tempfile

# `00:21`, `01:23:45`, `[00:21]`, `1:2:3`
_OFFSET_RE = re.compile(r"^\[?\s*(\d{1,3})(?::(\d{1,2}))?(?::(\d{1,2}))?\s*\]?$")

_EMPTY_TOKENS = {"", "-", "--", "---", "?", "n/a", "na", "none", "null", "nil", "nan", "<na>"}

# The two readings of a 2-field offset.
MMSS = "mmss"  # `01:23` -> 1 min 23 s
HHMM = "hhmm"  # `01:23` -> 1 h 23 min

# How far past the measured length a reading may reach before it is rejected.
#
# The model rounds its timestamps to the minute while ffprobe measures the
# container exactly, so the last entry of a 60-minute meeting can sit a fraction
# of a second past a measured 3599.4 s. Without slack that rounding artefact
# rejected the HH:MM reading and collapsed the whole transcript into its first
# minute. Sized to absorb rounding, not to admit a genuinely over-long reading —
# the wrong scale is wrong by a factor of 60.
_ROUNDING_SLACK_SECONDS = 120.0


def _offset_numbers(raw) -> list[int] | None:
    """The integer fields of a timestamp, or `None` when it cannot be read.

    Shared by `parse_offset` and the transcript-level scale decision, so both
    agree on what counts as readable: a value one of them rejects must never be
    silently accepted by the other.
    """
    if raw is None:
        return None

    text = str(raw).strip()
    if text.lower() in _EMPTY_TOKENS:
        return None

    m = _OFFSET_RE.match(text)
    if not m:
        return None

    parts = [g for g in m.groups() if g is not None]
    if not parts:
        return None

    try:
        return [int(p) for p in parts]
    except ValueError:
        return None


def parse_offset(raw, scale: str = MMSS) -> int | None:
    """Seconds elapsed, or `None` when the value cannot be read.

    `scale` applies only to the ambiguous 2-field form; `01:23:45` is
    unambiguous and `45` is a plain second count under either scale.
    """
    nums = _offset_numbers(raw)
    if nums is None:
        return None

    if len(nums) == 1:
        return nums[0]

    if len(nums) == 2:
        a, b = nums
        if b > 59:
            # `00:75` is not a valid MM:SS or HH:MM pair.
            return None
        if scale == HHMM:
            return a * 3600 + b * 60
        return a * 60 + b

    if len(nums) == 3:
        h, mm, ss = nums
        if mm > 59 or ss > 59:
            return None
        return h * 3600 + mm * 60 + ss

    return None


def _longest_offset(values, scale: str) -> int | None:
    """The largest readable offset under `scale`, or `None` if none read."""
    seen = [v for v in (parse_offset(s, scale=scale) for s in values) if v is not None]
    return max(seen) if seen else None


def _has_two_field(values) -> bool:
    """True when at least one stamp uses the ambiguous 2-field form."""
    return any(len(_offset_numbers(s) or []) == 2 for s in values)


def resolve_scale(stamps, duration_seconds: float | None = None) -> str:
    """Decide how to read 2-field offsets across a whole transcript.

    A single entry cannot decide: `02:15` fits inside a 2.5 h recording read
    either way, so the scale is a property of the whole transcript. It is chosen
    by asking which reading is CONSISTENT with the recording's measured length:

    1. A reading whose longest offset runs past the end of the recording cannot
       be right — a speaker cannot talk after the audio stopped.
    2. If both readings fit, the one that accounts for more of the recording
       wins: a transcript of a meeting spans a meaningful part of it.
    3. If neither fits, the measurement and the offsets disagree (an unmeasurable
       part in a multi-file upload, say), so MM:SS is used — it overshoots by
       less, being the smaller of the two inflations.

    With no usable duration the fallback is MM:SS, the convention for the
    sub-hour recordings this app targets. That is a *default*, not a certainty,
    and the earlier attempt to sharpen it was unsound: a leading field above 59
    is NOT evidence for HH:MM. `75:10` is a normal minutes:seconds offset in a
    75-minute recording, whereas reading it as hours:minutes claims a 75-hour
    meeting. Only a fit against a measured duration can raise HH:MM above the
    default.
    """
    values = list(stamps or [])

    if not _has_two_field(values):
        # Nothing hinges on the scale: a 3-field offset reads the same either
        # way, and an empty transcript has nothing to read.
        return MMSS

    mmss_max = _longest_offset(values, MMSS)
    hhmm_max = _longest_offset(values, HHMM)

    if duration_seconds and mmss_max is not None and hhmm_max is not None:
        ceiling = duration_seconds + _ROUNDING_SLACK_SECONDS
        fits_mmss = mmss_max <= ceiling
        fits_hhmm = hhmm_max <= ceiling

        if fits_mmss and not fits_hhmm:
            return MMSS
        if fits_hhmm and not fits_mmss:
            return HHMM
        if fits_mmss and fits_hhmm:
            # Both consistent: the reading that spans more of the recording is
            # the one that actually describes this meeting.
            return HHMM if hhmm_max > mmss_max else MMSS
        # Neither fits — the measurement and the offsets disagree, so fall
        # through to the documented default.

    return MMSS


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def probe_duration(raw: bytes, suffix: str = "") -> float | None:
    """Length of `raw` in seconds, or `None` when it cannot be determined.

    ffprobe is given a real temp file rather than a pipe: piping an Ogg stream
    fails with "Invalid data found when processing input" because ffprobe cannot
    seek to read the container's duration. The temp file is removed in a
    `finally`, so a session cannot leak one per run.

    Best-effort by contract — a missing ffprobe or an unreadable header degrades
    to `None`. Returning a made-up duration would be worse than returning none,
    because the duration decides the offset scale and a wrong scale shifts every
    timestamp in the minutes by an hour.
    """
    if not raw or not ffprobe_available():
        return None

    ext = ("." + suffix.lstrip(".")) if suffix and suffix.strip() else ""
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        proc = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return None
        value = (proc.stdout or "").strip()
        if not value or value.upper() == "N/A":
            return None
        secs = float(value)
        return secs if secs > 0 else None
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def total_duration(parts: list[float | None]) -> float | None:
    """Combined length of a multi-part recording, or `None` if nothing is known.

    Sequential parts of one meeting are played back to back, so the transcript's
    offsets span their SUM — the scale decision is worthless if it reasons about
    a single part's length.

    Unmeasured parts are skipped rather than counted as zero: a total that looks
    shorter than the real recording could pick the MM:SS reading for a long
    meeting, which is the one mistake that shifts every entry by an hour.
    """
    known = [p for p in (parts or []) if p]
    if not known:
        return None
    return float(sum(known))


def format_clock_time(start: datetime.time, offset_seconds: int) -> str:
    """`start` shifted by `offset_seconds`, rendered `HH:MM:SS`.

    Seconds are always shown so the result is unmistakably a time of day rather
    than another `MM:SS` offset.
    """
    base = datetime.datetime.combine(datetime.date(2000, 1, 1), start)
    shifted = base + datetime.timedelta(seconds=int(offset_seconds))
    return shifted.strftime("%H:%M:%S")


def anchor(
    stamps,
    start: datetime.time,
    duration_seconds: float | None = None,
    scale: str | None = None,
) -> list[str]:
    """Shift every readable offset in `stamps` onto the `start` clock.

    Unreadable values are passed through byte-for-byte: stamping a fabricated
    time onto an entry we could not parse would hide the failure, and showing
    the model's raw value is the honest degradation.
    """
    values = list(stamps or [])
    chosen = scale or resolve_scale(values, duration_seconds=duration_seconds)
    out = []
    for s in values:
        secs = parse_offset(s, scale=chosen)
        out.append(format_clock_time(start, secs) if secs is not None else s)
    return out
