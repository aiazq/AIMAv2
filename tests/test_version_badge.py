"""The app displays its version, and the displayed version matches the release.

A version badge that drifts from the actual release is worse than none — it
tells the user they are running code they are not. These tests pin both the
presence of the badge and the single source of truth for its value.

The render check runs in a SUBPROCESS: tests/ststub.py installs a MagicMock as
sys.modules["streamlit"], which breaks AppTest in-process.
"""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402

# Emits the rendered captions, so we test what the USER sees rather than that a
# string merely exists in the source.
_PROBE = r"""
import json, sys
from streamlit.testing.v1 import AppTest

at = AppTest.from_file(sys.argv[1], default_timeout=90)
at.secrets["API_KEY"] = "dummy-not-a-real-secret"
at.run()

print(json.dumps({
    "exceptions": [str(e.value) for e in at.exception],
    "captions": [str(c.value) for c in at.caption],
    "markdown": [str(m.value) for m in at.markdown],
}))
"""


def _render():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, os.path.join(ROOT, "app.py")],
        capture_output=True, text=True, timeout=300, cwd=ROOT,
    )
    assert proc.returncode == 0, f"render failed:\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_app_exposes_a_version_constant():
    assert isinstance(app.APP_VERSION, str)
    assert re.fullmatch(r"v\d+\.\d+(\.\d+)?", app.APP_VERSION), app.APP_VERSION


def test_version_is_the_current_release():
    """Pins the release. Bumping APP_VERSION without updating this is a deliberate
    two-file edit, so a version bump can never be accidental."""
    assert app.APP_VERSION == "v0.5.2"


def test_version_is_not_duplicated_as_a_literal():
    """The badge must read the constant, not restate the number.

    Two hardcoded copies drift: one gets bumped, the other does not. Counting
    occurrences of the CURRENT version (not just standalone string literals)
    catches a version embedded inside a longer string such as "AIMA v0.2.1".
    Version-agnostic on purpose, so a bump does not require editing this test.
    """
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    occurrences = re.findall(re.escape(app.APP_VERSION), src)
    assert len(occurrences) == 1, (
        f"expected {app.APP_VERSION} to appear exactly once (the APP_VERSION "
        f"constant), found {len(occurrences)} occurrences"
    )
    assert re.search(rf"APP_VERSION\s*=\s*\"{re.escape(app.APP_VERSION)}\"", src), (
        "the version must be assigned to APP_VERSION"
    )
    assert re.search(r"st\.caption\(f\"AIMA \{APP_VERSION\}\"\)", src), (
        "the footer must interpolate APP_VERSION"
    )


def test_the_version_appears_on_the_page():
    """Reads app.APP_VERSION rather than a literal, so a release bump does not
    break the test — and the assertion still pins the page to the constant."""
    rendered = _render()
    assert not rendered["exceptions"], rendered["exceptions"]
    visible = " ".join(rendered["captions"] + rendered["markdown"])
    assert app.APP_VERSION in visible, (
        f"{app.APP_VERSION} not rendered. captions={rendered['captions']}"
    )


def test_the_version_is_rendered_as_small_text():
    """Requested as 'small text' — a caption or a small-styled element, not a heading."""
    rendered = _render()
    v = app.APP_VERSION
    in_caption = any(v in c for c in rendered["captions"])
    in_small_markdown = any(
        v in m and ("<small" in m or "font-size" in m)
        for m in rendered["markdown"]
    )
    assert in_caption or in_small_markdown, (
        "version should render as caption/small text, not a heading. "
        f"captions={rendered['captions']}"
    )


def test_the_version_is_not_a_heading():
    rendered = _render()
    for m in rendered["markdown"]:
        if app.APP_VERSION in m:
            assert not re.search(r"^#{1,6}\s", m.strip()), f"version must not be a heading: {m!r}"


def test_the_page_still_renders_without_exceptions():
    rendered = _render()
    assert rendered["exceptions"] == [], rendered["exceptions"]


def test_changelog_has_a_section_for_the_current_version():
    """Changelog-driven versioning: every release has a matching heading."""
    path = os.path.join(ROOT, "CHANGELOG.md")
    assert os.path.exists(path), "CHANGELOG.md is missing"
    changelog = open(path, encoding="utf-8").read()
    assert re.search(rf"^##\s+{re.escape(app.APP_VERSION)}\b", changelog, re.M), (
        f"CHANGELOG.md has no '## {app.APP_VERSION}' section"
    )
