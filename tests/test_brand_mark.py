"""Brand-mark regression guard.

Runs in a SUBPROCESS on purpose.

`tests/ststub.py` installs a MagicMock as `sys.modules["streamlit"]` at import
time so that `app.py` can be imported without a runtime. Pytest imports every
test module up front, so by the time any test runs the genuine streamlit
package is shadowed process-wide — and AppTest cannot work in-process. Running
the check in a fresh interpreter is the only reliable isolation.

Nothing here performs a network call: a placeholder API key is supplied.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Emits one JSON object describing what the app rendered. Kept inline so the
# child has no imports from this test package (which would re-install the stub).
_PROBE = r"""
import json, sys
from streamlit.testing.v1 import AppTest

at = AppTest.from_file(sys.argv[1], default_timeout=90)
at.secrets["API_KEY"] = "dummy-not-a-real-secret"
at.run()

embedded = [str(m.value) for m in at.markdown
            if "data:image/png;base64" in str(m.value)]
header = " ".join(str(m.value) for m in at.markdown[:4])
print(json.dumps({
    "exceptions": [str(e.value) for e in at.exception],
    "embedded_count": len(embedded),
    "embedded": embedded,
    "header": header,
}))
"""


def _probe():
    """Boot the real app in a clean interpreter and report what it rendered."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, os.path.join(ROOT, "app.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "app failed to boot in a clean interpreter\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    # the probe prints exactly one JSON line; streamlit may log to stderr
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no probe output\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")


def test_app_renders_the_brand_mark_only_once():
    """Regression: exactly ONE logo.

    A second mark used to render as a watermark in the right panel's empty
    state, which the user saw as a duplicate logo. Only the header may embed one.
    """
    info = _probe()
    assert not info["exceptions"], info["exceptions"]
    assert info["embedded_count"] == 1, (
        f"expected exactly 1 embedded logo, found {info['embedded_count']}"
    )


def test_header_pairs_the_mark_with_the_tagline_text():
    """The header mark must be followed by the plain text 'AI Meeting Assistant'."""
    info = _probe()
    assert "AI Meeting Assistant" in info["header"], info["header"][:300]


def test_no_watermark_logo_remains():
    """No faded/second copy of the mark anywhere (the old watermark)."""
    info = _probe()
    for block in info["embedded"]:
        assert "opacity" not in block, "a faded watermark logo is still rendered"
