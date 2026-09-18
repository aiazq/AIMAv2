"""Launch-time model readiness check — cost-aware, fail-loud, never-blocking.

The app used to discover a bad model only after the user had uploaded several
hundred MB of audio and waited on a dispatch. This module validates the selected
model at launch instead.

Two properties matter more than the happy path:

1. **Token cost.** The check runs on every launch, so the cheap rung must be
   genuinely free. A catalogue listing (`GET /models`) is a metadata call and
   costs nothing; only when the catalogue cannot answer the question does the
   probe rung fire, and it is capped at a single output token.

2. **Uncertainty is not failure.** A gateway that does not serve `/models` and
   refuses a degenerate probe has NOT proven the model broken. Blocking the user
   then would be a worse bug than the one being fixed, so the check degrades to a
   warning and lets the run proceed.

Run:  ./.venv/bin/python -m pytest tests/test_model_readiness.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

import model_check  # noqa: E402


# ---------------------------------------------------------------------------
# A fake httpx-shaped transport. Records every request so the tests can assert
# on COST (no completion call at all) rather than merely on the return value.
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeClient:
    """Minimal stand-in for `httpx.Client` with request recording."""

    def __init__(self, get=None, post=None):
        self._get = get
        self._post = post
        self.get_calls = []
        self.post_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        self.get_calls.append((url, kw))
        result = self._get(url, kw) if callable(self._get) else self._get
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, url, **kw):
        self.post_calls.append((url, kw))
        result = self._post(url, kw) if callable(self._post) else self._post
        if isinstance(result, Exception):
            raise result
        return result


def _openai_catalogue(*ids):
    return FakeResponse(200, {"object": "list", "data": [{"id": i} for i in ids]})


def _gemini_catalogue(*names):
    return FakeResponse(
        200,
        {
            "models": [
                {
                    "name": f"models/{n}",
                    "supportedGenerationMethods": ["generateContent"],
                }
                for n in names
            ]
        },
    )


def _openai_completion(content="OK", status=200):
    """A successful one-token completion — the confirmation rung."""
    return FakeResponse(
        status,
        {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
        },
    )


OPENAI_BASE = "https://gateway.internal/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


# ---------------------------------------------------------------------------
# Bullet 1 — the catalogue rung: free, and it actually answers the question
# ---------------------------------------------------------------------------
def test_a_model_in_the_catalogue_is_confirmed_by_the_capped_probe():
    """A catalogue hit proves the model EXISTS; only a completion proves the
    endpoint will SERVE it.

    This was a real reported failure: the app announced a listed model READY and
    the user's first request came back 503 UNAVAILABLE. Confirming costs the same
    single token as a fallback probe, so it is affordable on every launch.
    """
    fake = FakeClient(
        get=_openai_catalogue("good-model", "other-model"),
        post=_openai_completion(),
    )
    result = model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert result.ok is True
    assert result.status == model_check.READY
    assert len(fake.post_calls) == 1, "a catalogue hit must still be confirmed"


def test_the_catalogue_hit_confirmation_costs_exactly_one_output_token():
    """The budget claim: whatever the path, verification costs at most one token."""
    fake = FakeClient(
        get=_openai_catalogue("good-model"), post=_openai_completion()
    )
    result = model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert fake.post_calls[0][1]["json"]["max_tokens"] == 1
    assert result.tokens_used <= 1


def test_a_model_absent_from_the_catalogue_spends_nothing():
    """The conclusive negative must not probe: asking again cannot reach a model
    the provider does not advertise, so the check stops at the catalogue."""
    fake = FakeClient(get=_openai_catalogue("good-model", "other-model"))
    result = model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="absent-model",
        client_factory=lambda **kw: fake,
    )
    assert result.status == model_check.MISSING
    assert fake.post_calls == [], "an absent model must not cost a completion"
    assert result.tokens_used == 0


def test_the_catalogue_is_listed_exactly_once():
    """One metadata call, not one per rung — the check runs on every launch."""
    fake = FakeClient(
        get=_openai_catalogue("good-model"), post=_openai_completion()
    )
    model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert len(fake.get_calls) == 1


@pytest.mark.parametrize(
    "base_url,response",
    [
        (OPENAI_BASE, _openai_catalogue("good-model", "other")),
        (GEMINI_BASE, _gemini_catalogue("good-model", "other")),
    ],
)
def test_both_provider_dialects_read_their_own_catalogue_shape(base_url, response):
    """Gemini nests models under `models[]` with a `generateContent` filter;
    OpenAI-compatible gateways use `data[].id`. Reading the wrong shape would
    make every model look absent."""
    fake = FakeClient(get=response, post=_openai_completion())
    result = model_check.validate_model(
        base_url=base_url, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert result.ok is True, result.message
    assert result.tokens_used <= 1


# ---------------------------------------------------------------------------
# Bullet 2 — the probe rung: confirmation, capped at a single token
# ---------------------------------------------------------------------------
def test_a_model_absent_from_a_readable_catalogue_is_reported_missing():
    """The catalogue answered, and the answer was no. No probe is warranted:
    a model the provider does not list cannot be reached by asking again."""
    fake = FakeClient(get=_openai_catalogue("other-model"), post=_openai_completion())
    result = model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert result.ok is False
    assert result.status == model_check.MISSING
    assert fake.post_calls == [], "a readable catalogue's 'no' is conclusive"
    assert result.tokens_used == 0


def test_an_unreadable_catalogue_escalates_to_a_probe():
    """A gateway that 404s /models prompts the one call that settles it."""
    fake = FakeClient(post=_openai_completion())
    fake._get = FakeResponse(404, {"error": "not found"})
    result = model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert result.ok is True, result.message
    assert fake.post_calls, "an unreadable catalogue must not conclude 'broken'"


def test_the_probe_costs_at_most_one_output_token():
    """Token economy is the requirement, not a nicety.

    The probe runs whenever the catalogue cannot answer, so it must be the
    cheapest possible completion: `max_tokens: 1` (OpenAI dialect) /
    `maxOutputTokens: 1` (Gemini). Asserting the *payload* is what makes this a
    cost test rather than a "did it call" test.
    """
    fake = FakeClient(get=FakeResponse(404, {}), post=_openai_completion())
    model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    payload = fake.post_calls[0][1]["json"]
    assert payload["max_tokens"] == 1, payload
    assert payload["stream"] is False, "a streamed probe cannot be a 1-token cap"

    gem = FakeClient(get=FakeResponse(404, {}), post=_openai_completion())
    model_check.validate_model(
        base_url=GEMINI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: gem,
    )
    gen_cfg = gem.post_calls[0][1]["json"]["generationConfig"]
    assert gen_cfg["maxOutputTokens"] == 1, gen_cfg


def test_the_probe_carries_the_smallest_possible_prompt():
    """A one-token probe wearing a 2000-line prompt is not a cheap check.

    The prompt must be a single short token in and of itself, and it must NOT
    drag the minutes-generation schema along with it.
    """
    fake = FakeClient(get=FakeResponse(404, {}), post=_openai_completion())
    model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    payload = fake.post_calls[0][1]["json"]
    prompt = payload["messages"][0]["content"]
    assert len(prompt) <= 16, f"probe prompt is not minimal: {prompt!r}"
    assert "MeetingMinutesReport" not in json.dumps(payload)


def test_the_probe_asks_for_a_response_format_the_endpoint_can_honour():
    """Deploy is on any OpenAI-compatible gateway.

    `json_object` is the shape the real run needs, and is optional for the probe:
    requiring it can make a working model look broken. The probe therefore
    asserts only what the app itself requires of every call.
    """
    fake = FakeClient(get=FakeResponse(404, {}), post=_openai_completion())
    model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert "response_format" not in fake.post_calls[0][1]["json"]


# ---------------------------------------------------------------------------
# Bullet 3 — failures are classified, and uncertainty never blocks
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "base_url,status",
    [
        (OPENAI_BASE, 401),
        (OPENAI_BASE, 403),
        (GEMINI_BASE, 401),
        (GEMINI_BASE, 403),
    ],
)
def test_a_rejected_key_is_reported_as_an_auth_failure(base_url, status):
    """A bad key needs a different fix from a bad model name, so the two must
    not collapse into one message. 401/403 mean the key, at either dialect."""
    fake = FakeClient(get=FakeResponse(404, {}),
                      post=FakeResponse(status, {"error": {"message": "nope"}}))
    result = model_check.validate_model(
        base_url=base_url, api_key="bad", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert result.ok is False
    assert result.status == model_check.AUTH
    assert "key" in result.message.lower()


@pytest.mark.parametrize("status", [400, 404])
def test_a_gemini_model_name_failure_is_reported_as_missing(status):
    """Gemini puts the model in the URL path, so a 400/404 there names the model
    rather than the request — the opposite of the OpenAI shape."""
    fake = FakeClient(get=FakeResponse(404, {}),
                      post=FakeResponse(status, {"error": {"message": "no such model"}}))
    result = model_check.validate_model(
        base_url=GEMINI_BASE, api_key="k", model_name="ghost-model",
        client_factory=lambda **kw: fake,
    )
    assert result.ok is False
    assert result.status == model_check.MISSING


def test_an_unreachable_endpoint_is_unknown_not_missing():
    """A DNS failure or a connection refused says nothing about the model. It
    must not be reported as 'this model does not exist'."""
    def _boom(url, kw):
        raise OSError("connection refused")

    fake = FakeClient(get=FakeResponse(404, {}), post=_boom)
    result = model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert result.ok is False
    assert result.status == model_check.UNKNOWN
    assert "reach" in result.message.lower()


def test_an_unreadable_catalogue_plus_a_network_error_costs_no_tokens():
    fake = FakeClient(get=FakeResponse(404, {}), post=OSError("timed out"))
    result = model_check.validate_model(
        base_url=OPENAI_BASE, api_key="k", model_name="good-model",
        client_factory=lambda **kw: fake,
    )
    assert result.tokens_used == 0, "a failed probe consumed nothing to count"


def test_only_an_unambiguous_failure_is_shown_as_an_error():
    """The severity rule, stated once as a table.

    `missing` and `auth` are definite answers: the selected model cannot work
    and the user is the only one who can change it. `unknown` is the absence of
    an answer — a slow gateway, a proxy that hides /models — and presenting that
    as an error would send the user chasing a problem that may not exist.
    """
    assert model_check.ModelCheckResult(True, model_check.READY).severity is None
    assert model_check.ModelCheckResult(False, model_check.MISSING).severity == "error"
    assert model_check.ModelCheckResult(False, model_check.AUTH).severity == "error"
    assert model_check.ModelCheckResult(False, model_check.UNKNOWN).severity == "warning"


def test_the_severity_maps_onto_a_console_log_level():
    assert model_check.ModelCheckResult(False, model_check.MISSING).log_level == "ERROR"
    assert model_check.ModelCheckResult(False, model_check.UNKNOWN).log_level == "WARN"
    assert model_check.ModelCheckResult(True, model_check.READY).log_level == "SUCCESS"


def test_the_console_line_carries_the_guidance_only_when_definite():
    """An error must tell the user what to change; a warning must not imply the
    setup is broken, so it carries no instruction at all."""
    definite = model_check.ModelCheckResult(False, model_check.MISSING, "Model gone.")
    line = definite.console_line()
    assert "Model gone." in line
    assert "Settings" in line

    uncertain = model_check.ModelCheckResult(False, model_check.UNKNOWN, "No reply.")
    uncertain_line = uncertain.console_line()
    assert "No reply." in uncertain_line
    assert "Settings" not in uncertain_line


def test_every_failure_carries_actionable_guidance():
    """The feature's second requirement: the console message must tell the user
    to change the model OR the key/endpoint via Settings. A bare 'failed' is the
    behaviour being replaced."""
    for status in (model_check.MISSING, model_check.AUTH, model_check.UNKNOWN):
        result = model_check.ModelCheckResult(False, status, "something broke")
        assert "Settings" in result.guidance
        assert "model" in result.guidance.lower()
        assert ("key" in result.guidance.lower()
                or "endpoint" in result.guidance.lower())


def test_a_ready_model_needs_no_guidance():
    assert model_check.ModelCheckResult(True, model_check.READY).guidance == ""


