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
import copy
import io
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
    "Sarhad Chamber & ISO 14001 Registration",
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
    """D11 on actual client content: the ISO 14001 row."""
    tpl = Document()
    tpl.add_paragraph("{{ item.task }}")
    buf = io.BytesIO()
    tpl.save(buf)
    out = render(buf.getvalue(), {
        "item": {"task": "Sarhad Chamber & ISO 14001 Registration"}})
    assert "Chamber & ISO" in all_text(Document(io.BytesIO(out)))


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
        "minute_taker": "Ali & Nayab",
        "attendees": [{"name": "Saadat Khattak", "designation": "CEO"},
                      {"name": "Wasim Kakakhel", "designation": "Director"}],
        "action_items": [
            {"task": "Sarhad Chamber & ISO 14001 Registration",
             "owner": "Nayab", "department": "Admin",
             "deadline": "13 Sep", "remarks": "urgent"},
            {"task": "Acquire proposals", "owner": "Saadat sb",
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
    assert "Sarhad Chamber & ISO 14001 Registration" in text, "& lost in real round-trip"
    assert "Saadat Khattak" in text
    assert "Wasim Kakakhel" in text
    assert media_parts(out) == media_parts(load_real())
