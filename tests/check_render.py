"""Headless render check for the multi-file UI.

Runs in a SUBPROCESS: tests/ststub.py installs a MagicMock as sys.modules
["streamlit"] at import time, and pytest imports every test module during
collection — so in-process AppTest always sees the stub, not real Streamlit.

Run:  ./.venv/bin/python tests/check_render.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_PROBE = r"""
import json, sys
from streamlit.testing.v1 import AppTest

at = AppTest.from_file(sys.argv[1], default_timeout=120)
at.secrets["API_KEY"] = "dummy-not-a-real-secret"
at.run()

ups = at.get("file_uploader")
info = {
    "exceptions": [str(e.value) for e in at.exception],
    "uploaders": len(ups),
    "accepts_multiple": bool(ups[0].proto.multiple_files) if ups else None,
    "audios": len(at.get("audio")),
    "number_inputs": len(at.get("number_input")),
    "markdown_blobs": len(at.markdown),
}
print("__RESULT__" + json.dumps(info))
"""


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, os.path.join(ROOT, "app.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=400,
    )
    line = [l for l in proc.stdout.splitlines() if l.startswith("__RESULT__")]
    if not line:
        print("PROBE FAILED — no result emitted")
        print("--- stdout tail ---"); print(proc.stdout[-3000:])
        print("--- stderr tail ---"); print(proc.stderr[-3000:])
        return 1

    r = json.loads(line[0][len("__RESULT__"):])
    print(json.dumps(r, indent=2))
    ok = (
        not r["exceptions"]
        and r["uploaders"] == 1
        and r["accepts_multiple"] is True
    )
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
