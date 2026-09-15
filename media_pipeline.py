"""Multi-file audio ingestion helpers (Enhancement 1).

Pure logic, deliberately free of Streamlit so it is unit-testable: ordering and
batch-size validation.

Audio is sent RAW to the model — this module no longer transcodes it. The v0.2
ffmpeg stage was removed in v0.5 because it degraded recognition for no benefit:
it collapsed stereo to mono and capped the signal at 16 kHz, discarding the
>8 kHz band where sibilants live. `compress_audio` is retained below as an
unused utility (and contract of record for the removed behaviour); the send path
does not call it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

MB = 1024 * 1024

# Browser-reported MIME -> type the API accepts.
#
# Load-bearing now that audio is sent raw: the browser's string goes straight
# into the request. Safari reports `audio/x-m4a` for a .m4a (not an accepted
# type), and `audio/x-wav` / `audio/x-mpeg` variants are common across OSes.
# Before v0.5 the transcoder normalised everything to audio/mp3, which masked
# this entirely.
_MIME_ALIASES = {
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/mp4a-latm": "audio/mp4",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/x-mpeg": "audio/mp3",
    "audio/mpeg": "audio/mp3",
    "audio/mpeg3": "audio/mp3",
    "audio/x-mp3": "audio/mp3",
    "audio/x-aac": "audio/aac",
    "audio/x-ogg": "audio/ogg",
    "audio/vorbis": "audio/ogg",
    "audio/x-flac": "audio/flac",
    "video/x-m4v": "video/mp4",
    "video/quicktime": "video/mp4",
}

_EXT_MIME = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
}


def normalize_mime(mime: str, filename: str = "") -> str:
    """Map a browser-reported MIME to one the API accepts.

    Falls back to the file extension, then to audio/mp3, so an empty or
    unknown `FileUpload.type` still produces a usable request rather than an
    unsupported-media rejection.
    """
    m = (mime or "").split(";")[0].strip().lower()
    if m in _MIME_ALIASES:
        return _MIME_ALIASES[m]
    if m.startswith("audio/") or m.startswith("video/"):
        return m
    ext = os.path.splitext(filename or "")[1].lower()
    return _EXT_MIME.get(ext, "audio/mp3")

# Assumed Gemini inline ceiling for a single request. Audio is sent raw now, so
# this — not the compressor — is the binding limit on batch size.
INLINE_MAX_REQUEST_MB = 2048.0
PROMPT_HEADROOM_MB = 5.0
INLINE_RAW_BUDGET_MB = INLINE_MAX_REQUEST_MB - PROMPT_HEADROOM_MB  # 2043.0

# Whole-batch ceiling, tracking the assumed API limit above.
#
# NOTE: the API is no longer the tightest constraint — the container is. Raw
# bytes are base64'd into the JSON body (~1.37x), and Streamlit Community Cloud
# gives ~1 GB of RAM. A batch well below 2 GB can therefore still OOM the
# process. Raise/keep this in step with the hosting plan, not just the API.
MAX_TOTAL_MB = 2048.0

# Retained only for the unused `compress_audio` utility below.
COMPRESS_TARGET_KBPS = 32


@dataclass(frozen=True)
class Part:
    """One uploaded audio file.

    `key` must be STABLE across reruns and distinct from `name` — the ordering
    UI edits order *values*, which never moves rows, so row position is not an
    identity. `name` is display-only and may repeat.
    """

    key: str
    name: str
    size: int


def natural_key(name: str) -> list:
    """Sort key that reads embedded integers numerically ('p2' < 'p10')."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def order_parts(parts: list[Part], order: dict[str, int] | None) -> list[Part]:
    """Return `parts` in play order.

    With no explicit order, fall back to natural filename order. With an
    explicit order, pair by KEY — never by row position, which would silently
    attach the wrong audio to the wrong slot.
    """
    if not order:
        return sorted(parts, key=lambda p: natural_key(p.name))
    known = [(order[p.key], i, p) for i, p in enumerate(parts) if p.key in order]
    unknown = [p for p in parts if p.key not in order]
    known.sort(key=lambda t: (t[0], t[1]))
    return [p for _, _, p in known] + unknown


def total_bytes(parts: list[Part]) -> int:
    return sum(p.size for p in parts)


def format_size(num_bytes: int) -> str:
    return f"{num_bytes / MB:.1f} MB"


def validate_batch(sizes: list[int], max_total_mb: float = MAX_TOTAL_MB) -> tuple[bool, str]:
    """Refuse an over-budget batch BEFORE the upload is consumed.

    Without this the app uploads, base64s and POSTs everything first, then dies
    on a 413 or a 360 s timeout — a multi-minute wait ending in a cryptic error.
    """
    total = sum(sizes)
    if total > max_total_mb * MB:
        return False, (
            f"Batch is {format_size(total)}, over the {max_total_mb:.0f} MB limit. "
            f"Split it into smaller parts and retry."
        )
    return True, ""


def derive_max_seconds(kbps: int, budget_mb: float = INLINE_RAW_BUDGET_MB) -> int:
    """Longest audio that still encodes into `budget_mb` at `kbps`.

    Derived rather than hardcoded so bitrate and duration can never drift into
    contradicting each other after a later edit.
    """
    return int(budget_mb * MB / (kbps * 1000 / 8))


MAX_COMPRESS_SECONDS = derive_max_seconds(COMPRESS_TARGET_KBPS)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def compress_audio(raw: bytes, suffix: str, kbps: int = COMPRESS_TARGET_KBPS) -> bytes:
    """Transcode `raw` to mono MP3 at `kbps`, through pipes.

    NOT on the send path as of v0.5 — the app sends raw uploads. Kept because it
    is a working, tested utility and the contract of record for the removed
    behaviour (mono / 16 kHz / 32 kbps). Do not re-wire it into
    `analyze_meeting_audio_rest` without re-reading the why-removed note at the
    top of this module.

    Piped so the source bytes are never copied again in Python. On any failure
    the original bytes are returned — a graceful degrade to "sends but may be
    large" rather than a hard crash on a container lacking ffmpeg.
    """
    if not ffmpeg_available():
        return raw

    src_fmt = (suffix or "").lstrip(".").lower() or "wav"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", src_fmt,
        "-i", "pipe:0",
        "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", f"{kbps}k",
        "-f", "mp3",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, input=raw, capture_output=True, timeout=900)
    except (subprocess.SubprocessError, OSError):
        return raw
    if proc.returncode != 0 or not proc.stdout:
        return raw
    return proc.stdout


def estimate_peak_mb(raw_mb: float, compression_ratio: float = 0.1) -> float:
    """Rough peak RAM for a batch: raw bytes stay resident, encoded parts are
    held as bytes + base64 + JSON."""
    compressed = raw_mb * compression_ratio
    return raw_mb + compressed * 3
