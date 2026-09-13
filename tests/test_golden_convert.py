"""Golden-file test suite for the DOCX sample -> template conversion.

Asserts the conversion is CORRECT, not merely non-crashing:
  - template compiles and renders
  - every data value survives into the output
  - special characters (&, <, >) are not lost        [D11]
  - client row formatting is inherited                [D6]
  - the client logo/header survives                   [real sample]
  - narrow / odd-column tables do not silently drop data [D3]

Run:  ./.venv/bin/python -m pytest tests/ -q
"""
import base64
import copy
import io
import json
import os
import re
import sys
import zipfile

import pytest
from docx import Document
from docx.oxml.ns import qn
from docxtpl import DocxTemplate

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # local ststub.py — tests are self-contained
sys.path.insert(0, ROOT)

import ststub  # noqa: E402

ststub.install()

import app  # noqa: E402

# The real client sample is PRIVATE and deliberately not committed. When it is
# absent (fresh clone / CI), fall back to a synthetic sample with the same
# structure so the suite always runs.
REAL_SAMPLE = os.path.join(ROOT, "samples", "sample_real.docx")
SYNTHETIC_SAMPLE = os.path.join(HERE, "sample_synthetic.docx")


def _ensure_sample():
    if os.path.exists(REAL_SAMPLE):
        return REAL_SAMPLE
    if not os.path.exists(SYNTHETIC_SAMPLE):
        import make_synthetic_sample

        make_synthetic_sample.build(SYNTHETIC_SAMPLE)
    return SYNTHETIC_SAMPLE


SAMPLE = _ensure_sample()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def load_real():
    """Load the sample under test (private real one, else the synthetic stand-in)."""
    with open(SAMPLE, "rb") as f:
        return f.read()


def pid_of(text_fragment, sample=None):
    """Find a paragraph's p_id by its TEXT, not by index.

    Plans are keyed by p_id, and the real sample (gitignored) has different
    paragraph indices from the synthetic stand-in. Hardcoding 'p_7' therefore
    makes a test pass on one sample and fail on the other. Look the id up from the
    text instead so the suite is sample-agnostic and runs on a fresh clone.
    """
    with open(sample or SAMPLE, "rb") as f:
        doc = Document(io.BytesIO(f.read()))
    for i, p in enumerate(doc.paragraphs):
        if text_fragment.lower() in p.text.lower():
            return f"p_{i}"
    raise AssertionError(f"no paragraph containing {text_fragment!r} in {sample or SAMPLE}")


def render(template_bytes, ctx, autoescape=True):
    tpl = DocxTemplate(io.BytesIO(template_bytes))
    tpl.render(ctx, autoescape=autoescape)
    out = io.BytesIO()
    tpl.save(out)
    return out.getvalue()


def cells(doc, ti):
    return [[c.text for c in r.cells] for r in doc.tables[ti].rows]


def all_text(doc):
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for r in t.rows:
            for c in r.cells:
                parts.append(c.text)
    return "\n".join(parts)


def media_parts(docx_bytes):
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        return [n for n in z.namelist() if n.startswith("word/media/")]


def _ctx_for_render():
    """A complete render context for every variable the app's templates use."""
    return {
        "title": "T", "date": "D", "meeting_time": "MT", "minute_taker": "MTK",
        "executive_summary": "S",
        "agenda_and_decisions": [
            {"topic": "Topic", "discussion_summary": "Disc", "decisions_made": ["D1"]},
        ],
        "attendees": [{"name": "A", "designation": "CEO"}],
        "action_items": [
            {"task": "Task", "owner": "Owner", "department": "Dept",
             "deadline": "TBD", "remarks": ""},
        ],
        "detected_speakers": [{"speaker_id": "S1", "inferred_name": "A"}],
        "transcript": [
            {"speaker": "A", "timestamp": "00:01",
             "original_text": "orig", "translated_text": "trans"},
        ],
        "next_meeting_date": "NMD", "next_meeting_time": "NMT",
        "next_meeting_agenda_focus": "NMAF", "closing_remarks": "CR",
    }


def body_xml(docx_bytes):
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        return z.read("word/document.xml").decode("utf-8", "replace")


