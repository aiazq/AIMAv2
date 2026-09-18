"""Transient provider overload must not destroy a completed upload.

Reported in production: a 503 `UNAVAILABLE` ("This model is currently
experiencing high demand") on the processing path threw away the user's upload
and dispatch after a single attempt, even though the same status is retried for
free everywhere else in the app.

Two separate defects are pinned here:

1. `dispatch_http_request` had no retry, and `analyze_meeting_audio_rest` raised
   on the first non-200. A 503 that clears in two seconds cost the user the whole
   run.
2. The launch check reported the model READY while the model was in fact
   overloaded, because it only asked the catalogue ("is this model listed?")
   instead of exercising the model.

Run:  ./.venv/bin/python -m pytest tests/test_transient_provider_failure.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402
import model_check  # noqa: E402

PROVIDER_503 = json.dumps({
    "error": {
        "code": 503,
        "message": "This model is currently experiencing high demand. Spikes in "
                   "demand are usually temporary. Please try again later.",
        "status": "UNAVAILABLE",
    }
})


class _Resp:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


# ---------------------------------------------------------------------------
# Defect 1: the dispatch retries a transient failure instead of discarding the run
# ---------------------------------------------------------------------------
def test_a_transient_503_is_retried_and_the_run_completes(monkeypatch):
    """The exact reported failure: 503, then the provider recovers.

    The upload has already happened by this point. Failing on the first attempt
    throws away work the user cannot get back without re-uploading, so a status
    the provider itself describes as temporary must be retried.
    """
    attempts = []

    def _dispatch(endpoint_url, headers, payload):
        attempts.append(1)
        if len(attempts) < 3:
            return _Resp(503, text=PROVIDER_503)
        return _Resp(200, payload={
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "title": "T", "date": "Undated",
                "attendees": [{"name": "A", "designation": ""}],
                "executive_summary": "S.",
                "agenda_and_decisions": [], "action_items": [], "transcript": [],
            })}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2,
                              "totalTokenCount": 3},
        })

    monkeypatch.setattr(app, "dispatch_http_request", _dispatch)
    report, _ = app.analyze_meeting_audio_rest(
        audio_file_bytes=b"AUDIO", mime_type="audio/mp3", api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-3.6-flash",
        progress_bar=_Mock(), status_text=_Mock(), log_container=_Mock(),
        logs_list=[],
    )
    assert report is not None, "a transient 503 must not discard the run"
    assert len(attempts) >= 2, "the dispatch must have retried"


def test_a_definite_failure_is_not_retried(monkeypatch):
    """A 400/401/403 will not fix itself. Retrying it just delays the message and
    hides the cause, so it must be raised on the first response."""
    attempts = []

    def _dispatch(endpoint_url, headers, payload):
        attempts.append(1)
        return _Resp(400, text='{"error":{"message":"bad request"}}')

    monkeypatch.setattr(app, "dispatch_http_request", _dispatch)
    with pytest.raises(Exception):
        app.analyze_meeting_audio_rest(
            audio_file_bytes=b"AUDIO", mime_type="audio/mp3", api_key="k",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model_name="gemini-3.6-flash",
            progress_bar=_Mock(), status_text=_Mock(), log_container=_Mock(),
            logs_list=[],
        )
    assert len(attempts) == 1, "a permanent failure must not be retried"


def test_the_503_message_tells_the_user_what_it_means(monkeypatch):
    """The raw provider blob is not an explanation. It must be presented as a
    temporary provider condition, not as a fault in the user's configuration."""
    def _dispatch(endpoint_url, headers, payload):
        return _Resp(503, text=PROVIDER_503)

    monkeypatch.setattr(app, "dispatch_http_request", _dispatch)
    logs = []
    with pytest.raises(Exception) as exc:
        app.analyze_meeting_audio_rest(
            audio_file_bytes=b"AUDIO", mime_type="audio/mp3", api_key="k",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model_name="gemini-3.6-flash",
            progress_bar=_Mock(), status_text=_Mock(), log_container=_Mock(),
            logs_list=logs,
        )
    message = str(exc.value).lower()
    assert "overload" in message or "high demand" in message or "temporary" in message
    assert "settings" not in message, (
        "an overloaded provider is not a misconfiguration — sending the user to "
        "Settings would be wrong advice"
    )


# ---------------------------------------------------------------------------
# Defect 2: a listed model is not necessarily a model that will serve you
# ---------------------------------------------------------------------------
def test_a_listed_but_overloaded_model_is_not_reported_ready():
    """The reported gap: the catalogue lists the model, the user is told READY,
    and the first real request 503s.

    A catalogue hit proves the model EXISTS. It does not prove the endpoint will
    serve it, so an overload signal must downgrade the verdict.
    """
    def _get(url, kw):
        return _FakeResp(200, {"object": "list", "data": [{"id": "gemini-3.6-flash"}]})

    def _post(url, kw):
        return _FakeResp(503, text=PROVIDER_503).with_payload(
            json.loads(PROVIDER_503)
        )

    result = model_check.validate_model(
        base_url="https://gateway.internal/v1", api_key="k",
        model_name="gemini-3.6-flash",
        client_factory=lambda **kw: _Client(_get, _post),
    )
    assert result.status != model_check.READY, (
        "a model the provider is refusing with 503 must not be reported ready"
    )
    assert result.ok is False


def test_an_overload_verdict_is_a_warning_not_a_configuration_error():
    """An overloaded provider is not the user's mistake — nothing in Settings
    fixes it. It must warn without pointing at Settings."""
    result = model_check.ModelCheckResult(
        False, model_check.OVERLOADED,
        message="The provider is overloaded right now.",
    )
    assert result.severity == "warning"
    assert result.log_level == "WARN"
    assert "Settings" not in result.console_line()


def test_an_overload_warning_still_names_the_model():
    result = model_check.ModelCheckResult(
        False, model_check.OVERLOADED, message="Overloaded.",
    )
    assert "Overloaded." in result.console_line()


# --- tiny fakes ------------------------------------------------------------
class _Mock:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def with_payload(self, payload):
        self._payload = payload
        return self

    def json(self):
        return self._payload


class _Client:
    def __init__(self, get, post):
        self._get, self._post = get, post
        self.get_calls, self.post_calls = [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        self.get_calls.append(url)
        return self._get(url, kw)

    def post(self, url, **kw):
        self.post_calls.append(url)
        return self._post(url, kw)
