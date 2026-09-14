"""Multi-file audio ingestion helpers (Enhancement 1).

Pure logic, deliberately free of Streamlit so it is unit-testable: ordering,
batch-size validation, and the ffmpeg transcode that keeps a multi-part run
inside the container's memory budget.

Why compression is not optional here: the Gemini path base64-inlines each part
into the JSON body, so peak RAM is ~3.7x raw bytes. Three 90 MB parts is
270 MB resident -> ~988 MB peak, against Community Cloud's 1 GB ceiling. Mono
32 kbps speech drops that to ~70 MB total with no accuracy loss in practice,
because Gemini downsamples to 16 kbps and collapses to mono anyway.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

MB = 1024 * 1024

# Documented Gemini inline ceiling: 20 MB total request including the prompt.
# We leave headroom for the prompt text and the response schema.
INLINE_MAX_REQUEST_MB = 20.0
PROMPT_HEADROOM_MB = 5.0
INLINE_RAW_BUDGET_MB = INLINE_MAX_REQUEST_MB - PROMPT_HEADROOM_MB  # 15.0

# Whole-batch ceiling. Below the 1 GB container wall with room to spare.
MAX_TOTAL_MB = 260.0

# Gemini downsamples to 16 kbps mono regardless, so a higher bitrate buys
# nothing but bytes. Keep this in step with the duration derived below.
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
