"""End-to-end: a launch-time verdict reaches the Execution Console.

The unit tests prove the ladder is correct and the AST guards prove the wiring
exists. Neither proves the USER sees a message, because the console renders from
`st.session_state["logs_list"]` into a Streamlit placeholder and "the list was
mutated" is not the same claim as "the line is on screen".

So this boots the REAL app under `AppTest`, with `AIMA_SKIP_MODEL_CHECK` unset so
the check actually runs, against a local stub provider — a real socket, a real
`GET /models`, no external network and no real API key.

Run:  ./.venv/bin/python -m pytest tests/test_model_readiness_launch.py -q
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Runs in a fresh interpreter: tests/ststub.py installs a MagicMock as
# sys.modules["streamlit"] at import time, and pytest imports every test module
# during collection, so AppTest cannot work in-process.
_PROBE = r"""
import json, os, sys
from streamlit.testing.v1 import AppTest

# The suite-wide isolation switch is set by tests/conftest.py; this probe is the
# one place that must defeat it, because observing the real behaviour is the
# entire point of the test.
os.environ.pop("AIMA_SKIP_MODEL_CHECK", None)

at = AppTest.from_file(sys.argv[1], default_timeout=120)
at.secrets["API_KEY"] = "stub-not-a-real-secret"
at.secrets["ENDPOINT_URL"] = sys.argv[2]
at.run()

# The console is a single markdown blob identified by its container id.
console = [str(m.value) for m in at.markdown if "aima-terminal-box" in str(m.value)]
print("__RESULT__" + json.dumps({
    "exceptions": [str(e.value) for e in at.exception],
    "console": console,
    "captions": [str(c.value) for c in at.caption],
}))
"""


class _StubProvider(BaseHTTPRequestHandler):
    """Serves the two endpoints the readiness check can reach."""

    catalogue = []            # models advertised by GET /models
    catalogue_status = 200
    completion_status = 200
    posts_seen = 0            # proves which rung answered

    def log_message(self, *a):  # keep the test output clean
        pass

    def do_GET(self):
        # The configured endpoint carries a path prefix (`/v1/models`), so match
        # on the suffix — matching `/models` exactly never fires, silently
        # pushing every case onto the paid rung.
        if self.path.rstrip("/").endswith("/models"):
            body = json.dumps(
                {"object": "list", "data": [{"id": m} for m in self.catalogue]}
            ).encode()
            self.send_response(self.catalogue_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        type(self).posts_seen += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.completion_status != 200:
            body = json.dumps({
                "error": {
                    "code": self.completion_status,
                    "message": "This model is currently experiencing high demand. "
                               "Spikes in demand are usually temporary. Please try "
                               "again later.",
                    "status": "UNAVAILABLE",
                }
            }).encode()
            self.send_response(self.completion_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def stub_provider():
    """A real HTTP server on localhost, one per test, torn down afterwards."""
    _StubProvider.posts_seen = 0
    _StubProvider.completion_status = 200
    server = HTTPServer(("127.0.0.1", 0), _StubProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _boot(base_url):
    """Boot the real app against `base_url` and report what it rendered."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, os.path.join(ROOT, "app.py"), base_url],
        capture_output=True, text=True, timeout=420, cwd=ROOT,
        env={**os.environ, "AIMA_SKIP_MODEL_CHECK": ""},
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr[-3000:]}"
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    raise AssertionError(
        f"no probe output\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}"
    )


def _console_text(rendered):
    return " ".join(rendered["console"])


def test_the_app_still_boots_with_the_check_enabled(stub_provider):
    _StubProvider.catalogue = ["gemini-3.6-flash"]
    _StubProvider.catalogue_status = 200
    rendered = _boot(stub_provider)
    assert rendered["exceptions"] == [], rendered["exceptions"]


def test_a_ready_model_is_reported_in_the_execution_console(stub_provider):
    """Positive case: the user is told the model is fine, at launch.

    A catalogue hit is confirmed by one capped probe, so exactly one POST is
    expected — and it must be a 1-token request, not a real generation.
    """
    _StubProvider.catalogue = ["gemini-3.6-flash"]
    _StubProvider.catalogue_status = 200
    rendered = _boot(stub_provider)
    text = _console_text(rendered)
    assert "aima-terminal-box" in text
    assert "Model readiness" in text, f"no readiness line on screen: {text[-800:]}"
    assert "gemini-3.6-flash" in text
    assert _StubProvider.posts_seen == 1, (
        "a listed model must be confirmed by exactly one capped probe"
    )


def test_a_listed_but_overloaded_model_is_reported_at_launch(stub_provider):
    """The reported failure, reproduced end-to-end.

    The catalogue lists the model, so the old check announced READY — and the
    user's first real request came back 503 UNAVAILABLE. Now the launch message
    must say the provider is busy, and must NOT blame the configuration.
    """
    _StubProvider.catalogue = ["gemini-3.6-flash"]
    _StubProvider.catalogue_status = 200
    _StubProvider.completion_status = 503
    rendered = _boot(stub_provider)
    text = _console_text(rendered)
    assert "Model readiness" in text, f"no readiness line on screen: {text[-800:]}"
    assert "overload" in text.lower() or "high demand" in text.lower(), (
        f"the launch message must report the provider as busy: {text[-600:]}"
    )
    assert "gemini-3.6-flash" in text, "the message must name the model affected"
    assert "Settings" not in text, (
        "an overloaded provider is not a misconfiguration — do not send the user "
        "to Settings for it"
    )


def test_an_unavailable_model_is_reported_in_the_execution_console(stub_provider):
    """The reported behaviour: a bad model must be visible in the console, with
    the remedy spelled out — not merely an assertion that something broke."""
    _StubProvider.catalogue = ["some-other-model"]
    _StubProvider.catalogue_status = 200
    rendered = _boot(stub_provider)
    text = _console_text(rendered)
    assert "Model readiness" in text, f"no readiness line on screen: {text[-800:]}"
    assert "Settings" in text, "the message must name the Settings menu as the remedy"
    assert "model" in text.lower()
    assert "gemini-3.6-flash" in text, "the message must name the model that failed"


def test_the_console_message_survives_the_first_render(stub_provider):
    """The verdict must be present on the FIRST paint, not appended later.

    The check runs at module scope precisely so `main()`'s initial console render
    already includes it. A message that only appeared after a rerun would be a
    message most users never see.
    """
    _StubProvider.catalogue = ["some-other-model"]
    _StubProvider.catalogue_status = 200
    rendered = _boot(stub_provider)
    # Exactly one console render is captured for a single run; the readiness line
    # being inside it is the assertion that it made the first pass.
    assert rendered["console"], "the console rendered nothing"
    assert "Model readiness" in rendered["console"][0]


def test_an_unreachable_provider_does_not_break_the_app(stub_provider):
    """Degradation requirement: if the endpoint cannot be reached, the app must
    still boot and still let the user work. Blocking here would be a worse bug
    than the one this feature fixes."""
    rendered = _boot("http://127.0.0.1:9/v1")  # discard port: nothing listens
    assert rendered["exceptions"] == [], rendered["exceptions"]
    text = _console_text(rendered)
    assert "Model readiness" in text
