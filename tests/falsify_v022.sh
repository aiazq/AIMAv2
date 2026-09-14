#!/usr/bin/env bash
# Falsification battery: re-introduce each bug, confirm the matching test goes RED,
# then restore. A guard that never fails has no evidence it guards anything.
set -uo pipefail
cd "$(dirname "$0")/.."
APP=app.py
PY=.venv/bin/python
cp "$APP" /tmp/app_backup.py
restore() { cp /tmp/app_backup.py "$APP"; }
trap restore EXIT

run() { $PY -m pytest "$1" -q 2>&1 | tail -1; }

echo "=== baseline (all new tests must be GREEN) ==="
echo "date/header : $(run tests/test_date_default_and_header.py)"
echo "roster      : $(run tests/test_attendee_roster.py)"

echo
echo "=== F1: ensure_report_date becomes a no-op (item 1) ==="
$PY - <<'PYEOF'
src = open("app.py", encoding="utf-8").read()
old = """    updated = report.model_copy(deep=True)
    if parse_report_date(updated.date) is None:
        updated.date = format_report_date(today or datetime.date.today())
    return updated"""
new = """    return report.model_copy(deep=True)"""
assert old in src, "anchor F1 not found"
open("app.py","w",encoding="utf-8").write(src.replace(old, new, 1))
PYEOF
run tests/test_date_default_and_header.py
restore

echo
echo "=== F2: speaker roster reverts to append-only (item 3) ==="
$PY - <<'PYEOF'
src = open("app.py", encoding="utf-8").read()
old = """    for attendee in updated.attendees:
        replacement = name_map.get(attendee.name, "").strip()
        if replacement:
            attendee.name = replacement"""
new = """    pass  # falsified: no in-place rename"""
assert old in src, "anchor F2 not found"
open("app.py","w",encoding="utf-8").write(src.replace(old, new, 1))
PYEOF
run tests/test_attendee_roster.py
restore

echo
echo "=== F3: header drops the start time (item 4) ==="
$PY - <<'PYEOF'
src = open("app.py", encoding="utf-8").read()
old = """                if (result.meeting_time or "").strip():
                    _header_bits.append(f"🕐 **Time:** {result.meeting_time.strip()}")"""
new = """                pass  # falsified: no time in header"""
assert old in src, "anchor F3 not found"
open("app.py","w",encoding="utf-8").write(src.replace(old, new, 1))
PYEOF
run tests/test_date_default_and_header.py
restore

echo
echo "=== F4: roster reverts to a read-only dataframe (item 5) ==="
$PY - <<'PYEOF'
src = open("app.py", encoding="utf-8").read()
old = """                    edited_rows = st.data_editor(
                        [a.model_dump() for a in result.attendees] or [{"name": "", "designation": ""}],
                        num_rows="dynamic",
                        hide_index=True,
                        width="stretch",
                        column_config={
                            "name": st.column_config.TextColumn("Name", width="medium"),
                            "designation": st.column_config.TextColumn("Designation", width="medium"),
                        },
                        key="attendee_roster_editor",
                    )"""
new = """                    st.dataframe([a.model_dump() for a in result.attendees], use_container_width=True)
                    edited_rows = []"""
assert old in src, "anchor F4 not found"
open("app.py","w",encoding="utf-8").write(src.replace(old, new, 1))
PYEOF
run tests/test_attendee_roster.py
restore

echo
echo "=== F5: the whole NaN/blank scrub is removed (item 5) ==="
$PY - <<'PYEOF'
src = open("app.py", encoding="utf-8").read()
old = """    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "<na>", "nat", "none"} else text"""
new = """    return str(value).strip()"""
assert old in src, "anchor F5 not found"
open("app.py","w",encoding="utf-8").write(src.replace(old, new, 1))
PYEOF
run tests/test_attendee_roster.py
restore

echo
echo "=== F5b: only the string-token half removed (proves BOTH layers matter) ==="
$PY - <<'PYEOF'
src = open("app.py", encoding="utf-8").read()
old = """    text = str(value).strip()
    return "" if text.lower() in {"nan", "<na>", "nat", "none"} else text"""
new = """    return str(value).strip()"""
assert old in src, "anchor F5b not found"
open("app.py","w",encoding="utf-8").write(src.replace(old, new, 1))
PYEOF
run tests/test_attendee_roster.py
restore

echo
echo "=== F6: blank-row dropping removed, trailing editor row kept (item 5) ==="
$PY - <<'PYEOF'
src = open("app.py", encoding="utf-8").read()
old = """        if not name and not designation:
            continue
"""
new = """"""
assert old in src, "anchor F6 not found"
open("app.py","w",encoding="utf-8").write(src.replace(old, new, 1))
PYEOF
run tests/test_attendee_roster.py
restore

echo
echo "=== restored: final re-verify ==="
echo "date/header : $(run tests/test_date_default_and_header.py)"
echo "roster      : $(run tests/test_attendee_roster.py)"
git status --porcelain
