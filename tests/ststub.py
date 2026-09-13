"""Minimal Streamlit stub so `app.py` can be imported headlessly.

`app.py` calls `st.set_page_config(...)`, `st.markdown(...)`, registers callbacks
etc. at MODULE level, so importing it outside a Streamlit runtime explodes.
Hand-rolling those stubs is error-prone (a naive recursive stub hung the
interpreter); `MagicMock` handles arbitrary attribute chains correctly.

Usage:
    import ststub
    ststub.install()
    import app          # now importable
"""
import sys
from unittest.mock import MagicMock

_INSTALLED = False


def install(force: bool = False):
    """Install the stub into `sys.modules` as `streamlit`. Idempotent."""
    global _INSTALLED
    if _INSTALLED and not force:
        return sys.modules["streamlit"]

    st = MagicMock(name="streamlit")

    # `st.columns(n)` is unpacked -> must return a REAL list of n mocks, not a Mock.
    st.columns.side_effect = lambda spec, **kw: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    # `st.tabs([...])` is likewise unpacked.
    st.tabs.side_effect = lambda labels, **kw: [MagicMock() for _ in labels]

    # session_state / secrets support dict-style access.
    st.session_state = MagicMock()
    st.session_state.get.side_effect = lambda k, d=None: d
    st.session_state.__getitem__.side_effect = lambda k: MagicMock()
    st.session_state.__contains__ = lambda self, k: False
    st.secrets = MagicMock()
    st.secrets.get.side_effect = lambda k, d=None: d

    # Context managers used with `with`.
    for name in ("spinner", "expander", "form", "container", "status", "empty"):
        getattr(st, name).return_value.__enter__ = lambda *a, **k: MagicMock()
        getattr(st, name).return_value.__exit__ = lambda *a, **k: False

    st.set_page_config.return_value = None
    st.cache_data.return_value = lambda fn: fn
    st.cache_resource.return_value = lambda fn: fn

    # `st.config.get_option(...)` must return a REAL number, not a Mock.
    # Otherwise `int(st.config.get_option("server.maxUploadSize"))` yields 1 and
    # any size-cap logic silently computes nonsense under test.
    _DEFAULTS = {
        "server.maxUploadSize": 200,
        "server.maxMessageSize": 200,
    }
    st.config.get_option.side_effect = lambda key, *a, **k: _DEFAULTS.get(key, None)

    sys.modules["streamlit"] = st
    _INSTALLED = True
    return st


if __name__ == "__main__":
    install()
    import app  # noqa: F401

    print("ok, DEFAULT_MODEL =", app.DEFAULT_MODEL)
    print("has generate_template_from_sample_ai:", hasattr(app, "generate_template_from_sample_ai"))