def make_synthetic(cols_attendees=3, cols_action=6, rows_attendees=3, rows_action=4,
                   shading=True, header="Agenda Items"):
    """Build a synthetic sample in the same shape as a real minutes doc."""
    doc = Document()
    doc.add_paragraph("Meeting Title: SAMPLE TITLE\nDate: 01/01/2026\n"
                      "Time: 09:00\nMinute Taker: SAMPLE TAKER")
    doc.add_paragraph("ATTENDEES")
    ta = doc.add_table(rows=1, cols=cols_attendees)
    ta.style = "Table Grid"
    for i, c in enumerate(ta.rows[0].cells):
        c.text = ["Sr.#", "Name", "Designation"][i] if i < 3 else f"H{i}"
    for n in range(rows_attendees):
        r = ta.add_row()
        r.cells[0].text = str(n + 1)
        r.cells[1].text = f"SAMPLE PERSON {n}"
        if cols_attendees > 2:
            r.cells[2].text = "SAMPLE ROLE"
        if shading and cols_attendees > 2:
            tcPr = r.cells[1]._tc.get_or_add_tcPr()
            shd = tcPr.makeelement(qn("w:shd"), {})
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), "D9E2F3")
            tcPr.append(shd)

    doc.add_paragraph("Agenda Points:")
    tb = doc.add_table(rows=1, cols=cols_action)
    tb.style = "Table Grid"
    labels = ["Sr.#", header, "AP", "Department", "Dead line", "Remarks"]
    for i, c in enumerate(tb.rows[0].cells):
        c.text = labels[i] if i < len(labels) else f"H{i}"
    for n in range(rows_action):
        r = tb.add_row()
        for i in range(cols_action):
            r.cells[i].text = f"sample{n}c{i}"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class FakeStatus:
    def info(self, *a, **k):
        pass

    def success(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def convert(sample_bytes, plan):
    """Run the real conversion path with the LLM stubbed by `plan`."""
    import json as _json
    orig = app.call_llm_json
    app.call_llm_json = lambda *a, **k: _json.dumps(plan)
    try:
        return app.generate_template_from_sample_ai(
            sample_bytes=sample_bytes, base_url="https://x.invalid/v1",
            api_key="k", model_name="stub", status_container=None).getvalue()
    finally:
        app.call_llm_json = orig


# ---------------------------------------------------------------------------
# D1 — the conversion must not crash
# ---------------------------------------------------------------------------
def test_real_sample_converts_without_crashing():
    """D1: was TemplateSyntaxError 'unknown tag endfor'."""
    plan = {
        "paragraphs": [{"p_id": f"p_{i}", "action": "KEEP_STATIC",
                        "cleaned_template_text": ""} for i in range(18)],
        "tables": [
            {"t_id": "t_0", "table_type": "ATTENDEES_TABLE", "header_rows_count": 1},
            {"t_id": "t_1", "table_type": "ACTION_ITEMS_TABLE", "header_rows_count": 1},
        ],
    }
    tpl = convert(load_real(), plan)
    assert tpl, "conversion returned nothing"
    # template must compile
    DocxTemplate(io.BytesIO(tpl))


# ---------------------------------------------------------------------------
# D11 — special characters must survive rendering
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", [
    "Org & Partner Ltd Registration",
    "A & B",
    "less < than and greater > than",
    'quotes "double" and \'single\'',
    "5 > 3 & 2 < 4",
])
def test_special_characters_survive_render(value):
    """D11: & < > were silently DELETED from user data."""
    doc = Document()
    doc.add_paragraph("Task: {{ item.task }}")
    buf = io.BytesIO()
    doc.save(buf)
    out = render(buf.getvalue(), {"item": {"task": value}})
    text = all_text(Document(io.BytesIO(out)))
    assert value in text, f"data lost: {value!r} not in {text!r}"


def test_ampersand_from_real_sample_content_survives():
    """D11 on real-world content: an organisation name with an embedded '&'."""
    tpl = Document()
    tpl.add_paragraph("{{ item.task }}")
    buf = io.BytesIO()
    tpl.save(buf)
    out = render(buf.getvalue(), {
        "item": {"task": "Org & Partner Ltd Registration"}})
    assert "Org & Partner" in all_text(Document(io.BytesIO(out)))


# ---------------------------------------------------------------------------
# D3 — narrow / unusual tables must not silently drop data
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cols", [1, 2, 3, 4, 5, 6, 7])
def test_action_table_any_column_count_carries_data(cols):
    """D3: tables with <4 cols produced a blank row and no error."""
    sample = make_synthetic(cols_action=cols)
    plan = {
        "paragraphs": [],
        "tables": [{"t_id": "t_0", "table_type": "ACTION_ITEMS_TABLE",
                    "header_rows_count": 1}],
    }
    tpl = convert(sample, plan)
    tpl_doc = Document(io.BytesIO(tpl))
    joined = all_text(tpl_doc)
    assert "action" in joined or "item" in joined, \
        f"no action-item tags emitted for {cols}-col table; got {joined!r}"


