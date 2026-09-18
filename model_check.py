"""Launch-time model readiness check.

Pure-logic module (Streamlit-free, same convention as `media_pipeline` and
`timeline`) so the ladder is unit-testable without a runtime.

Why this exists: the app previously discovered an unusable model only AFTER the
user had uploaded several hundred MB of audio and waited on a dispatch. The
failure was reported one step from the finish line, when it was knowable at
launch.

Two properties matter more than the happy path:

1. **Token cost.** The check runs on every launch, so the cheap rung must be
   genuinely free. A catalogue listing (`GET /models`) is a metadata call that
   costs no tokens. Only a catalogue that cannot answer the question escalates to
   the probe rung, which is capped at a single output token.

2. **Uncertainty is not failure.** A gateway that does not serve `/models` and
   refuses a degenerate probe has NOT proven the model broken. Blocking the user
   then would be a worse bug than the one being fixed, so the check degrades to a
   warning and lets the run proceed.
"""
from __future__ import annotations

# Result statuses.
READY = "ready"
MISSING = "missing"
AUTH = "auth"
UNKNOWN = "unknown"

_CATALOGUE_TIMEOUT = 10.0
_PROBE_TIMEOUT = 20.0

# The cheapest a completion can possibly be: one character in, one token out.
# Anything larger turns a launch-time check into a recurring cost.
_PROBE_PROMPT = "hi"
_PROBE_MAX_TOKENS = 1

# The instruction shown to the user on any failure. Kept in one place so the
# console message cannot drift from what the Settings menu actually offers.
GUIDANCE = (
    "Open ⚙️ Settings in the header to select a different model, or correct the "
    "API key / provider endpoint URL."
)


def _is_gemini_probe_failure(status_code: int) -> bool:
    return status_code in (400, 404)


class ModelCheckResult:
    """Outcome of a readiness check.

    `tokens_used` is carried explicitly rather than assumed: the cheap rung's
    whole justification is that it spends nothing, and a claim like that needs to
    be assertable.
    """

    def __init__(self, ok, status, message="", tokens_used=0):
        self.ok = ok
        self.status = status
        self.message = message
        self.tokens_used = tokens_used

    @property
    def guidance(self) -> str:
        """The next action for the user. Empty when the model is ready."""
        return "" if self.ok else GUIDANCE

    @property
    def severity(self) -> str | None:
        """Console severity: `"error"`, `"warning"`, or None when ready.

        `MISSING` / `AUTH` are definite answers and read as errors. `UNKNOWN` is
        the ABSENCE of an answer — a gateway that hides its catalogue, a slow
        proxy — so it reads as a warning. Presenting uncertainty as an error
        would send the user chasing a problem that may not exist.
        """
        if self.ok:
            return None
        return "error" if self.status in (MISSING, AUTH) else "warning"

    @property
    def log_level(self) -> str:
        """The `log_event` level this outcome should be printed at."""
        return {"error": "ERROR", "warning": "WARN"}.get(self.severity, "SUCCESS")

    def console_line(self) -> str:
        """The single line that goes to the Execution Console.

        A definite failure carries the Settings guidance with it, so the user is
        told what to change rather than merely that something is wrong.
        """
        base = f"Model readiness: {self.message}"
        return f"{base} {self.guidance}" if self.severity == "error" else base

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"ModelCheckResult(ok={self.ok!r}, status={self.status!r})"


def is_gemini_endpoint(base_url: str) -> bool:
    """Gemini's native API differs in BOTH catalogue shape and auth header."""
    return "googleapis.com" in (base_url or "")


def list_models(base_url: str, api_key: str, client_factory=None) -> list[str] | None:
    """Model IDs the endpoint advertises, or `None` when it cannot be read.

    `None` is deliberately distinct from `[]`: "the provider does not answer
    /models" is not evidence that any particular model is absent.
    """
    cleaned = (base_url or "").rstrip("/")
    if not cleaned:
        return None

    factory = client_factory or _default_client_factory(cleaned)
    try:
        with factory(timeout=_CATALOGUE_TIMEOUT) as client:
            if is_gemini_endpoint(cleaned):
                resp = client.get(f"{cleaned}/models?key={api_key}")
                if resp.status_code != 200:
                    return None
                data = resp.json() or {}
                return [
                    str(m.get("name", "")).replace("models/", "")
                    for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]
            resp = client.get(
                f"{cleaned}/models", headers={"Authorization": f"Bearer {api_key}"}
            )
            if resp.status_code != 200:
                return None
            data = resp.json() or {}
            return [str(m.get("id", "")) for m in data.get("data", [])]
    except Exception:
        # A missing or unreachable catalogue is not evidence about the model.
        return None


