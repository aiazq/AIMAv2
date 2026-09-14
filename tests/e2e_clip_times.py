#!/usr/bin/env python
"""End-to-end on the REAL fixture: clip_report.json + its 61 s recording.

Synthetic reports prove the arithmetic; only this proves the actual deliverable.
Renders the DOCX and the template context and looks for an expected clock time.
"""
import datetime
import io
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ststub  # noqa: E402
ststub.install()
import app  # noqa: E402
import timeline  # noqa: E402

fx = json.load(open(os.path.join(ROOT, "private_fixtures", "clip_report.json")))
rep = app.MeetingMinutesReport.model_validate(fx)

print("BEFORE")
print("  meeting_time :", repr(rep.meeting_time))
print("  date         :", repr(rep.date))
print("  offsets      :", [e.timestamp for e in rep.transcript])

raw = open(os.path.join(ROOT, "private_fixtures", "meeting_clip_1min.ogg"), "rb").read()
dur = timeline.probe_duration(raw, ".ogg")
print("  measured dur :", dur, "s")

start = datetime.time(10, 0)
out = app.apply_datetime_overrides(rep, None, start, duration_seconds=dur)

print("\nAFTER  (start=10:00, real duration)")
print("  meeting_time :", repr(out.meeting_time))
print("  clock times  :", [e.timestamp for e in out.transcript])

# The downloadable document
docx = app.build_default_docx(out)
z = zipfile.ZipFile(io.BytesIO(docx.getvalue()))
xml = z.read("word/document.xml").decode("utf-8")
import re
text = re.sub(r"<[^>]+>", "", xml.replace("</w:p>", "\n"))

print("\nDOWNLOADABLE DOCX")
hits = [t for t in [e.timestamp for e in out.transcript] if t in text]
print("  clock times present:", hits)
print("  raw offsets present:", [t for t in ("[00:21]", "[00:46]") if t in text])

# Template context (custom-template path)
ctx = app._build_render_context(out.model_dump())
print("\nTEMPLATE CONTEXT")
print("  transcript[1].timestamp:", ctx["transcript"][1]["timestamp"])

# JSON export
print("\nJSON EXPORT")
print("  transcript[2].timestamp:", out.model_dump()["transcript"][2]["timestamp"])
print("  private attr leaked  :", "_source_timestamp" in json.dumps(out.model_dump()))

# Idempotency: save again at a new time
again = app.apply_datetime_overrides(out, None, datetime.time(11, 30), duration_seconds=dur)
print("\nRE-ANCHOR at 11:30 (idempotency)")
print("  clock times  :", [e.timestamp for e in again.transcript])

ok = (
    all(t in text for t in [e.timestamp for e in out.transcript])
    and not any(t in text for t in ("[00:21]", "[00:46]"))
    and ctx["transcript"][1]["timestamp"] == out.transcript[1].timestamp
    and again.transcript[1].timestamp == "11:30:21"
    and "_source_timestamp" not in json.dumps(out.model_dump())
)
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
