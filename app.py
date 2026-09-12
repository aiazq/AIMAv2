import base64
from concurrent.futures import ThreadPoolExecutor
import datetime
import io
import json
import os
import random
import time
import httpx
import streamlit as st
from docx import Document
from docxtpl import DocxTemplate
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Configuration & Styling
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="AIMA — AI Meeting Assistant",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 3.2rem !important;
        padding-bottom: 2rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        max-width: 100% !important;
    }

    div[data-testid="stPopover"] {
        margin-top: 0.35rem;
    }

    button[aria-label="Show password text"],
    button[aria-label="Hide password text"],
    input[type="password"]::-ms-reveal,
    input[type="password"]::-ms-clear {
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
    }

    div[data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        padding: 0.5rem 0.75rem;
        border-radius: 6px;
        border: 1px solid rgba(128, 128, 128, 0.15);
    }

    /* Terminal Console Box with Native Auto-Scroll */
    .terminal-container {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        font-size: 0.82rem;
        background-color: #0b0f19;
        color: #d1d5db;
        padding: 0.85rem;
        border-radius: 8px;
        height: 320px;
        overflow-y: auto;
        border: 1px solid #1f2937;
        line-height: 1.45;
        white-space: pre-wrap;
        display: flex;
        flex-direction: column-reverse;
    }

    .log-quip {
        font-size: 0.70rem !important;
        color: #94a3b8 !important;
        font-style: italic;
        padding-left: 0.5rem;
        display: block;
        margin: 2px 0;
    }

    .log-info { color: #38bdf8; }
    .log-debug { color: #9ca3af; }
    .log-warn { color: #fbbf24; }
    .log-error { color: #f87171; }
    .log-success { color: #4ade80; }
    </style>
    """,
    unsafe_allow_html=True,
)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.6-flash"

SECRET_KEY = st.secrets.get("API_KEY", os.environ.get("API_KEY", st.secrets.get("GEMINI_API_KEY", "")))
SECRET_BASE_URL = st.secrets.get("ENDPOINT_URL", os.environ.get("ENDPOINT_URL", DEFAULT_BASE_URL))

if "api_key" not in st.session_state:
    st.session_state["api_key"] = SECRET_KEY

if "base_url" not in st.session_state:
    st.session_state["base_url"] = SECRET_BASE_URL

if "available_models" not in st.session_state:
    st.session_state["available_models"] = [DEFAULT_MODEL]

if "selected_model" not in st.session_state:
    st.session_state["selected_model"] = DEFAULT_MODEL

if "saved_template_bytes" not in st.session_state:
    st.session_state["saved_template_bytes"] = None

if "active_template_bytes" not in st.session_state:
    st.session_state["active_template_bytes"] = None

if "logs_list" not in st.session_state:
    st.session_state["logs_list"] = [
        '<span class="log-debug">[System] Ready. Awaiting audio recording...</span>'
    ]

if "usage_stats" not in st.session_state:
    st.session_state["usage_stats"] = None


# -----------------------------------------------------------------------------
# Corporate Quips
# -----------------------------------------------------------------------------
FUNNY_QUIPS = [
    "Translating 'per my last email' into diplomatic corporate prose...",
    "Detecting who spoke for 45 seconds while double-muted...",
    "Calculating how many agenda points could have been a quick Slack message...",
    "Politely ignoring the dog barking in Speaker 2's background...",
    "Extracting action items that everyone pretended not to hear...",
    "Filtering out the phrase 'Can everyone see my screen?'...",
    "Determining if 'Let's take this offline' means 'Let's never discuss this again'...",
    "Synergizing the paradigms and operationalizing low-hanging fruit...",
    "Teaching the neural net to distinguish insightful pauses from coffee sipping...",
    "Cross-referencing promises made with realistic human capabilities...",
    "Decoding corporate buzzwords into plain human English...",
    "Compiling artificial executive confidence into the summary...",
    "Reconstructing what was said right before someone's Wi-Fi dropped...",
    "Ensuring all circled backs are sufficiently aligned...",
]


# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------
class ActionItem(BaseModel):
    task: str = Field(description="Description of the action item or task.")
    owner: str = Field(description="Person, role, or team assigned to this task.")
    deadline: str = Field(description="Due date, timeframe, or 'TBD' if unspecified.")
    priority: str = Field(description="High, Medium, or Low.")


class TranscriptEntry(BaseModel):
    speaker: str = Field(description="Name or label of the speaker.")
    timestamp: str = Field(description="Approximate timestamp (e.g., '01:23') or empty string.")
    original_text: str = Field(description="Speech in original spoken language.")
    translated_text: str = Field(description="Accurate English translation (same as original if already English).")


class AgendaItem(BaseModel):
    topic: str = Field(description="Agenda topic discussed.")
    discussion_summary: str = Field(description="Summary of discussions regarding this topic.")
    decisions_made: list[str] = Field(description="Key conclusions reached.")


class DetectedSpeaker(BaseModel):
    speaker_id: str = Field(description="Unique label used in transcription, e.g., 'Speaker 1'.")
    inferred_name: str = Field(description="Inferred full or first name, or 'Unknown'.")


class MeetingMinutesReport(BaseModel):
    title: str = Field(description="Descriptive title for the meeting.")
    date: str = Field(description="Date of the meeting or 'Undated'.")
    attendees: list[str] = Field(description="Detected participants.")
    detected_speakers: list[DetectedSpeaker] = Field(
        default_factory=list,
        description="List of detected speakers and any names inferred from introductions or dialog.",
    )
    executive_summary: str = Field(description="Executive summary of the meeting in English.")
    agenda_and_decisions: list[AgendaItem] = Field(description="Topic breakdowns and decisions in English.")
    action_items: list[ActionItem] = Field(description="Action items extracted in English.")
    transcript: list[TranscriptEntry] = Field(description="Bilingual speaker-diarized transcript.")


# AI Template Transformation Schemas
class ParagraphInstruction(BaseModel):
    element_id: str = Field(description="The p_index identifier (e.g., 'p_3').")
    action: str = Field(
        description="One of: 'KEEP_STATIC' (headers, titles, corporate labels), 'REPLACE' (substitute sample value with Jinja tag), 'PURGE' (delete dummy sample text entirely)."
    )
    jinja_tag: str = Field(
        default="",
        description="Exact Jinja2 placeholder if action is 'REPLACE'. E.g. '{{ executive_summary }}', 'Date: {{ date }}', 'Attendees: {% for a in attendees %}{{ a }}{% if not loop.last %}, {% endif %}{% endfor %}'",
    )


class TableInstruction(BaseModel):
    table_id: str = Field(description="The table_index identifier (e.g., 't_0').")
    action: str = Field(
        description="One of: 'KEEP_STATIC', 'ACTION_ITEMS_LOOP', 'AGENDA_LOOP', 'IGNORE'."
    )
    cell_tag_map: list[str] = Field(
        default_factory=list,
        description="Jinja expressions to populate across the newly injected row's cells (e.g. ['{%tr for item in action_items %}{{ item.task }}', '{{ item.owner }}', '{{ item.deadline }}', '{{ item.priority }}{%tr endfor %}']).",
    )


class TemplateTransformationPlan(BaseModel):
    paragraph_instructions: list[ParagraphInstruction] = Field(
        description="Specific instructions for each paragraph node."
    )
    table_instructions: list[TableInstruction] = Field(
        description="Specific instructions for each table node."
    )


# -----------------------------------------------------------------------------
# Logger
# -----------------------------------------------------------------------------
def log_event(log_container, logs_list, message: str, level: str = "INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    level_css = {
        "INFO": "log-info",
        "DEBUG": "log-debug",
        "WARN": "log-warn",
        "ERROR": "log-error",
        "SUCCESS": "log-success",
        "QUIP": "log-quip",
    }.get(level, "log-debug")

    if level == "QUIP":
        entry = f'<span class="log-quip">[{ts}] 💬 {message}</span>'
    else:
        entry = f'<span>[{ts}] <span class="{level_css}">[{level}]</span> {message}</span>'

    logs_list.append(entry)
    reversed_items = "<br>".join(reversed(logs_list))
    html_output = f'<div id="aima-terminal-box" class="terminal-container">{reversed_items}</div>'
    log_container.markdown(html_output, unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# Model Discovery & Inference Helpers
# -----------------------------------------------------------------------------
def fetch_available_models(base_url: str, api_key: str) -> list[str]:
    cleaned_base = base_url.rstrip("/")
    is_gemini = "googleapis.com" in cleaned_base

    try:
        with httpx.Client(timeout=10.0) as client:
            if is_gemini:
                resp = client.get(f"{cleaned_base}/models?key={api_key}")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [
                        m["name"].replace("models/", "")
                        for m in data.get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", [])
                    ]
                    return models if models else [DEFAULT_MODEL]
            else:
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = client.get(f"{cleaned_base}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return models if models else [DEFAULT_MODEL]
    except Exception:
        pass
    return [DEFAULT_MODEL]


def call_llm_json(endpoint_base: str, api_key: str, model_name: str, prompt: str) -> str:
    """Executes a synchronous LLM call requesting structured JSON output."""
    cleaned_base = endpoint_base.rstrip("/")
    is_gemini = "googleapis.com" in cleaned_base

    if is_gemini:
        url = f"{cleaned_base}/models/{model_name}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1},
        }
    else:
        url = f"{cleaned_base}/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

    with httpx.Client(timeout=60.0) as client:
        res = client.post(url, headers=headers, json=payload)

    if res.status_code != 200:
        raise RuntimeError(f"HTTP {res.status_code} from provider: {res.text}")

    res_json = res.json()
    if "candidates" in res_json:
        raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
    elif "choices" in res_json:
        raw_text = res_json["choices"][0]["message"]["content"]
    else:
        raw_text = json.dumps(res_json)

    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


# -----------------------------------------------------------------------------
# AI-Assisted Sample Document Deconstruction & Template Pipeline
# -----------------------------------------------------------------------------
def build_document_skeleton(doc: Document) -> dict:
    """Step 1 (Deterministic Extraction): Maps Word XML into indexed coordinate structures."""
    paragraphs_meta = []
    for idx, p in enumerate(doc.paragraphs):
        full_text = "".join(r.text for r in p.runs).strip()
        if not full_text:
            continue
        paragraphs_meta.append({
            "id": f"p_{idx}",
            "text": full_text,
            "style": p.style.name if p.style else "Normal",
            "is_bold": any(r.bold for r in p.runs if r.text.strip()),
        })

    tables_meta = []
    for t_idx, table in enumerate(doc.tables):
        headers = [c.text.strip() for c in table.rows[0].cells] if len(table.rows) > 0 else []
        sample_row = [c.text.strip() for c in table.rows[1].cells] if len(table.rows) > 1 else []
        tables_meta.append({
            "id": f"t_{t_idx}",
            "cols_count": len(table.columns),
            "total_rows": len(table.rows),
            "headers": headers,
            "first_sample_row": sample_row,
        })

    return {"paragraphs": paragraphs_meta, "tables": tables_meta}


def generate_template_from_sample_ai(
    sample_bytes: bytes,
    base_url: str,
    api_key: str,
    model_name: str,
    status_container=None,
) -> io.BytesIO:
    """AI-powered multi-stage structural deconstruction and Jinja2 synthesis engine."""
    doc = Document(io.BytesIO(sample_bytes))

    if status_container:
        status_container.info("Step 1/3: Extracting document node skeleton...")
    skeleton = build_document_skeleton(doc)

    prompt = (
        "You are an expert document template architect. We are converting a finished meeting minutes Word document "
        "into a reusable Jinja2 template for python-docx / docxtpl.\n\n"
        "Here is the structural node skeleton of the document:\n"
        f"{json.dumps(skeleton, indent=2)}\n\n"
        "Available context variables in downstream rendering:\n"
        "- title: str\n"
        "- date: str\n"
        "- attendees: list[str]\n"
        "- executive_summary: str\n"
        "- agenda_and_decisions: list[{topic: str, discussion_summary: str, decisions_made: list[str]}]\n"
        "- action_items: list[{task: str, owner: str, deadline: str, priority: str}]\n"
        "- transcript: list[{speaker: str, timestamp: str, translated_text: str}]\n\n"
        "Instructions:\n"
        "1. For paragraphs: Determine which lines are STATIC headers/labels (KEEP_STATIC), which are single-line dynamic "
        "fields to REPLACE (e.g. 'Date: {{ date }}', '{{ title }}', '{{ executive_summary }}', 'Attendees: {% for a in attendees %}{{ a }}{% if not loop.last %}, {% endif %}{% endfor %}'), "
        "and which lines are residual dummy sample text that MUST BE DELETED (PURGE).\n"
        "2. For tables: If it's an Action Items grid, action = 'ACTION_ITEMS_LOOP' and provide cell_tag_map using docxtpl row repetition syntax "
        "like ['{%tr for item in action_items %}{{ item.task }}', '{{ item.owner }}', '{{ item.deadline }}', '{{ item.priority }}{%tr endfor %}']. "
        "If it's an Agenda table, action = 'AGENDA_LOOP'. Otherwise 'KEEP_STATIC' or 'IGNORE'.\n\n"
        "Return pure valid JSON matching this schema:\n"
        + json.dumps(TemplateTransformationPlan.model_json_schema())
    )

    if status_container:
        status_container.info("Step 2/3: AI synthesizing layout, static headers & Jinja2 loops...")

    raw_plan_json = call_llm_json(base_url, api_key, model_name, prompt)
    plan_dict = json.loads(raw_plan_json)
    plan = TemplateTransformationPlan(**plan_dict)

    if status_container:
        status_container.info("Step 3/3: Deterministically executing XML node mutations...")

    p_instructions = {item.element_id: item for item in plan.paragraph_instructions}
    t_instructions = {item.table_id: item for item in plan.table_instructions}

    # Execute Paragraph Mutations
    paragraphs_to_remove = []
    for idx, p in enumerate(doc.paragraphs):
        p_id = f"p_{idx}"
        if p_id in p_instructions:
            instr = p_instructions[p_id]
            if instr.action == "PURGE":
                paragraphs_to_remove.append(p)
            elif instr.action == "REPLACE" and instr.jinja_tag:
                # Preserve paragraph formatting: clear trailing runs and set run 0 text
                if p.runs:
                    p.runs[0].text = instr.jinja_tag
                    for r in p.runs[1:]:
                        r.text = ""
                else:
                    p.add_run(instr.jinja_tag)

    for p in paragraphs_to_remove:
        p_element = p._p
        if p_element.getparent() is not None:
            p_element.getparent().remove(p_element)

    # Execute Table Mutations
    for t_idx, table in enumerate(doc.tables):
        t_id = f"t_{t_idx}"
        if t_id in t_instructions:
            t_instr = t_instructions[t_id]
            if t_instr.action in ["ACTION_ITEMS_LOOP", "AGENDA_LOOP"] and t_instr.cell_tag_map:
                # Purge all sample data rows; keep header row
                while len(table.rows) > 1:
                    row = table.rows[-1]
                    tr = row._tr
                    tr.getparent().remove(tr)

                new_row = table.add_row()
                for c_idx, cell in enumerate(new_row.cells):
                    if c_idx < len(t_instr.cell_tag_map):
                        cell.text = t_instr.cell_tag_map[c_idx]

    out_stream = io.BytesIO()
    doc.save(out_stream)
    out_stream.seek(0)
    return out_stream


def render_template_docx(template_bytes: bytes, data: MeetingMinutesReport) -> io.BytesIO:
    doc = DocxTemplate(io.BytesIO(template_bytes))
    context = data.model_dump()
    for item in context.get("transcript", []):
        item["text"] = item.get("translated_text") or item.get("original_text", "")
    doc.render(context)
    out_stream = io.BytesIO()
    doc.save(out_stream)
    out_stream.seek(0)
    return out_stream


def build_default_docx(data: MeetingMinutesReport) -> io.BytesIO:
    doc = Document()
    doc.add_heading(data.title, level=0)
    doc.add_paragraph(f"Date: {data.date}")
    doc.add_paragraph(f"Attendees: {', '.join(data.attendees) if data.attendees else 'Not specified'}")

    doc.add_heading("1. Executive Summary", level=1)
    doc.add_paragraph(data.executive_summary)

    doc.add_heading("2. Agenda Items & Decisions", level=1)
    for idx, item in enumerate(data.agenda_and_decisions, 1):
        doc.add_heading(f"2.{idx} {item.topic}", level=2)
        doc.add_paragraph(f"Summary: {item.discussion_summary}")
        if item.decisions_made:
            doc.add_paragraph("Decisions Reached:", style="List Bullet")
            for dec in item.decisions_made:
                doc.add_paragraph(dec, style="List Bullet 2")

    doc.add_heading("3. Action Items", level=1)
    if data.action_items:
        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "Task"
        hdr_cells[1].text = "Owner"
        hdr_cells[2].text = "Deadline"
        hdr_cells[3].text = "Priority"

        for ai in data.action_items:
            row_cells = table.add_row().cells
            row_cells[0].text = ai.task
            row_cells[1].text = ai.owner
            row_cells[2].text = ai.deadline
            row_cells[3].text = ai.priority

    doc.add_heading("4. Speaker-Diarized Transcript (English)", level=1)
    for entry in data.transcript:
        ts = f"[{entry.timestamp}] " if entry.timestamp else ""
        p = doc.add_paragraph()
        runner = p.add_run(f"{ts}{entry.speaker}: ")
        runner.bold = True
        p.add_run(entry.translated_text)

    out_stream = io.BytesIO()
    doc.save(out_stream)
    out_stream.seek(0)
    return out_stream


# -----------------------------------------------------------------------------
# Audio Pipeline Dispatcher
# -----------------------------------------------------------------------------
def dispatch_http_request(endpoint_url: str, headers: dict, payload: dict) -> httpx.Response:
    with httpx.Client(timeout=360.0) as client:
        return client.post(endpoint_url, headers=headers, json=payload)


def analyze_meeting_audio_rest(
    audio_file_bytes: bytes,
    mime_type: str,
    api_key: str,
    base_url: str,
    model_name: str,
    progress_bar,
    status_text,
    log_container,
    logs_list,
) -> tuple[MeetingMinutesReport | None, dict | None]:
    file_size_mb = len(audio_file_bytes) / (1024 * 1024)
    log_event(log_container, logs_list, f"Ingested raw stream: {file_size_mb:.2f} MB ({mime_type})", "INFO")

    status_text.markdown("🔄 *Encoding audio stream to base64...*")
    progress_bar.progress(10)

    b64_start = time.time()
    b64_audio = base64.b64encode(audio_file_bytes).decode("utf-8")
    b64_duration = time.time() - b64_start
    log_event(log_container, logs_list, f"Base64 complete: {len(b64_audio):,} chars in {b64_duration:.2f}s", "DEBUG")
    progress_bar.progress(25)

    prompt = (
        "You are an expert executive meeting assistant. Listen carefully to this meeting audio recording:\n"
        "1. Produce a full diarized transcript identifying distinct speakers (e.g., Speaker 1, Speaker 2).\n"
        "2. In the transcript, transcribe the speech verbatim in 'original_text' (preserving native language/words), "
        "and provide an accurate English translation in 'translated_text' (keep identical if already English).\n"
        "3. Listen for verbal introductions, greetings, or names addressed in conversation to infer the real name of each speaker in 'detected_speakers'.\n"
        "4. Generate a comprehensive English executive summary.\n"
        "5. List all topics and decisions made in English.\n"
        "6. Extract all action items with owners, deadlines, and priorities in English.\n"
        "Return the output as pure valid JSON conforming strictly to this structure:\n"
        + json.dumps(MeetingMinutesReport.model_json_schema())
    )

    cleaned_base = base_url.rstrip("/")
    is_gemini = "googleapis.com" in cleaned_base

    if is_gemini:
        endpoint_url = f"{cleaned_base}/models/{model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_audio,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2,
            },
        }
    else:
        endpoint_url = f"{cleaned_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        audio_fmt = mime_type.split("/")[-1].replace("mpeg", "mp3")
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": b64_audio,
                                "format": audio_fmt,
                            },
                        },
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

    log_event(log_container, logs_list, f"Dispatching POST request to: {endpoint_url}", "DEBUG")
    progress_bar.progress(35)

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(dispatch_http_request, endpoint_url, headers, payload)

    req_start = time.time()
    quip_pool = list(FUNNY_QUIPS)
    random.shuffle(quip_pool)
    quip_idx = 0
    last_quip_time = req_start
    pct = 35

    while not future.done():
        time.sleep(1.2)
        elapsed = time.time() - req_start

        if pct < 85:
            pct += 1
            progress_bar.progress(pct)

        if time.time() - last_quip_time > 7.0:
            current_quip = quip_pool[quip_idx % len(quip_pool)]
            status_text.markdown(f"💬 *{current_quip}*")
            log_event(log_container, logs_list, current_quip, "QUIP")
            log_event(
                log_container,
                logs_list,
                f"Heartbeat: Model reasoning active... (elapsed: {int(elapsed)}s)",
                "DEBUG",
            )
            quip_idx += 1
            last_quip_time = time.time()

    response = future.result()
    latency = time.time() - req_start

    log_event(
        log_container,
        logs_list,
        f"Provider response received in {latency:.2f}s (HTTP {response.status_code})",
        "SUCCESS" if response.status_code == 200 else "ERROR",
    )

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

    progress_bar.progress(90)
    status_text.markdown("⚡ *Validating JSON structure and parsing usage metadata...*")

    res_json = response.json()

    prompt_tokens = 0
    completion_tokens = 0
    thoughts_tokens = 0
    total_tokens = 0

    if "usageMetadata" in res_json:
        usage = res_json["usageMetadata"]
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        thoughts_tokens = usage.get("thoughtsTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", 0)
    elif "usage" in res_json:
        usage = res_json["usage"]
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        details = usage.get("completion_tokens_details", {})
        thoughts_tokens = details.get("reasoning_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

    if "candidates" in res_json:
        raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
    elif "choices" in res_json:
        raw_text = res_json["choices"][0]["message"]["content"]
    else:
        raw_text = json.dumps(res_json)

    cleaned_json = raw_text.strip()
    if cleaned_json.startswith("```json"):
        cleaned_json = cleaned_json[7:]
    if cleaned_json.startswith("```"):
        cleaned_json = cleaned_json[3:]
    if cleaned_json.endswith("```"):
        cleaned_json = cleaned_json[:-3]

    parsed_data = json.loads(cleaned_json.strip())
    report = MeetingMinutesReport(**parsed_data)

    tok_per_sec = (completion_tokens / latency) if latency > 0 and completion_tokens > 0 else 0

    stats = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "thoughts_tokens": thoughts_tokens,
        "total_tokens": total_tokens,
        "latency": latency,
        "speed": tok_per_sec,
        "model": model_name,
    }

    log_event(
        log_container,
        logs_list,
        f"Usage validated: {total_tokens:,} tokens ({prompt_tokens:,} prompt, {completion_tokens:,} output, {thoughts_tokens:,} thinking)",
        "INFO",
    )

    progress_bar.progress(100)
    status_text.markdown("✅ **Processing Complete! Ready to review.**")
    return report, stats


def apply_speaker_replacements(report: MeetingMinutesReport, name_map: dict[str, str]) -> MeetingMinutesReport:
    updated = report.model_copy(deep=True)

    for entry in updated.transcript:
        if entry.speaker in name_map and name_map[entry.speaker].strip():
            entry.speaker = name_map[entry.speaker].strip()

    for ai in updated.action_items:
        for old_spk, new_spk in name_map.items():
            if new_spk.strip() and old_spk.lower() in ai.owner.lower():
                ai.owner = ai.owner.replace(old_spk, new_spk.strip())

    new_attendees = set()
    for att in updated.attendees:
        replaced = att
        for old_spk, new_spk in name_map.items():
            if new_spk.strip() and old_spk.lower() in att.lower():
                replaced = new_spk.strip()
        new_attendees.add(replaced)

    for new_spk in name_map.values():
        if new_spk.strip():
            new_attendees.add(new_spk.strip())

    updated.attendees = sorted(list(new_attendees))
    return updated


# -----------------------------------------------------------------------------
# Main Application UI
# -----------------------------------------------------------------------------
def main():
    h_col1, h_col2, h_col3 = st.columns([0.65, 0.18, 0.17], vertical_alignment="center")
    with h_col1:
        st.markdown("### 🎙️ AIMA — AI Meeting Assistant")
    with h_col2:
        if st.button("🔄 Start Over", use_container_width=True, help="Clear session and reset all fields"):
            for k in [
                "meeting_result",
                "usage_stats",
                "saved_template_bytes",
                "active_template_bytes",
                "converted_template_download",
                "logs_list",
            ]:
                if k in st.session_state:
                    del st.session_state[k]
            st.session_state["logs_list"] = [
                '<span class="log-debug">[System] Session reset. Ready.</span>'
            ]
            st.rerun()
    with h_col3:
        with st.popover("⚙️ Settings", use_container_width=True):
            st.markdown("**Provider & Model Settings**")
            has_key = bool(st.session_state.get("api_key"))
            st.caption(f"Status: {'🟢 Key is Set' if has_key else '🔴 No Key Set'}")

            new_key = st.text_input(
                "API Key / Token:",
                value="",
                type="password",
                placeholder="••••••••••••••••" if has_key else "Paste key...",
            )
            if new_key.strip():
                st.session_state["api_key"] = new_key.strip()
                st.rerun()

            current_base = st.session_state.get("base_url", DEFAULT_BASE_URL)
            new_base = st.text_input("Provider Endpoint URL:", value=current_base)
            if new_base != current_base:
                st.session_state["base_url"] = new_base.strip()
                st.session_state["available_models"] = fetch_available_models(
                    st.session_state["base_url"], st.session_state.get("api_key", "")
                )
                st.rerun()

            st.markdown("---")
            models_list = st.session_state.get("available_models", [DEFAULT_MODEL])
            curr_model = st.session_state.get("selected_model", DEFAULT_MODEL)
            idx = models_list.index(curr_model) if curr_model in models_list else 0

            st.session_state["selected_model"] = st.selectbox(
                "Active AI Model:",
                options=models_list,
                index=idx,
            )

            if st.button("🔄 Refresh Models List", use_container_width=True):
                st.session_state["available_models"] = fetch_available_models(
                    st.session_state["base_url"], st.session_state.get("api_key", "")
                )
                st.rerun()

    # Split Workspace
    col_left, col_right = st.columns([0.40, 0.60], gap="large")

    # =========================================================================
    # LEFT PANEL: Sequenced Workflow (1, 2, 3) + Execution Console
    # =========================================================================
    with col_left:
        st.markdown("#### 1. Upload Audio")
        audio_file = st.file_uploader(
            "Select meeting recording",
            type=["mp3", "wav", "m4a", "ogg", "aac", "mp4"],
            help="Supports MP3, WAV, M4A, OGG, AAC, MP4.",
            label_visibility="collapsed",
        )
        if audio_file:
            st.audio(audio_file)

        st.markdown("#### 2. Meeting Document Template")
        doc_choice = st.radio(
            "Template strategy:",
            ["Default Clean Format", "Upload Tagged .docx", "AI Convert Sample Finished .docx"],
            horizontal=False,
            label_visibility="collapsed",
        )

        if doc_choice == "Default Clean Format":
            st.session_state["active_template_bytes"] = None

        elif doc_choice == "Upload Tagged .docx":
            uploaded_tpl = st.file_uploader("Upload Word Template (.docx)", type=["docx"], key="tagged_docx")
            if uploaded_tpl:
                st.session_state["active_template_bytes"] = uploaded_tpl.getvalue()
                st.caption("✅ Custom Tagged Template Armed")

        elif doc_choice == "AI Convert Sample Finished .docx":
            sample_file = st.file_uploader("Upload Finished Sample (.docx)", type=["docx"], key="sample_docx")
            if sample_file:
                tpl_status = st.empty()
                if st.button("🤖 Build AI Template from Sample", use_container_width=True):
                    active_key = st.session_state.get("api_key")
                    active_base = st.session_state.get("base_url", DEFAULT_BASE_URL)
                    active_mod = st.session_state.get("selected_model", DEFAULT_MODEL)

                    if not active_key:
                        st.error("API Key required for AI template generation. Configure it in ⚙️ Settings.")
                    else:
                        try:
                            converted_io = generate_template_from_sample_ai(
                                sample_bytes=sample_file.getvalue(),
                                base_url=active_base,
                                api_key=active_key,
                                model_name=active_mod,
                                status_container=tpl_status,
                            )
                            converted_bytes = converted_io.getvalue()
                            st.session_state["active_template_bytes"] = converted_bytes
                            st.session_state["converted_template_download"] = converted_bytes
                            tpl_status.success("AI template successfully synthesized! All dummy text purged.")
                        except Exception as err:
                            tpl_status.error(f"AI conversion error: {err}")

            if st.session_state.get("converted_template_download"):
                st.download_button(
                    label="📥 Download Generated Template (.docx)",
                    data=st.session_state["converted_template_download"],
                    file_name="AI_Generated_Meeting_Template.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )

        st.markdown("#### 3. Process Meeting Audio")
        run_clicked = st.button("⚡ Process Meeting Audio", type="primary", use_container_width=True)

        st.markdown("---")

        # LEFT BOTTOM: Execution Console
        st.markdown("#### 📟 Execution Console")
        status_text = st.empty()
        progress_bar = st.progress(0)
        log_container = st.empty()

        reversed_initial = "<br>".join(reversed(st.session_state["logs_list"]))
        initial_html = f'<div id="aima-terminal-box" class="terminal-container">{reversed_initial}</div>'
        log_container.markdown(initial_html, unsafe_allow_html=True)

        stats = st.session_state.get("usage_stats")
        if stats:
            st.markdown("##### 📊 Telemetry & Usage Stats")
            has_thoughts = stats.get("thoughts_tokens", 0) > 0

            if has_thoughts:
                t_cols = st.columns(4)
                with t_cols[0]:
                    st.metric("Prompt", f"{stats['prompt_tokens']:,}")
                with t_cols[1]:
                    st.metric("Output", f"{stats['completion_tokens']:,}")
                with t_cols[2]:
                    st.metric("Thinking", f"{stats['thoughts_tokens']:,}")
                with t_cols[3]:
                    st.metric("Total Tokens", f"{stats['total_tokens']:,}")
            else:
                t_cols = st.columns(3)
                with t_cols[0]:
                    st.metric("Prompt", f"{stats['prompt_tokens']:,}")
                with t_cols[1]:
                    st.metric("Output", f"{stats['completion_tokens']:,}")
                with t_cols[2]:
                    st.metric("Total Tokens", f"{stats['total_tokens']:,}")

            p_cols = st.columns(2)
            with p_cols[0]:
                st.metric("Latency", f"{stats['latency']:.2f}s")
            with p_cols[1]:
                speed_str = f"{stats['speed']:.1f} tok/s" if stats['speed'] > 0 else "N/A"
                st.metric("Speed", speed_str)

        if run_clicked:
            active_key = st.session_state.get("api_key")
            active_base = st.session_state.get("base_url", DEFAULT_BASE_URL)
            active_mod = st.session_state.get("selected_model", DEFAULT_MODEL)

            if not active_key:
                st.error("Missing API Key. Open ⚙️ Settings in header.")
                return

            if not audio_file:
                st.error("Please upload an audio file in Step 1.")
                return

            st.session_state["logs_list"] = []
            log_event(log_container, st.session_state["logs_list"], "Execution initialized...", "INFO")

            try:
                audio_bytes = audio_file.read()
                mime = audio_file.type if audio_file.type else "audio/mp3"

                report, usage_metrics = analyze_meeting_audio_rest(
                    audio_file_bytes=audio_bytes,
                    mime_type=mime,
                    api_key=active_key,
                    base_url=active_base,
                    model_name=active_mod,
                    progress_bar=progress_bar,
                    status_text=status_text,
                    log_container=log_container,
                    logs_list=st.session_state["logs_list"],
                )

                if report:
                    st.session_state["meeting_result"] = report
                    st.session_state["usage_stats"] = usage_metrics
                    st.session_state["saved_template_bytes"] = st.session_state.get("active_template_bytes")
                    st.rerun()

            except Exception as e:
                progress_bar.empty()
                status_text.empty()
                log_event(log_container, st.session_state["logs_list"], f"Execution failed: {str(e)}", "ERROR")
                st.error(f"Error: {e}")
                return

    # =========================================================================
    # RIGHT PANEL: Document View & Deliverables
    # =========================================================================
    with col_right:
        if "meeting_result" not in st.session_state:
            st.info("👈 Upload meeting audio and follow Steps 1 to 3 on the left to produce your report.")
            st.markdown(
                """
                ```
                +-------------------------------------------------------------+
                |                    MEETING DOCUMENT VIEWER                  |
                |                                                             |
                |  • Executive Summary (English)                              |
                |  • Diarized Speaker Verification & Re-mapping               |
                |  • Bilingual Transcript (Original Spoken + Translated)      |
                |  • Action Items Matrix (Owner, Deadline, Priority)          |
                |  • Dynamic DOCX Generation & Export                         |
                +-------------------------------------------------------------+
                ```
                """
            )
        else:
            result: MeetingMinutesReport = st.session_state["meeting_result"]
            active_template = st.session_state.get("saved_template_bytes")

            t_col1, t_col2 = st.columns([0.7, 0.3])
            with t_col1:
                st.markdown(f"## {result.title}")
                st.caption(f"📅 **Date:** {result.date} | 👥 **Attendees:** {', '.join(result.attendees)}")
            with t_col2:
                if active_template:
                    try:
                        doc_io = render_template_docx(active_template, result)
                    except Exception:
                        doc_io = build_default_docx(result)
                else:
                    doc_io = build_default_docx(result)

                st.download_button(
                    label="📥 Download .docx",
                    data=doc_io,
                    file_name=f"{result.title.replace(' ', '_')}_Minutes.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                    type="primary",
                )

            # Speaker Mapping Component
            raw_speakers = sorted(list({entry.speaker for entry in result.transcript}))
            inferred_lookup = {ds.speaker_id: ds.inferred_name for ds in getattr(result, "detected_speakers", [])}

            with st.expander("👥 Speaker Identity Mapping & Verification", expanded=False):
                with st.form("speaker_mapping_form"):
                    cols = st.columns(min(len(raw_speakers), 3) if raw_speakers else 1)
                    confirmed_mapping = {}

                    for idx, spk in enumerate(raw_speakers):
                        col = cols[idx % len(cols)]
                        default_guess = inferred_lookup.get(spk, "")
                        if default_guess.lower() == "unknown":
                            default_guess = ""

                        with col:
                            confirmed_name = st.text_input(
                                f"Label: {spk}",
                                value=default_guess if default_guess else spk,
                                key=f"spk_{spk}",
                            )
                            confirmed_mapping[spk] = confirmed_name

                    if st.form_submit_button("⚡ Update Names Across Document", use_container_width=True):
                        st.session_state["meeting_result"] = apply_speaker_replacements(result, confirmed_mapping)
                        st.rerun()

            # Structured Deliverables
            tab_overview, tab_actions, tab_transcript, tab_raw = st.tabs(
                ["📄 Overview & Agendas", "✅ Action Items", "📝 Diarized Transcript", "🔧 Raw Data"]
            )

            with tab_overview:
                st.markdown("### Executive Summary")
                st.write(result.executive_summary)
                st.markdown("---")

                st.markdown("### Agenda Breakdown & Decisions")
                for item in result.agenda_and_decisions:
                    with st.expander(f"Topic: {item.topic}", expanded=True):
                        st.write(item.discussion_summary)
                        if item.decisions_made:
                            st.markdown("**Decisions Reached:**")
                            for dec in item.decisions_made:
                                st.markdown(f"- {dec}")

            with tab_actions:
                st.markdown("### Action Items Matrix")
                if result.action_items:
                    st.dataframe([item.model_dump() for item in result.action_items], use_container_width=True)
                else:
                    st.info("No action items detected in the discussion.")

            with tab_transcript:
                view_mode = st.radio(
                    "Transcript View Mode:",
                    ["English Translation", "Original Spoken Language", "Bilingual Side-by-Side"],
                    horizontal=True,
                )

                for entry in result.transcript:
                    ts = f"`{entry.timestamp}` " if entry.timestamp else ""
                    if view_mode == "English Translation":
                        st.markdown(f"{ts}**{entry.speaker}**: {entry.translated_text}")
                    elif view_mode == "Original Spoken Language":
                        st.markdown(f"{ts}**{entry.speaker}**: {entry.original_text}")
                    else:
                        st.markdown(f"{ts}**{entry.speaker}**")
                        st.markdown(f"- *Original:* {entry.original_text}")
                        st.markdown(f"- *English:* {entry.translated_text}")

            with tab_raw:
                st.markdown("### Export JSON Metadata")
                json_bytes = json.dumps(result.model_dump(), indent=2)
                st.download_button(
                    label="Export Raw JSON",
                    data=json_bytes,
                    file_name="meeting_metadata.json",
                    mime="application/json",
                )
                st.json(result.model_dump())


if __name__ == "__main__":
    main()
