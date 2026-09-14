#!/usr/bin/env bash
# Falsification battery for the transcript clock-time fix.
#
# A test that has never been seen to fail is indistinguishable from no test.
# Each case re-introduces one bug from the fix, runs the module that should
# catch it, and restores the tree via an EXIT trap so an interrupt cannot leave
# the repo broken.
#
# CRITICAL: every mutation asserts that its string actually matched the source.
# A silent no-op patch means the case runs against UNMUTATED code, so it reports
# "NOT CAUGHT" (or worse, a false pass) while testing nothing. An earlier
# version of this script failed exactly that way.
set -uo pipefail
cd "$(dirname "$0")/.."

APP=app.py
TL=timeline.py
cp "$APP" /tmp/falsify_app.bak
cp "$TL" /tmp/falsify_tl.bak
trap 'cp /tmp/falsify_app.bak "'"$APP"'"; cp /tmp/falsify_tl.bak "'"$TL"'"; echo "[restored]"' EXIT

RED=$'\033[31m'; GRN=$'\033[32m'; RST=$'\033[0m'; YEL=$'\033[33m'
declare -a TABLE
caught=0; notcaught=0; broken=0

# mut <file> <old> <new> <human-name>
# Rewrites `old` -> `new`, aborting the case loudly if `old` is absent.
mut () {
  ./.venv/bin/python - "$1" "$2" "$3" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(path).read()
if old not in src:
    print(f"PATCH-MISS in {path}: {old[:70]!r}")
    sys.exit(9)
open(path, "w").write(src.replace(old, new, 1))
PY
}

case_run () {
  local name="$1" modules="$2"
  if ./.venv/bin/python -m pytest $modules -q >/tmp/falsify_out.txt 2>&1; then
    TABLE+=("${RED}NOT CAUGHT${RST}  $name")
    notcaught=$((notcaught+1))
  else
    TABLE+=("${GRN}caught${RST}      $name")
    caught=$((caught+1))
  fi
  cp /tmp/falsify_app.bak "$APP"; cp /tmp/falsify_tl.bak "$TL"
}

# Each case: (1) mutate, verifying the patch landed; (2) run the tests.
do_case () {
  local name="$1" file="$2" old="$3" new="$4" modules="$5"
  if ! mut "$file" "$old" "$new"; then
    TABLE+=("${YEL}HARNESS BROKEN${RST}  $name  (mutation did not apply)")
    broken=$((broken+1))
    cp /tmp/falsify_app.bak "$APP"; cp /tmp/falsify_tl.bak "$TL"
    return
  fi
  case_run "$name" "$modules"
}

# --- F1: the original bug — never anchor the transcript --------------------
do_case "F1 transcript never anchored (the reported bug)" "$APP" \
"        _anchor_transcript(updated, new_time, duration_seconds)
" "" \
"tests/test_transcript_clock_times.py"

# --- F2: no idempotency — re-anchor from the written value -----------------
do_case "F2 re-anchor drifts (offset added twice)" "$APP" \
'        original = getattr(entry, "_source_timestamp", None)
        if original is None:
            original = entry.timestamp
' '        original = entry.timestamp
        if False:
            pass
' \
"tests/test_transcript_clock_times.py"

# --- F3: always MM:SS — ignore the duration signal ------------------------
do_case "F3 long meeting read as MM:SS" "$TL" \
"        if (
            longest_hhmm is not None
            and longest_mmss is not None
            and longest_mmss < _COVERAGE_THRESHOLD * duration_seconds
        ):
            # MM:SS would account for only a sliver of the recording; this is a
            # transcript of the whole meeting, so the HH:MM reading is the fit.
            return HHMM
" "" \
"tests/test_transcript_timeline.py tests/test_transcript_clock_times.py"

# --- F4: drop the leading-field guard (4-space indent — see header) -------
do_case "F4 '75:10' treated as MM:SS" "$TL" \
"    if max(leading) > _MMSS_MAX_MINUTES:
        return HHMM
" "" \
"tests/test_transcript_timeline.py"

# --- F4b: the weaker half of the guard — allow leading == 60 --------------
do_case "F4b boundary: leading field of exactly 60 accepted as MM:SS" "$TL" \
"    if max(leading) > _MMSS_MAX_MINUTES:" \
"    if max(leading) > 100000:" \
"tests/test_transcript_timeline.py"