# ---------------------------------------------------------------------------
# D5 — STATIC_TABLE must not leave sample dummy rows behind
# ---------------------------------------------------------------------------
def test_static_table_sample_rows_do_not_leak():
    """D5: sample dummy rows shipped into every generated document."""
    sample = make_synthetic()
    plan = {
        "paragraphs": [],
        "tables": [{"t_id": "t_0", "table_type": "STATIC_TABLE",
                    "header_rows_count": 1}],
    }
    tpl = convert(sample, plan)
    out = render(tpl, {"attendees": [], "action_items": []})
    text = all_text(Document(io.BytesIO(out)))
    assert "SAMPLE PERSON" not in text, "sample dummy row leaked into output"


# ---------------------------------------------------------------------------
# D6 — client formatting must be inherited by generated rows
# ---------------------------------------------------------------------------
def test_generated_rows_inherit_sample_row_formatting():
    """D6: delete-rows + add_row() lost shading/borders."""
    sample = make_synthetic(shading=True, rows_attendees=3)
    plan = {
        "paragraphs": [],
        "tables": [{"t_id": "t_0", "table_type": "ATTENDEES_TABLE",
                    "header_rows_count": 1}],
    }
    tpl = convert(sample, plan)
    doc = Document(io.BytesIO(tpl))
    tbl = doc.tables[0]

    def shaded(tbl_):
        for r in tbl_.rows[1:]:
            for c in r.cells:
                tcPr = c._tc.find(qn("w:tcPr"))
                if tcPr is not None and tcPr.find(qn("w:shd")) is not None:
                    return True
        return False

    assert shaded(tbl), "generated rows lost the sample's cell shading"


# ---------------------------------------------------------------------------
# Real sample: logo / header survival
# ---------------------------------------------------------------------------
def test_real_sample_logo_survives_conversion():
    """The client's header logo must not be dropped."""
    before = media_parts(load_real())
    plan = {
        "paragraphs": [{"p_id": f"p_{i}", "action": "KEEP_STATIC",
                        "cleaned_template_text": ""} for i in range(18)],
        "tables": [
            {"t_id": "t_0", "table_type": "ATTENDEES_TABLE", "header_rows_count": 1},
            {"t_id": "t_1", "table_type": "ACTION_ITEMS_TABLE", "header_rows_count": 1},
        ],
    }
    tpl = convert(load_real(), plan)
    assert media_parts(tpl) == before, "logo/media parts changed during conversion"


def test_real_sample_keeps_header_parts():
    """Conversion must not add or drop header/footer parts."""
    plan = {"paragraphs": [], "tables": []}
    before = sorted(
        n for n in zipfile.ZipFile(io.BytesIO(load_real())).namelist()
        if re.match(r"word/(header|footer)\d*\.xml$", n)
    )
    tpl = convert(load_real(), plan)
    after = sorted(
        n for n in zipfile.ZipFile(io.BytesIO(tpl)).namelist()
        if re.match(r"word/(header|footer)\d*\.xml$", n)
    )
    assert after == before, f"header/footer parts changed: {before} -> {after}"
    assert after, "sample has no header/footer parts to preserve"


