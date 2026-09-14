"""Wiring guards: the fix must actually REACH the call site.

This bug's whole shape was "correct logic, never connected to the output". A
unit test on `apply_datetime_overrides` cannot catch a call site that stopped
passing the recording's duration, or an export path that renders something other
than `entry.timestamp`. These are static assertions about the app's own source.

They are deliberately cheap and deterministic: no Streamlit runtime, no API call.
A skipped wire is a silent regression, so it is asserted rather than trusted.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

APP_PATH = os.path.join(ROOT, "app.py")


def _app_tree():
    with open(APP_PATH, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=APP_PATH)


def _calls_to(tree, name):
    """Every Call node in the module whose callee is `name`."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = getattr(func, "id", None) or getattr(func, "attr", None)
            if fname == name:
                out.append(node)
    return out


def _kwarg_names(call):
    return {kw.arg for kw in call.keywords if kw.arg}


# ---------------------------------------------------------------------------
# apply_datetime_overrides is called WITH the measured duration
# ---------------------------------------------------------------------------
def test_every_apply_datetime_overrides_call_passes_the_duration():
    """Without this wiring the app silently always uses the MM:SS fallback, so a
    multi-hour meeting's `02:15` lands 135 s into the minutes. The parameter has
    a default, so dropping the argument is a silent, test-invisible regression —
    which is why it is asserted here.

    The value must be *derived*, not a literal: passing `duration_seconds=None`
    keeps the kwarg present while disabling the scale decision entirely, so a
    presence-only check would pass on a broken wire.
    """
    calls = _calls_to(_app_tree(), "apply_datetime_overrides")
    assert calls, "apply_datetime_overrides is never called from app.py"
    for call in calls:
        kw = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        assert "duration_seconds" in kw, (
            f"app.py:{call.lineno} calls apply_datetime_overrides without "
            f"duration_seconds — the recording's length would never reach the "
            f"offset-scale decision."
        )
        value = kw["duration_seconds"]
        assert not (isinstance(value, ast.Constant) and value.value is None), (
            f"app.py:{call.lineno} passes a literal None as duration_seconds, "
            f"which disables the offset-scale decision just as surely as "
            f"omitting it (a constant cannot carry the measured duration)."
        )


def test_the_duration_is_actually_measured_from_the_uploaded_audio():
    """`probe_duration` must be called on the uploads; a hardcoded value or a
    missing call would make the scale decision fiction."""
    tree = _app_tree()
    assert _calls_to(tree, "probe_duration"), (
        "app.py never calls timeline.probe_duration — the recording's duration "
        "is never measured, so the HH:MM scale can never be selected."
    )


def test_the_measured_duration_is_summed_across_parts():
    """A multi-part meeting's offsets span the SUM of the parts."""
    assert _calls_to(_app_tree(), "total_duration"), (
        "app.py never calls timeline.total_duration — multi-part meetings would "
        "be scaled from a single part's length."
    )


# ---------------------------------------------------------------------------
# The transcript's timestamp is the ONE value every export reads
# ---------------------------------------------------------------------------
def test_no_second_source_of_truth_for_the_rendered_time():
    """The DOCX, the template context and the JSON export must all render
    `entry.timestamp`. `format_clock_time` is the offset->string primitive; if it
    were called anywhere except inside `_anchor_transcript`, something would be
    formatting a time independently of the anchored transcript — and drift is
    what the user reported.

    `_anchor_transcript` is the single legitimate caller, reached through
    `timeline.anchor`.
    """
    tree = _app_tree()
    assert not _calls_to(tree, "format_clock_time"), (
        "app.py calls format_clock_time directly — the rendered time must come "
        "from entry.timestamp, which _anchor_transcript is the only writer of."
    )

    # `anchor` may be called exactly once, from _anchor_transcript.
    anchor_calls = _calls_to(tree, "anchor")
    assert len(anchor_calls) == 1, (
        f"timeline.anchor is called {len(anchor_calls)} times in app.py; the "
        f"transcript must be anchored in exactly one place."
    )


def test_the_transcript_module_is_a_single_shared_implementation():
    """There must be exactly one place that turns offsets into clock times."""
    import glob

    impls = []
    for path in glob.glob(os.path.join(ROOT, "*.py")):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        if "def format_clock_time" in src:
            impls.append(os.path.basename(path))
    assert impls == ["timeline.py"], (
        f"clock-time formatting is implemented in {impls}; it must live only in "
        f"timeline.py so screen, DOCX and export cannot diverge."
    )


# ---------------------------------------------------------------------------
# A fresh run anchors without the user touching the panel
# ---------------------------------------------------------------------------
def test_the_render_path_anchors_the_transcript():
    """`apply_datetime_overrides` only runs when the user saves the panel, so a
    new run would show 00:00-relative times until they did. The render path must
    anchor on its own — the panel is a correction tool, not a prerequisite."""
    calls = _calls_to(_app_tree(), "auto_anchor_transcript")
    # One definition-site call check: it must be CALLED, not merely defined.
    assert calls, (
        "app.py never calls auto_anchor_transcript — a fresh run would leave the "
        "minutes showing the model's raw elapsed offsets."
    )


def test_the_render_path_anchoring_receives_the_measured_duration():
    """Same silent-default trap as apply_datetime_overrides: a literal None keeps
    the call present while disabling the scale decision."""
    for call in _calls_to(_app_tree(), "auto_anchor_transcript"):
        kw = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        assert "duration_seconds" in kw, (
            f"app.py:{call.lineno} calls auto_anchor_transcript without "
            f"duration_seconds — the HH:MM scale could never be selected."
        )
        value = kw["duration_seconds"]
        assert not (isinstance(value, ast.Constant) and value.value is None), (
            f"app.py:{call.lineno} passes a literal None as duration_seconds."
        )