# --- F5: invent a time for unreadable entries -----------------------------
do_case "F5 unreadable offset stamped with a fake time" "$TL" \
"        out.append(format_clock_time(start, secs) if secs is not None else s)" \
"        out.append(format_clock_time(start, secs) if secs is not None else format_clock_time(start, 0))" \
"tests/test_transcript_timeline.py tests/test_transcript_clock_times.py"

# --- F6: default a blank start time to 09:00 ------------------------------
do_case "F6 blank start time silently defaults to 09:00" "$APP" \
"    if new_time is not None:
        updated.meeting_time = format_report_time(new_time)
        _anchor_transcript(updated, new_time, duration_seconds)" \
"    if new_time is None:
        new_time = datetime.time(9, 0)
    updated.meeting_time = format_report_time(new_time)
    _anchor_transcript(updated, new_time, duration_seconds)" \
"tests/test_transcript_clock_times.py"

# --- F7: probe leaks its temp file ---------------------------------------
do_case "F7 duration probe leaks temp files" "$TL" \
"    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
" "" \
"tests/test_audio_duration.py"

# --- F8: total_duration counts unmeasured parts as zero -------------------
do_case "F8 unmeasured part counted as 0 s" "$TL" \
"    known = [p for p in (parts or []) if p]" \
"    known = [p or 0.0 for p in (parts or [])]" \
"tests/test_transcript_timeline.py"

# --- F9: cross-module wiring — the scale is ignored end to end ------------
do_case "F9 duration never reaches the transcript scale decision" "$APP" \
"                        duration_seconds=st.session_state.get(\"audio_duration_seconds\")," \
"                        duration_seconds=None," \
"tests/test_transcript_clock_times.py tests/test_timeline_wiring.py"

# --- F10: remove the duration probe call entirely (wire silently dead) ----
do_case "F10 probe_duration call removed from the run flow" "$APP" \
"                    timeline.probe_duration(
                        bytes_by_key[p.key],
                        os.path.splitext(p.name)[1],
                    )" \
"                    None" \
"tests/test_timeline_wiring.py"

# --- F11: a second, independent time source appears in app.py -------------
do_case "F11 second clock-time source injected (drift risk)" "$APP" \
"import timeline" \
"import timeline


def _shadow_time(start, off):
    return timeline.format_clock_time(start, off)" \
"tests/test_timeline_wiring.py"

# --- F12: the shared implementation is forked ------------------------------
do_case "F12 a second offset->clock implementation appears" "$TL" \
"def format_clock_time(start: datetime.time, offset_seconds: int) -> str:" \
"def format_clock_time_dup(start: datetime.time, offset_seconds: int) -> str:" \
"tests/test_timeline_wiring.py tests/test_transcript_timeline.py"

# --- F13: a fresh run never anchors (the panel becomes a prerequisite) ----
do_case "F13 fresh run never anchors the transcript" "$APP" \
"            result = auto_anchor_transcript(
                result, duration_seconds=st.session_state.get(\"audio_duration_seconds\")
            )" \
"            pass" \
"tests/test_transcript_clock_times.py tests/test_timeline_wiring.py"

# --- F14: auto_anchor mutates the caller's report -------------------------
do_case "F14 auto_anchor mutates its input report" "$APP" \
"    updated = report.model_copy(deep=True)
    _anchor_transcript(updated, start, duration_seconds)
    return updated" \
"    _anchor_transcript(report, start, duration_seconds)
    return report" \
"tests/test_transcript_clock_times.py"

# --- F15: auto_anchor invents a start time when there is none -------------
do_case "F15 auto_anchor invents a start time when there is none" "$APP" \
"    start = parse_report_time(getattr(report, \"meeting_time\", None))
    if start is None:
        return report" \
"    start = parse_report_time(getattr(report, \"meeting_time\", None)) or datetime.time(0, 0)" \
"tests/test_transcript_clock_times.py"

echo
echo "==================== FALSIFICATION TABLE ===================="
for row in "${TABLE[@]}"; do echo "  $row"; done
echo "============================================================"
echo "  caught: $caught    NOT CAUGHT: $notcaught    HARNESS BROKEN: $broken"
echo
[ "$notcaught" -eq 0 ] && [ "$broken" -eq 0 ] || exit 1
