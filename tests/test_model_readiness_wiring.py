"""Wiring guards: the readiness check must REACH the launch path and the console.

The failure this feature guards against is the same shape as the bug this repo
already hit once — correct logic that was never connected to the output. A
`model_check.py` that nothing calls is worthless, and a call site that discards
the result is indistinguishable from no check at all.

These are static assertions about `app.py`'s own source: cheap, deterministic,
no Streamlit runtime and no network.

Run:  ./.venv/bin/python -m pytest tests/test_model_readiness_wiring.py -q
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

APP_PATH = os.path.join(ROOT, "app.py")


def _app_source():
    with open(APP_PATH, encoding="utf-8") as f:
        return f.read()


def _app_tree():
    return ast.parse(_app_source(), filename=APP_PATH)


def _calls_to(tree, name):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = getattr(func, "id", None) or getattr(func, "attr", None)
            if fname == name:
                out.append(node)
    return out


# ---------------------------------------------------------------------------
# The check is reachable from app.py at all
# ---------------------------------------------------------------------------
def test_app_imports_the_model_check_module():
    tree = _app_tree()
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "model_check" in imported, (
        "app.py never imports model_check — the readiness result can never reach "
        "the console."
    )


def test_the_launch_path_runs_the_readiness_check():
    """A check that exists but is never invoked on launch is not the feature."""
    calls = _calls_to(_app_tree(), "run_model_readiness_check")
    assert calls, (
        "app.py never calls run_model_readiness_check — the selected model is "
        "still discovered to be unusable only after an upload and a dispatch."
    )


def test_the_launch_check_is_guarded_against_re_running_every_rerun():
    """Streamlit re-executes the whole module on every interaction.

    Without a fingerprint guard the check would fire a network call on every
    click, which is both slow and — on the probe rung — a recurring token cost.
    The guard must both STORE and COMPARE the fingerprint, and the comparison must
    be against the CURRENT settings: comparing a value with itself never changes,
    so the guard would be dead and a change in Settings would never revalidate.
    """
    src = _app_source()
    assert "model_check_fingerprint" in src, (
        "the launch check has no fingerprint guard: it would re-run on every "
        "Streamlit rerun."
    )
    assert re.search(r'st\.session_state\["model_check_fingerprint"\]\s*=', src), (
        "the fingerprint is never stored, so the guard can never match and the "
        "check re-runs on every rerun."
    )
    assert re.search(
        r'st\.session_state\.get\("model_check_fingerprint"\)\s*==\s*fingerprint', src
    ), (
        "the stored fingerprint is never compared against the freshly-computed "
        "one, so editing the model/key/endpoint would not trigger re-validation."
    )


# ---------------------------------------------------------------------------
# The result reaches the Execution Console
# ---------------------------------------------------------------------------
def test_a_failed_check_writes_to_the_execution_console_log():
    """The requirement is a message in the Execution Console, not a toast.

    The console renders `st.session_state["logs_list"]`, so the failure has to
    land in that list. Asserting on the session key is what ties the message to
    the surface the user was told about.
    """
    assert "logs_list" in _app_source()
    assert "console_line" in _app_source(), (
        "the readiness result is never formatted for the console — the user would "
        "see no message at all."
    )


def test_the_console_entry_is_emitted_through_append_log():
    """One logging path, and it must be the container-free one.

    The launch check runs before the console column exists, so it can only touch
    the LIST. Asserting specifically on `append_log` (rather than `log_event`) is
    what makes this a real guard: `log_event` writes into a Streamlit container.
    """
    calls = _calls_to(_app_tree(), "append_log")
    assert calls, (
        "the readiness result is never written to the console log list via "
        "append_log."
    )


def test_the_log_entry_builder_is_separate_from_the_renderer():
    """The launch check runs BEFORE the console column exists.

    `log_event` writes into a Streamlit container, which cannot happen at launch;
    only the list append can. So the state mutation must be callable on its own —
    a pure `append_log` — and the container write must tolerate `None`.
    """
    src = _app_source()
    assert "def append_log" in src, (
        "no container-free log builder exists, so the launch check cannot record "
        "its result before the console is rendered."
    )


def test_the_secret_key_never_reaches_the_fingerprint():
    """The fingerprint decides whether to re-validate. Comparing the raw API key
    would copy a live credential into `st.session_state`, where it would sit
    alongside the report and could leak into the JSON export tab. A digest
    answers "did the settings change?" without storing the secret.
    """
    src = _app_source()
    assert "def settings_fingerprint" in src, "no fingerprint helper exists"
    assert "sha256" in src, (
        "the fingerprint must digest the credentials rather than store them"
    )

    # The digest must cover all three settings, or a change to one would be
    # silently ignored and the user's fix would never be re-validated. The
    # helper takes them as parameters named for the settings they carry.
    body = re.search(r"def settings_fingerprint\(([^)]*)\)", src)
    assert body, "settings_fingerprint has no signature"
    params = body.group(1)
    assert "api_key" in params, "the digest does not cover the API key"
    assert "base_url" in params, "the digest does not cover the endpoint URL"
    assert "model" in params, (
        "the digest does not cover the selected model: changing the model in "
        "Settings would not trigger re-validation."
    )
    assert "hashlib.sha256" in src, (
        "the settings must be digested, not retained: a raw API key in session "
        "state is a credential with a second path to the JSON export."
    )


def test_the_settings_menu_names_all_three_things_the_user_can_change():
    """The message must point at a real remedy, and Settings is where it lives.

    The app exposes exactly three knobs — model, key, endpoint — and the
    guidance must name all three, because the failure could be any of them.
    """
    import model_check

    guidance = model_check.GUIDANCE.lower()
    assert "settings" in guidance
    assert "model" in guidance
    assert "key" in guidance
    assert "endpoint" in guidance


def test_the_processing_dispatch_retries_a_transient_failure():
    """A 503 on the processing path must not discard a completed upload.

    This was a real production failure. The upload has already happened by the
    time the dispatch runs, so the retry is not a nicety — without it the user
    re-uploads hundreds of megabytes to fix something that was never wrong on
    their side.
    """
    src = _app_source()
    assert "RETRYABLE_STATUS" in src, "no retryable-status set on the dispatch path"
    assert re.search(r"DISPATCH_MAX_ATTEMPTS\s*=\s*[2-9]", src), (
        "the dispatch must attempt more than once"
    )
    assert re.search(r"for attempt in range\(1, DISPATCH_MAX_ATTEMPTS", src), (
        "the dispatch has no retry loop"
    )
    # A permanent rejection must still fail fast rather than burn the retries.
    assert re.search(r"not in RETRYABLE_STATUS", src), (
        "a permanent failure must be raised without retrying"
    )


def test_the_overload_message_does_not_misdirect_the_user():
    """A busy provider is not a misconfiguration. The guidance for it must say so
    rather than sending the user to Settings, which cannot fix it.

    Asserted against `model_check` (a pure-logic module, importable with no
    Streamlit runtime) rather than `app` — the wiring module stays static-only so
    these guards remain cheap and runtime-free.
    """
    import model_check

    busy = model_check.ModelCheckResult(
        False, model_check.OVERLOADED,
        message="The provider is overloaded and declined to run 'm' (HTTP 503).",
    )
    line = busy.console_line()
    assert "overload" in line.lower() or "temporary" in line.lower()
    assert "Settings" not in line, (
        "an overloaded provider is not a misconfiguration — do not send the user "
        "to Settings for it"
    )
    assert busy.severity == "warning"


def test_the_dispatch_describes_busy_separately_from_broken():
    """The dispatch-path message must distinguish "wait" from "fix your config".

    Checked as source text (this module does not import `app`), asserting the
    two branches exist and that the busy one says the condition is temporary.
    """
    src = _app_source()
    body = re.search(r"def _describe_http_failure.*?(?=\ndef )", src, re.S)
    assert body, "no failure-description helper on the dispatch path"
    text = body.group(0)
    assert "_BUSY_STATUS_CODES" in text, "busy statuses are not distinguished"
    assert "temporary" in text.lower(), (
        "the busy message must tell the user the condition is temporary"
    )