def probe_model(base_url, api_key, model_name, client_factory=None,
                timeout=None) -> ModelCheckResult:
    """One minimum-cost completion — the rung that settles what the catalogue could not.

    Capped at a single output token and a two-character prompt, so the cost of
    running this on every launch is negligible even on a paid endpoint.
    """
    cleaned = (base_url or "").rstrip("/")
    gemini = is_gemini_endpoint(cleaned)

    if gemini:
        url = f"{cleaned}/models/{model_name}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        payload = {
            "contents": [{"parts": [{"text": _PROBE_PROMPT}]}],
            "generationConfig": {
                "maxOutputTokens": _PROBE_MAX_TOKENS,
                "temperature": 0,
            },
        }
    else:
        url = f"{cleaned}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            "max_tokens": _PROBE_MAX_TOKENS,
            # A gateway that ignores max_tokens but streams by default would turn
            # a 1-token probe into an unbounded response. Ask for one body.
            "stream": False,
            "temperature": 0,
        }

    factory = client_factory or _default_client_factory(cleaned)
    try:
        with factory(timeout=timeout or _PROBE_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
    except Exception as exc:
        # Nothing was proven about the model — only that it could not be reached.
        return ModelCheckResult(
            ok=False, status=UNKNOWN,
            message=f"Could not reach {cleaned} to test '{model_name}': {exc}",
        )

    if resp.status_code == 200:
        return ModelCheckResult(
            ok=True, status=READY,
            message=f"Model '{model_name}' responded to a {_PROBE_MAX_TOKENS}-token probe.",
            tokens_used=_PROBE_MAX_TOKENS,
        )

    if resp.status_code in (401, 403):
        return ModelCheckResult(
            ok=False, status=AUTH,
            message=(
                f"The provider rejected the API key (HTTP {resp.status_code}). "
                "The key is missing, expired, or not valid for this endpoint."
            ),
        )

    if gemini and _is_gemini_probe_failure(resp.status_code):
        # Gemini validates the model name in the URL path, so 400/404 here names
        # the model itself rather than the request shape.
        return ModelCheckResult(
            ok=False, status=MISSING,
            message=(
                f"'{model_name}' is not available at this endpoint "
                f"(HTTP {resp.status_code})."
            ),
        )

    return ModelCheckResult(
        ok=False, status=UNKNOWN,
        message=(
            f"'{model_name}' could not be confirmed ready — the provider returned "
            f"HTTP {resp.status_code} to a minimal probe."
        ),
    )


def validate_model(base_url, api_key, model_name, client_factory=None,
                   timeout=None) -> ModelCheckResult:
    """Decide whether `model_name` is usable at `base_url`.

    Rung 1 (free): the provider's catalogue. A model listed as present is proof
    of readiness and costs no tokens; a model absent from a catalogue that was
    read successfully is proof of the opposite, and is conclusive — asking again
    cannot reach a model the provider does not advertise.

    Rung 2 (one token): only when the catalogue could not be read at all.
    """
    catalogue = list_models(base_url, api_key, client_factory=client_factory)
    if catalogue is not None:
        if model_name in catalogue:
            return ModelCheckResult(
                ok=True, status=READY,
                message=f"Model '{model_name}' is listed as available.",
                tokens_used=0,
            )
        return ModelCheckResult(
            ok=False, status=MISSING,
            message=(
                f"'{model_name}' is not in this endpoint's model list. "
                "It may have been retired or renamed."
            ),
        )

    # The catalogue could not answer — spend the one token that can.
    return probe_model(
        base_url, api_key, model_name, client_factory=client_factory, timeout=timeout
    )



def _default_client_factory(base_url: str):
    def _factory(**kwargs):
        import httpx

        return httpx.Client(**kwargs)

    return _factory