# ---------------------------------------------------------------------------
# D4 — column mapping must be semantic, not positional
# ---------------------------------------------------------------------------
def test_real_sample_action_columns_map_semantically():
    """D4: 'Action Point' received owner, 'Responsible' received department."""
    plan = {
        "paragraphs": [],
        "tables": [{"t_id": "t_1", "table_type": "ACTION_ITEMS_TABLE",
                    "header_rows_count": 1}],
    }
    tpl = convert(load_real(), plan)
    xml = body_xml(tpl)
    doc = Document(io.BytesIO(tpl))
    # the plan only converts t_1, so the untouched t_0 remains first; find the
    # table that actually carries placeholders rather than assuming an index.
    tpl_table = None
    for t in doc.tables:
        if any("{{" in c.text for r in t.rows for c in r.cells):
            tpl_table = t
            break
    assert tpl_table is not None, "no table received template placeholders"
    hdr = [c.text.strip() for c in tpl_table.rows[0].cells]
    # locate the template row (the one containing placeholders)
    tpl_row = None
    for r in tpl_table.rows:
        if any("{{" in c.text for c in r.cells):
            tpl_row = [c.text for c in r.cells]
            break
    assert tpl_row is not None, "no template row emitted"

    # 'AP' = the ASSIGNED PERSON in this client's minutes (confirmed by the client),
    # NOT "action point". It must therefore map to `owner`.
    idx_ap = hdr.index("AP") if "AP" in hdr else None
    if idx_ap is not None:
        assert "owner" in tpl_row[idx_ap], (
            f"AP column got {tpl_row[idx_ap]!r}, expected the owner variable. "
            f"headers={hdr} row={tpl_row}")
        assert "task" not in tpl_row[idx_ap], (
            f"AP must not map to task: {tpl_row[idx_ap]!r}")
    # 'Agenda Items' carries the work description -> item.task (it used to map to
    # `item.topic`, a field ActionItem does not have, so the column rendered BLANK)
    idx_ag = hdr.index("Agenda Items") if "Agenda Items" in hdr else None
    if idx_ag is not None:
        assert "task" in tpl_row[idx_ag], (
            f"Agenda Items column got {tpl_row[idx_ag]!r}, expected the task variable. "
            f"headers={hdr} row={tpl_row}")
    # 'Remarks' column -> remarks variable
    idx_rem = hdr.index("Remarks") if "Remarks" in hdr else None
    if idx_rem is not None:
        assert "remarks" in tpl_row[idx_rem], (
            f"Remarks column got {tpl_row[idx_rem]!r}. headers={hdr} row={tpl_row}")


# ---------------------------------------------------------------------------
# AP = assigned person (client-confirmed), exact-match only
# ---------------------------------------------------------------------------
def test_ap_header_maps_to_owner():
    """The client's 'AP' column holds the ASSIGNED PERSON, so it must map to
    `owner` — not to `task` ('action point'), which is how the code once read it
    and which put a person's name in the work column."""
    assert app.map_header_to_variable("AP", "ACTION_ITEMS_TABLE") == "owner"
    assert app.map_header_to_variable("ap", "ACTION_ITEMS_TABLE") == "owner"
    assert app.map_header_to_variable("AP.", "ACTION_ITEMS_TABLE") == "owner"


def test_ap_keyword_does_not_capture_unrelated_headers():
    """'ap' is a dangerous 2-letter key: it must never match by prefix/substring,
    or headers like 'Approved' would be filled with the owner variable."""
    for h in ("Approved", "Application", "App", "Approval Status", "Capability"):
        got = app.map_header_to_variable(h, "ACTION_ITEMS_TABLE")
        assert got != "owner", f"{h!r} wrongly mapped to owner (got {got!r})"


def test_agenda_items_maps_to_task_not_dead_field():
    """'Agenda Items' carries the work -> item.task.

    It previously mapped to `item.topic`, a field ActionItem does not define, so
    the column rendered EMPTY with no error. Guard against that regression: the
    mapped variable must be a real, non-empty ActionItem field.
    """
    field = app.map_header_to_variable("Agenda Items", "ACTION_ITEMS_TABLE")
    assert field == "task"
    assert field in app.ActionItem.model_fields, (
        f"mapped field {field!r} is not a real ActionItem field "
        f"({sorted(app.ActionItem.model_fields)})")


def test_every_mappable_field_exists_on_its_model():
    """No header may map to a field the render context does not actually provide,
    which is how a column silently renders blank."""
    attendee_fields = set(app.Attendee.model_fields)
    item_fields = set(app.ActionItem.model_fields)
    samples = {
        "ATTENDEES_TABLE": (["Sr.#", "Name", "Designation"], attendee_fields),
        "ACTION_ITEMS_TABLE": (
            ["Sr.#", "Agenda Items", "AP", "Department", "Deadline", "Remarks"],
            item_fields),
    }
    for table_type, (headers, model_fields) in samples.items():
        for h in headers:
            f = app.map_header_to_variable(h, table_type)
            if f in (None, "@index"):
                continue
            assert f in model_fields, (
                f"header {h!r} maps to {f!r}, which is not a field of the model "
                f"used to render that table ({sorted(model_fields)})")


