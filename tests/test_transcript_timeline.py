"""Transcript offsets -> clock times.

The model returns ELAPSED OFFSETS from the start of the recording, not times of
day: a one-minute clip yields `00:00, 00:21, 00:46, 00:55`. The app rendered
`entry.timestamp` verbatim (screen, DOCX, JSON export), so the minutes showed
`00:00` at every entry however the Date & Start Time panel was set.

Anchoring `clock = start_time + offset` fixes all consumers at once, because
they all read the same field.

The awkward part is the 2-field form. `01:23` is 1 min 23 s in a short clip and
1 h 23 min in a long meeting, and the schema's description ("approximate
timestamp, e.g. '01:23'") does not say which. `02:15` fits *both* readings
inside a 2.5 h recording, so a single entry can never be disambiguated on its
own — the scale is a property of the whole transcript, decided from two
independent signals: a leading field above 59 (impossible as MM:SS), and how
well each reading's final offset covers the recording's known duration. A wrong
scale shifts every entry by an hour, so where neither signal decides, the
fallback is the documented convention (MM:SS) and never a silent guess.
"""
import datetime
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import timeline  # noqa: E402


# ---------------------------------------------------------------------------
# parse_offset — the unambiguous forms
# ---------------------------------------------------------------------------
def test_three_fields_are_hours_minutes_seconds():
    """`01:23:45` means 1 h 23 min 45 s under any scale — 5025 s."""
    assert timeline.parse_offset("01:23:45") == 5025


def test_a_plain_seconds_count_is_accepted():
    assert timeline.parse_offset("45") == 45


def test_surrounding_whitespace_and_brackets_are_tolerated():
    assert timeline.parse_offset("  [00:21]  ") == 21


def test_two_fields_under_the_short_scale_are_minutes_and_seconds():
    assert timeline.parse_offset("00:59", scale=timeline.MMSS) == 59


def test_two_fields_under_the_long_scale_are_hours_and_minutes():
    assert timeline.parse_offset("02:15", scale=timeline.HHMM) == 8100


# ---------------------------------------------------------------------------
# Unreadable input is left alone, never invented
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["", "   ", None, "nan", "<NA>", "N/A", "abc", "--", "?"])
def test_unreadable_offsets_return_none(raw):
    assert timeline.parse_offset(raw) is None


@pytest.mark.parametrize("raw", ["00:xx", "1:2:3:4", "::", "00:", "00:75"])
def test_malformed_offsets_return_none(raw):
    assert timeline.parse_offset(raw) is None


# ---------------------------------------------------------------------------
# resolve_scale — the transcript-level decision
# ---------------------------------------------------------------------------
def test_a_one_minute_clip_can_only_be_minutes_and_seconds():
    """The fixture's real shape. `00:59` as HH:MM would run 59 min inside a
    60 s recording, so MM:SS is the only consistent reading."""
    stamps = ["00:00", "00:21", "00:46", "00:55", "00:59"]
    assert timeline.resolve_scale(stamps, duration_seconds=60) == timeline.MMSS


def test_a_long_meeting_reads_two_fields_as_hours_and_minutes():
    """A 2.5 h recording whose last offset is `02:15`. Both readings fit, so
    duration alone cannot decide — but MM:SS would mean the transcript covered
    the first 135 s of a 2.5 h meeting, which is not a transcript of it."""
    stamps = ["00:05", "01:10", "02:15"]
    assert timeline.resolve_scale(stamps, duration_seconds=9000) == timeline.HHMM


def test_a_leading_field_over_59_still_reads_as_minutes_and_seconds():
    """`75:10` is a normal MM:SS offset in a 75-minute recording.

    An earlier version treated a leading field above 59 as proof of HH:MM, on
    the reasoning that MM:SS "caps at 59 minutes". It does not: the MINUTE count
    is what grows past 59 in a long recording, so `75:10` is 75 min 10 s. The
    old rule read it as 75 HOURS, which is the class of bug this pins.
    """
    assert timeline.resolve_scale(["00:10", "75:10"]) == timeline.MMSS


def test_a_minutes_seconds_offset_past_the_hour_uses_the_duration():
    """A 90-minute meeting written `90:00`, with the real duration measured.

    MM:SS accounts for 90 min of a 90-min recording; HH:MM would claim 90 hours.
    Only the duration-anchored reading is consistent.
    """
    stamps = ["00:00", "20:00", "45:00", "70:00", "90:00"]
    assert timeline.resolve_scale(stamps, duration_seconds=5400) == timeline.MMSS


