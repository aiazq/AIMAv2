"""Suite-wide isolation.

`app.py` now runs a model-readiness check at MODULE import, which means importing
it touches the network. Every existing test module imports `app` to assert on its
pure logic, so without this switch the suite would attempt real API calls against
whatever key happens to be in the environment — slow, flaky, and capable of
burning tokens.

Tests that need the check to actually RUN (the launch wiring tests) unset this
variable inside a subprocess and point the app at a local stub provider.
"""
import os

os.environ.setdefault("AIMA_SKIP_MODEL_CHECK", "1")