def test_model_purge_on_placeholder_is_recovered():
    """The live model also returned PURGE for this paragraph (same input, different
    run). PURGE deletes the section permanently — that is the reported bug, so the
    override must fire for PURGE too, not just KEEP_STATIC."""
    plan = {
        "paragraphs": [
            {"p_id": pid_of("ACTION ITEMS"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
            {"p_id": pid_of("Action Items Table goes here"), "action": "PURGE", "cleaned_template_text": ""},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    DocxTemplate(io.BytesIO(tpl))
    paras = [p.text for p in Document(io.BytesIO(tpl)).paragraphs]

    assert "{%p for item in action_items %}" in paras, (
        f"PURGE on the ACTION ITEMS placeholder still deleted the section. "
        f"paragraphs={paras!r}")
    assert "{%p endfor %}" in paras, paras
    assert "<Action Items Table goes here>" not in paras, paras

    ctx = _ctx_for_render()
    ctx["action_items"] = [
        {"task": "Chairs repaired", "owner": "Person A",
         "department": "Admin", "deadline": "13 Sep", "remarks": ""},
    ]
    out = all_text(Document(io.BytesIO(render(tpl, ctx))))
    assert "1. Chairs repaired — Person A" in out, out


def test_model_keep_static_on_placeholder_is_recovered():
    """Real-world case: the live model classified the ACTION ITEMS placeholder as
    KEEP_STATIC, so the client's placeholder text would ship into every generated
    document and the section would never be filled. The assembler must detect it and
    build the loop anyway."""
    plan = {
        "paragraphs": [
            {"p_id": pid_of("ACTION ITEMS"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
            {"p_id": pid_of("Action Items Table goes here"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    DocxTemplate(io.BytesIO(tpl))
    paras = [p.text for p in Document(io.BytesIO(tpl)).paragraphs]

    assert "{%p for item in action_items %}" in paras, (
        f"placeholder under the ACTION ITEMS heading was not recovered. "
        f"paragraphs={paras!r}")
    assert "{%p endfor %}" in paras, paras
    assert "<Action Items Table goes here>" not in paras, (
        "placeholder text still present in the template")

    ctx = _ctx_for_render()
    ctx["action_items"] = [
        {"task": "Chairs repaired", "owner": "Person A",
         "department": "Admin", "deadline": "13 Sep", "remarks": ""},
    ]
    out = all_text(Document(io.BytesIO(render(tpl, ctx))))
    assert "1. Chairs repaired — Person A" in out, out
    assert "<Action Items Table goes here>" not in out, out


def test_placeholder_without_known_heading_is_removed_not_shipped():
    """An unresolvable placeholder must never be printed into client output."""
    plan = {
        "paragraphs": [
            {"p_id": pid_of("Action Items Table goes here"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    DocxTemplate(io.BytesIO(tpl))
    paras = [p.text for p in Document(io.BytesIO(tpl)).paragraphs]
    assert "<Action Items Table goes here>" not in paras, paras


def test_edit_box_placeholder_resolves_by_heading_text():
    """A differently-worded placeholder ('<<insert agenda here>>') must resolve via
    its heading wording."""
    plan = {
        "paragraphs": [
            {"p_id": pid_of("DIARIZED TRANSCRIPT"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
            {"p_id": pid_of("transcript with translation text goes here"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    DocxTemplate(io.BytesIO(tpl))
    paras = [p.text for p in Document(io.BytesIO(tpl)).paragraphs]
    assert "{%p for entry in transcript %}" in paras, paras
    assert "{%p endfor %}" in paras, paras


def test_model_keep_static_on_real_dummy_text_is_preserved():
    """Regression guard: the placeholder detector must not swallow ordinary text."""
    plan = {
        "paragraphs": [
            {"p_id": pid_of("Discussion/update on the tasks assigned"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    paras = [p.text for p in Document(io.BytesIO(tpl)).paragraphs]
    assert "Discussion/update on the tasks assigned;" in paras, paras
    assert "{%p for" not in "\n".join(paras)


# ---------------------------------------------------------------------------
# Transient provider errors must not kill the upload
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, payload=None, content_type="application/json"):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {"content-type": content_type}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _FakeClient:
    """Returns the queued responses in order, one per post()."""

    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        self._calls.append(url)
        idx = min(len(self._calls) - 1, len(self._responses) - 1)
        return self._responses[idx]


def _patch_client(monkeypatch, responses):
    calls = []
    monkeypatch.setattr(app.httpx, "Client",
                        lambda **kw: _FakeClient(responses, calls))
    monkeypatch.setattr(app.time, "sleep", lambda *a: None)
    return calls


def test_call_llm_json_retries_on_429(monkeypatch):
    """A transient provider 429 must be retried, not raised. The client's upload
    died with 'HTTP 429 from provider' before this: a temporary rate limit killed
    the whole conversion."""
    ok = {"choices": [{"message": {"content": '{"paragraphs": [], "tables": []}'}}]}
    calls = _patch_client(monkeypatch, [_FakeResponse(429), _FakeResponse(200, ok)])
    out = app.call_llm_json("https://x.invalid/v1", "k", "m", "p")
    assert json.loads(out) == {"paragraphs": [], "tables": []}
    assert len(calls) == 2, f"should have retried once, made {len(calls)} call(s)"


def test_call_llm_json_retries_on_5xx(monkeypatch):
    ok = {"choices": [{"message": {"content": "{}"}}]}
    calls = _patch_client(monkeypatch,
                          [_FakeResponse(503), _FakeResponse(502), _FakeResponse(200, ok)])
    app.call_llm_json("https://x.invalid/v1", "k", "m", "p")
    assert len(calls) == 3


def test_call_llm_json_gives_up_after_max_attempts(monkeypatch):
    calls = _patch_client(monkeypatch, [_FakeResponse(429)])
    with pytest.raises(RuntimeError) as e:
        app.call_llm_json("https://x.invalid/v1", "k", "m", "p")
    assert "429" in str(e.value)
    assert len(calls) <= 4, f"retried too many times: {len(calls)}"


def test_call_llm_json_does_not_retry_client_errors(monkeypatch):
    """401 (bad key) / 400 (bad request) are permanent — retrying wastes time and
    hides the real problem."""
    calls = _patch_client(monkeypatch, [_FakeResponse(401, {"error": "bad key"})])
    with pytest.raises(RuntimeError) as e:
        app.call_llm_json("https://x.invalid/v1", "k", "m", "p")
    assert "401" in str(e.value)
    assert len(calls) == 1, f"401 must not be retried, made {len(calls)} call(s)"


def test_call_llm_json_retries_when_provider_streams_html(monkeypatch):
    """9router returns text/event-stream unless stream:false — a non-JSON body is
    transient noise, so retry it rather than failing the upload."""
    ok = {"choices": [{"message": {"content": "{}"}}]}
    calls = _patch_client(
        monkeypatch,
        [_FakeResponse(200, {}, content_type="text/event-stream"), _FakeResponse(200, ok)],
    )
    app.call_llm_json("https://x.invalid/v1", "k", "m", "p")
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# End-to-end on the real sample: full data must round-trip
# ---------------------------------------------------------------------------
def test_real_sample_end_to_end_roundtrip():
    plan = {
        "paragraphs": [
            {"p_id": "p_0", "action": "REPLACE_TEMPLATE",
             "cleaned_template_text": ("Meeting Title: {{ title }}\nDate: {{ date }}\n"
                                       "Time: {{ meeting_time }}\n"
                                       "Minute Taker: {{ minute_taker }}")},
        ] + [{"p_id": f"p_{i}", "action": "KEEP_STATIC",
              "cleaned_template_text": ""} for i in range(1, 18)],
        "tables": [
            {"t_id": "t_0", "table_type": "ATTENDEES_TABLE", "header_rows_count": 1},
            {"t_id": "t_1", "table_type": "ACTION_ITEMS_TABLE", "header_rows_count": 1},
        ],
    }
    tpl = convert(load_real(), plan)
    ctx = {
        "title": "Q4 Policy Review & Planning",
        "date": "12/09/2026",
        "meeting_time": "10:00AM - 12:50PM",
        "minute_taker": "Person A & Person B",
        "attendees": [{"name": "Person C", "designation": "CEO"},
                      {"name": "Person D", "designation": "Director"}],
        "action_items": [
            {"task": "Org & Partner Ltd Registration",
             "owner": "Person A", "department": "Admin",
             "deadline": "13 Sep", "remarks": "urgent"},
            {"task": "Acquire proposals", "owner": "Person C",
             "department": "BD", "deadline": "TBD", "remarks": ""},
        ],
        "next_meeting_date": "18/09/2026",
        "next_meeting_time": "10:00 A.M",
        "next_meeting_agenda_focus": "Website",
        "closing_remarks": "Adjourned at 12:30PM",
    }
    out = render(tpl, ctx)
    text = all_text(Document(io.BytesIO(out)))

    assert "Q4 Policy Review & Planning" in text
    assert "Org & Partner Ltd Registration" in text, "& lost in real round-trip"
    assert "Person C" in text
    assert "Person D" in text
    assert media_parts(out) == media_parts(load_real())


# ---------------------------------------------------------------------------
# Collection SECTIONS in paragraphs (not tables)
#
# The client's minutes carry two extra sections whose content is a LIST, written
# as a placeholder paragraph rather than a table:
#
#     ACTION ITEMS
#     <Action Items Table goes here>
#     ...
#     COMPLETE DIARIZED TRANSCRIPT OF MEETING WITH TRANSLATION:
#     The complete transcript with translation text goes here
#
# The schema could only express KEEP_STATIC / REPLACE_TEMPLATE / PURGE, so the
# model classified each placeholder as PURGE (indistinguishable from dummy text)
# and DELETED it. The headings survived, so the template looked plausible while
# silently dropping both whole sections.
# ---------------------------------------------------------------------------
BODY_AI = ("{{ loop.index }}. {{ item.task }} — {{ item.owner }}"
           " ({{ item.department }}, due {{ item.deadline }})")

BODY_TX = ("[{{ entry.timestamp }}] {{ entry.speaker }}: "
           "{{ entry.original_text }} ({{ entry.translated_text }})")


def test_placeholder_paragraph_becomes_collection_loop():
    """D14: a placeholder paragraph for a known collection must become a {%p for %}
    loop over that collection, not be purged."""
    plan = {
        "paragraphs": [
            {"p_id": pid_of("ACTION ITEMS"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
            {"p_id": pid_of("Action Items Table goes here"), "action": "REPLACE_TEMPLATE",
             "collection": "action_items",
             "cleaned_template_text": BODY_AI},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    DocxTemplate(io.BytesIO(tpl))          # must compile
    doc = Document(io.BytesIO(tpl))
    paras = [p.text for p in doc.paragraphs]

    assert "{%p for item in action_items %}" in paras, (
        f"no paragraph loop emitted for action_items. paragraphs={paras!r}")
    assert "{%p endfor %}" in paras, "no closing paragraph loop tag"
    # The tag paragraphs must be the tags ALONE — docxtpl only expands `{%p %}`
    # when it is the entire paragraph, and any leftover placeholder text would
    # otherwise be printed into every generated document.
    assert BODY_AI in paras, (
        f"loop body paragraph is not exactly the item text. paras={paras!r}")
    for t in ("{%p for item in action_items %}", "{%p endfor %}"):
        assert t in paras, f"{t!r} not alone in its own paragraph: {paras!r}"

    # and it must actually render one line per item
    ctx = _ctx_for_render()
    ctx["action_items"] = [
        {"task": "Chairs repaired", "owner": "Person A",
         "department": "Admin", "deadline": "13 Sep", "remarks": ""},
        {"task": "Quotations", "owner": "Person B",
         "department": "Admin", "deadline": "TBD", "remarks": ""},
    ]
    out = all_text(Document(io.BytesIO(render(tpl, ctx))))
    assert "1. Chairs repaired — Person A (Admin, due 13 Sep)" in out, out
    assert "2. Quotations — Person B (Admin, due TBD)" in out, out
    # The placeholder text must NOT survive anywhere.
    assert "<Action Items Table goes here>" not in out, (
        f"placeholder text leaked into output: {out!r}")


def test_transcript_placeholder_becomes_loop():
    """The transcript section must likewise become a loop over `transcript`."""
    plan = {
        "paragraphs": [
            {"p_id": pid_of("DIARIZED TRANSCRIPT"), "action": "KEEP_STATIC", "cleaned_template_text": ""},
            {"p_id": pid_of("transcript with translation text goes here"), "action": "REPLACE_TEMPLATE",
             "collection": "transcript",
             "cleaned_template_text": BODY_TX},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    DocxTemplate(io.BytesIO(tpl))
    doc = Document(io.BytesIO(tpl))
    paras = [p.text for p in doc.paragraphs]
    assert "{%p for entry in transcript %}" in paras, paras
    assert "{%p endfor %}" in paras, paras
    assert BODY_TX in paras, f"loop body paragraph is not exact: {paras!r}"

    ctx = _ctx_for_render()
    ctx["transcript"] = [
        {"speaker": "Person C", "timestamp": "00:12",
         "original_text": "Opening remarks", "translated_text": "Opening remarks"},
    ]
    out = all_text(Document(io.BytesIO(render(tpl, ctx))))
    assert "[00:12] Person C: Opening remarks (Opening remarks)" in out, out
    assert "The complete transcript with translation text goes here" not in out, (
        f"placeholder text leaked into output: {out!r}")


def test_unknown_collection_is_not_looped():
    """A bogus collection name must be rejected loudly rather than emitting a
    loop that would crash on render (or silently produce nothing)."""
    plan = {
        "paragraphs": [
            {"p_id": "p_7", "action": "REPLACE_TEMPLATE",
             "collection": "definitely_not_a_real_collection",
             "cleaned_template_text": "{{ x.y }}"},
        ],
        "tables": [],
    }
    try:
        tpl = convert(load_real(), plan)
    except ValueError as e:
        assert "collection" in str(e).lower(), e
        return
    # If it did not raise, the template must at least still compile.
    DocxTemplate(io.BytesIO(tpl))
    body = "\n".join(p.text for p in Document(io.BytesIO(tpl)).paragraphs)
    assert "{%p for" not in body, f"emitted a loop for an unknown collection: {body!r}"


def test_loop_body_uses_matching_variable():
    """The body must use the variable name from the for-tag, or docxtpl renders
    empty strings with no error."""
    plan = {
        "paragraphs": [
            {"p_id": "p_7", "action": "REPLACE_TEMPLATE",
             "collection": "action_items",
             "cleaned_template_text": "{{ item.task }}"},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    body = "\n".join(p.text for p in Document(io.BytesIO(tpl)).paragraphs)
    m = re.search(r"\{%p for (\w+) in action_items %\}", body)
    assert m, body
    var = m.group(1)
    assert "{{ item.task }}" in body, body
    # the tag's own variable must be a known alias for that collection
    assert var in ("item", "action_item", "a"), (
        f"loop variable {var!r} will not match '{{{{ item.task }}}}' in the body")


def test_purged_and_static_paragraphs_still_work():
    """Regression: adding collection support must not break PURGE / KEEP_STATIC /
    plain REPLACE_TEMPLATE on ORDINARY paragraphs.

    Note: a section placeholder is deliberately exempt from PURGE (that override is
    the whole point of the fix), so this checks ordinary paragraphs only.
    """
    plan = {
        "paragraphs": [
            {"p_id": pid_of("Discussion/update on the tasks assigned"), "action": "PURGE"},
            {"p_id": pid_of("Agenda Points:"), "action": "KEEP_STATIC",
             "cleaned_template_text": ""},
            {"p_id": pid_of("CLOSING"), "action": "REPLACE_TEMPLATE",
             "cleaned_template_text": "CLOSING\n{{ closing_remarks }}"},
        ],
        "tables": [],
    }
    tpl = convert(load_real(), plan)
    DocxTemplate(io.BytesIO(tpl))
    paras = [p.text for p in Document(io.BytesIO(tpl)).paragraphs]
    body = "\n".join(paras)
    assert "Discussion/update on the tasks assigned;" not in body, "PURGE ignored"
    assert "Agenda Points:" in body, "KEEP_STATIC paragraph lost"
    assert "{{ closing_remarks }}" in body, "plain REPLACE_TEMPLATE lost"
    assert "{%p for" not in body, (
        f"an unexpected loop was emitted for ordinary paragraphs: {paras!r}")



# ---------------------------------------------------------------------------
# Brand assets
# ---------------------------------------------------------------------------
def test_brand_logo_is_embedded_as_a_data_uri():
    """The header mark must inline as a data-URI so it cannot 404 on Cloud."""
    uri = app._brand_logo_uri()
    assert uri.startswith("data:image/png;base64,"), uri[:40]
    raw = base64.b64decode(uri.split(",", 1)[1])
    assert len(raw) > 1000, "logo payload suspiciously small"
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"


def test_brand_logo_returns_empty_string_when_asset_missing(monkeypatch):
    """A missing asset must degrade to the text title, not crash the app."""
    import builtins
    real_open = builtins.open

    def boom(path, *a, **k):
        if str(path).endswith("aima_lockup.png"):
            raise OSError("simulated missing asset")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", boom)
    assert app._brand_logo_uri() == ""


def test_brand_assets_are_transparent_and_not_white_boxed():
    """Guards the un-blend step: a baked off-white box would show as a grey tile
    against the page. Corners must be fully transparent."""
    from PIL import Image

    p = os.path.join(ROOT, "assets", "aima_lockup.png")
    assert os.path.exists(p), p
    with Image.open(p).convert("RGBA") as im:
        a = im.load()
        for corner in ((0, 0), (im.width - 1, 0), (0, im.height - 1),
                       (im.width - 1, im.height - 1)):
            assert a[corner][3] == 0, (corner, a[corner])


def test_favicon_is_square():
    """Streamlit needs a square-ish icon; a wide lockup gets letterboxed."""
    from PIL import Image

    fav = os.path.join(ROOT, "assets", "aima_favicon.png")
    assert os.path.exists(fav), fav
    with Image.open(fav) as im:
        assert im.width == im.height, im.size
        assert im.width >= 128, im.size