def test_an_hh_mm_offset_that_overruns_the_recording_is_rejected():
    """`01:30` in a 60-min recording is not 1.5 hours of a 1-hour meeting."""
    stamps = ["00:00", "00:20", "00:45", "01:30"]
    assert timeline.resolve_scale(stamps, duration_seconds=3600) == timeline.MMSS


def test_rounding_slack_keeps_the_hh_mm_reading_for_a_rounded_final_stamp():
    """The model rounds to the minute; ffprobe measures the container exactly.

    A 60-minute meeting's last entry reads `01:00` while ffprobe reports
    3599.4 s. Without slack the HH:MM reading "overran" by 0.6 s, was rejected,
    and the whole transcript collapsed into its first minute - the reported bug.
    """
    stamps = ["00:00", "00:12", "00:35", "01:00"]
    assert timeline.resolve_scale(stamps, duration_seconds=3599.4) == timeline.HHMM
    assert timeline.anchor(
        stamps, datetime.time(10, 0), duration_seconds=3599.4
    )[-1] == "11:00:00"


def test_a_small_reading_never_wins_over_a_fitting_larger_one():
    """Both readings consistent: the one spanning more of the recording wins.

    A 2 h meeting written `01:55` is 1 h 55 min, not 1 min 55 s - the latter is
    consistent but describes a transcript of the recording's opening seconds.
    """
    stamps = ["00:05", "01:10", "01:55"]
    assert timeline.resolve_scale(stamps, duration_seconds=7200) == timeline.HHMM


def test_without_a_duration_the_documented_fallback_is_minutes_and_seconds():
    assert timeline.resolve_scale(["00:05", "00:21"]) == timeline.MMSS


def test_an_empty_or_unreadable_transcript_still_yields_a_scale():
    assert timeline.resolve_scale([]) == timeline.MMSS
    assert timeline.resolve_scale(["", "n/a"]) == timeline.MMSS


# ---------------------------------------------------------------------------
# Offsets -> clock time
# ---------------------------------------------------------------------------
def test_offset_is_added_to_the_start_time():
    assert timeline.format_clock_time(datetime.time(10, 0), 21) == "10:00:21"


def test_the_clock_rolls_over_the_hour():
    assert timeline.format_clock_time(datetime.time(10, 59), 61) == "11:00:01"


def test_the_clock_rolls_over_midnight():
    assert timeline.format_clock_time(datetime.time(23, 59), 120) == "00:01:00"


def test_the_rendered_form_is_unmistakably_a_time_of_day():
    """HH:MM:SS, so `09:05:00` can never be misread as another MM:SS offset."""
    assert timeline.format_clock_time(datetime.time(9, 5), 0) == "09:05:00"


# ---------------------------------------------------------------------------
# anchor — the whole transcript at once
# ---------------------------------------------------------------------------
def test_every_entry_is_anchored_to_the_start_time():
    """The fixture, anchored at 10:00."""
    anchored = timeline.anchor(
        ["00:00", "00:21", "00:46", "00:55", "00:59"],
        datetime.time(10, 0),
        duration_seconds=60,
    )
    assert anchored == ["10:00:00", "10:00:21", "10:00:46", "10:00:55", "10:00:59"]


def test_an_unreadable_entry_is_left_exactly_as_it_was():
    """Stamping a fabricated time onto an entry we could not read would be
    worse than showing the raw value the model produced."""
    anchored = timeline.anchor(["00:21", "", "n/a"], datetime.time(10, 0), duration_seconds=60)
    assert anchored == ["10:00:21", "", "n/a"]


def test_moving_the_start_time_moves_every_entry():
    stamps = ["00:00", "00:21"]
    assert timeline.anchor(stamps, datetime.time(9, 0), duration_seconds=60) == [
        "09:00:00", "09:00:21",
    ]
    assert timeline.anchor(stamps, datetime.time(11, 30), duration_seconds=60) == [
        "11:30:00", "11:30:21",
    ]


# ---------------------------------------------------------------------------
# total_duration — a multi-part meeting is the SUM of its parts
# ---------------------------------------------------------------------------
def test_a_single_part_reports_its_own_length():
    assert timeline.total_duration([60.0]) == 60.0


def test_multi_part_meetings_are_summed():
    """Parts are sequential recordings of ONE meeting, so the offsets span the
    whole thing — the scale decision needs the combined length."""
    assert timeline.total_duration([3600.0, 1800.0, 600.0]) == 6000.0


def test_unknown_parts_are_skipped_rather_than_zeroed():
    """A part we could not measure must not make the total look shorter than it
    is: too short a total could pick MM:SS for a long meeting."""
    assert timeline.total_duration([3600.0, None, 600.0]) == 4200.0


def test_no_measurements_at_all_means_unknown():
    assert timeline.total_duration([None, None]) is None
    assert timeline.total_duration([]) is None
