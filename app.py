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
# Configuration & Setup
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
    :root {
        --aima-ink: #102033;
        --aima-muted: #627084;
        --aima-line: rgba(16, 32, 51, 0.11);
        --aima-blue: #2f6fed;
        --aima-blue-soft: #eef4ff;
        --aima-green: #1e9d72;
        --aima-cream: #f7f9fc;
    }

    .block-container {
        padding-top: 2.1rem !important;
        padding-bottom: 3rem !important;
        padding-left: clamp(1rem, 3vw, 3.25rem) !important;
        padding-right: clamp(1rem, 3vw, 3.25rem) !important;
        max-width: 1560px !important;
    }

    div[data-testid="stPopover"] { margin-top: 0.15rem; }

    button[aria-label="Show password text"],
    button[aria-label="Hide password text"],
    input[type="password"]::-ms-reveal,
    input[type="password"]::-ms-clear {
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
    }

    h1, h2, h3, h4, p, label { letter-spacing: -0.01em; }
    h2, h3, h4 { color: var(--aima-ink); }

    .aima-brand { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.25rem; }
    .aima-mark {
        width: 2.35rem; height: 2.35rem; display: grid; place-items: center;
        border-radius: 0.8rem; background: linear-gradient(135deg, #2f6fed, #7049d7);
        color: white; font-size: 1.15rem; box-shadow: 0 8px 20px rgba(47, 111, 237, 0.22);
    }
    .aima-name { font-size: 1.12rem; font-weight: 800; color: var(--aima-ink); line-height: 1.05; }
    .aima-subname { color: var(--aima-muted); font-size: 0.72rem; margin-top: 0.22rem; }

    .hero-panel {
        margin: 1.3rem 0 1.6rem; padding: 1.55rem 1.7rem; border-radius: 1.1rem;
        background: linear-gradient(118deg, #f1f5ff 0%, #fbfcff 54%, #eefaf7 100%);
        border: 1px solid rgba(47, 111, 237, 0.13); position: relative; overflow: hidden;
    }
    .hero-panel:after {
        content: ""; width: 12rem; height: 12rem; border-radius: 50%; position: absolute;
        right: -3rem; top: -5rem; background: rgba(112, 73, 215, 0.08);
    }
    .eyebrow, .card-kicker { color: var(--aima-blue); font-size: 0.68rem; font-weight: 800; letter-spacing: 0.11em; text-transform: uppercase; }
    .hero-title { color: var(--aima-ink); font-size: clamp(1.55rem, 2.5vw, 2.25rem); font-weight: 800; line-height: 1.04; margin: 0.35rem 0; max-width: 32rem; }
    .hero-copy { color: var(--aima-muted); max-width: 45rem; margin: 0; font-size: 0.96rem; line-height: 1.55; }

    .section-heading { margin: 0.2rem 0 0.75rem; }
    .section-title { color: var(--aima-ink); font-size: 1.03rem; font-weight: 800; margin: 0; }
    .section-caption { color: var(--aima-muted); font-size: 0.78rem; margin-top: 0.25rem; }
    .surface-caption { color: var(--aima-muted); font-size: 0.8rem; line-height: 1.45; }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-color: var(--aima-line) !important; border-radius: 1rem !important;
        box-shadow: 0 10px 30px rgba(16, 32, 51, 0.035);
    }
    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, #ffffff, #f8faff);
        padding: 0.7rem 0.85rem; border-radius: 0.8rem;
        border: 1px solid var(--aima-line); box-shadow: none;
    }
    div[data-testid="stMetricLabel"] { color: var(--aima-muted); font-size: 0.72rem; }
    div[data-testid="stMetricValue"] { color: var(--aima-ink); font-size: 1.35rem; }

    .empty-state { padding: 2.3rem 1rem 2.6rem; text-align: center; }
    .empty-icon { font-size: 2.4rem; margin-bottom: 0.7rem; }
    .empty-title { color: var(--aima-ink); font-size: 1.35rem; font-weight: 800; margin-bottom: 0.45rem; }
    .empty-copy { color: var(--aima-muted); max-width: 32rem; margin: 0 auto 1.35rem; line-height: 1.55; }
    .empty-steps { display: flex; justify-content: center; flex-wrap: wrap; gap: 0.55rem; }
    .empty-step { background: var(--aima-blue-soft); color: #31578f; border-radius: 999px; padding: 0.42rem 0.75rem; font-size: 0.74rem; font-weight: 700; }

    .result-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
    .result-title { color: var(--aima-ink); font-size: clamp(1.35rem, 2.2vw, 2rem); font-weight: 800; line-height: 1.08; margin: 0; }
    .result-meta { color: var(--aima-muted); font-size: 0.78rem; margin-top: 0.45rem; }
    .summary-card { padding: 1.15rem 1.25rem; border-radius: 0.9rem; background: #f8fbff; border: 1px solid #dfeafe; color: #30435b; line-height: 1.65; margin: 0.25rem 0 1.35rem; }
    .summary-label { color: var(--aima-blue); font-size: 0.68rem; font-weight: 800; letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 0.35rem; }

    .action-card { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0.35rem 1rem; align-items: center; padding: 0.9rem 1rem; margin: 0.55rem 0; border: 1px solid var(--aima-line); border-radius: 0.8rem; background: #fff; }
    .action-task { color: var(--aima-ink); font-weight: 700; line-height: 1.35; }
    .action-meta { color: var(--aima-muted); font-size: 0.76rem; margin-top: 0.3rem; }
    .priority { border-radius: 999px; padding: 0.3rem 0.58rem; font-size: 0.68rem; font-weight: 800; white-space: nowrap; }
    .priority-high { color: #a52b31; background: #fff0f0; }
    .priority-medium { color: #94620c; background: #fff7df; }
    .priority-low { color: #197454; background: #eaf8f1; }
    .priority-default { color: #526273; background: #f0f3f7; }

    .transcript-entry { display: grid; grid-template-columns: 2.3rem minmax(0, 1fr); gap: 0.7rem; padding: 0.75rem 0; border-bottom: 1px solid rgba(16, 32, 51, 0.08); }
    .transcript-entry:last-child { border-bottom: 0; }
    .speaker-avatar { width: 2.1rem; height: 2.1rem; display: grid; place-items: center; border-radius: 0.65rem; background: #eaf1ff; color: var(--aima-blue); font-weight: 800; font-size: 0.7rem; }
    .transcript-speaker { color: var(--aima-ink); font-weight: 800; font-size: 0.83rem; }
    .transcript-time { color: var(--aima-muted); font-size: 0.7rem; margin-left: 0.45rem; font-weight: 500; }
    .transcript-text { color: #43556a; line-height: 1.55; font-size: 0.86rem; margin-top: 0.22rem; }

    .terminal-container {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        font-size: 0.76rem; background: #101827; color: #d1d5db; padding: 0.9rem;
        border-radius: 0.8rem; height: 285px; overflow-y: auto; border: 1px solid #263249;
        line-height: 1.5; white-space: pre-wrap; box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }
    .log-quip { font-size: 0.68rem !important; color: #94a3b8 !important; font-style: italic; padding-left: 0.5rem; display: block; margin: 2px 0; }
    .log-info { color: #5ecbfa; }
    .log-debug { color: #9ca3af; }
    .log-warn { color: #fbbf24; }
    .log-error { color: #f87171; }
    .log-success { color: #4ade80; }

    @media (max-width: 900px) {
        .hero-panel { padding: 1.2rem; }
        .action-card { grid-template-columns: 1fr; }
    }
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
# Quips Registry
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
    text: str = Field(description="Transcribed statement.")


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
    executive_summary: str = Field(description="Executive summary of the meeting.")
    agenda_and_decisions: list[AgendaItem] = Field(description="Topic breakdowns and decisions.")
    action_items: list[ActionItem] = Field(description="Action items extracted.")
    transcript: list[TranscriptEntry] = Field(description="Full speaker-diarized transcript.")


# -----------------------------------------------------------------------------
# Helpers & Model Discovery
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
    html_output = f'<div class="terminal-container">{"<br>".join(logs_list)}</div>'
    log_container.markdown(html_output, unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# Asynchronous Background Worker & Telemetry
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
) -> tuple[MeetingMinutesReport, dict]:
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
        "2. Listen for verbal introductions, greetings, or names addressed in conversation to infer the real name of each speaker in 'detected_speakers'.\n"
        "3. Generate an executive summary.\n"
        "4. List all topics and decisions made.\n"
        "5. Extract all action items with owners, deadlines, and priorities.\n"
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

    # Launch inference in background thread
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(dispatch_http_request, endpoint_url, headers, payload)

    req_start = time.time()
    quip_pool = list(FUNNY_QUIPS)
    random.shuffle(quip_pool)
    quip_idx = 0
    last_quip_time = req_start
    pct = 35

    # Interactive polling loop while remote model processes audio
    while not future.done():
        time.sleep(1.2)
        elapsed = time.time() - req_start

        # Gradually advance progress bar toward 85%
        if pct < 85:
            pct += 1
            progress_bar.progress(pct)

        # Rotate quip and heartbeat every 7 seconds
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

    if "usageMetadata" in res_json:  # Gemini Native
        usage = res_json["usageMetadata"]
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        thoughts_tokens = usage.get("thoughtsTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", 0)
    elif "usage" in res_json:  # OpenAI / Compatible
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


# -----------------------------------------------------------------------------
# Speaker Replacement Routine
# -----------------------------------------------------------------------------
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
# DOCX Engines
# -----------------------------------------------------------------------------
def convert_sample_docx_to_template(sample_bytes: bytes) -> io.BytesIO:
    doc = Document(io.BytesIO(sample_bytes))

    def replace_keywords_in_paragraph(p):
        txt = p.text.strip().lower()
        if not txt:
            return
        if any(w in txt for w in ["executive summary", "overview", "background", "summary:"]):
            p.text = "{{ executive_summary }}"
        elif any(w in txt for w in ["attendees", "participants", "present:"]):
            p.text = "Attendees: {% for a in attendees %}{{ a }}{% if not loop.last %}, {% endif %}{% endfor %}"
        elif any(w in txt for w in ["meeting date", "date:"]):
            p.text = "Date: {{ date }}"
        elif any(w in txt for w in ["meeting title", "subject:", "title:"]):
            p.text = "{{ title }}"

    for p in doc.paragraphs:
        replace_keywords_in_paragraph(p)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    replace_keywords_in_paragraph(p)

        if len(table.rows) >= 2:
            first_row_txt = " ".join(c.text.lower() for c in table.rows[0].cells)
            if any(w in first_row_txt for w in ["task", "action", "owner", "assignee", "due"]):
                while len(table.rows) > 1:
                    row_to_delete = table.rows[-1]
                    tr = row_to_delete._tr
                    tr.getparent().remove(tr)

                new_row = table.add_row()
                cells = new_row.cells
                if len(cells) >= 4:
                    cells[0].text = "{%tr for item in action_items %}{{ item.task }}"
                    cells[1].text = "{{ item.owner }}"
                    cells[2].text = "{{ item.deadline }}"
                    cells[3].text = "{{ item.priority }}{%tr endfor %}"
                elif len(cells) >= 3:
                    cells[0].text = "{%tr for item in action_items %}{{ item.task }}"
                    cells[1].text = "{{ item.owner }}"
                    cells[2].text = "{{ item.deadline }}{%tr endfor %}"

    out_stream = io.BytesIO()
    doc.save(out_stream)
    out_stream.seek(0)
    return out_stream


def render_template_docx(template_bytes: bytes, data: MeetingMinutesReport) -> io.BytesIO:
    doc = DocxTemplate(io.BytesIO(template_bytes))
    context = data.model_dump()
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

    doc.add_heading("4. Speaker-Diarized Transcript", level=1)
    for entry in data.transcript:
        ts = f"[{entry.timestamp}] " if entry.timestamp else ""
        p = doc.add_paragraph()
        runner = p.add_run(f"{ts}{entry.speaker}: ")
        runner.bold = True
        p.add_run(entry.text)

    out_stream = io.BytesIO()
    doc.save(out_stream)
    out_stream.seek(0)
    return out_stream


# -----------------------------------------------------------------------------
# Main Application
# -----------------------------------------------------------------------------
def main():
    # Brand header
    h_col1, h_col2 = st.columns([0.78, 0.22], vertical_alignment="center")
    with h_col1:
        st.markdown(
            '<div class="aima-brand"><div class="aima-mark">✦</div><div><div class="aima-name">AIMA</div><div class="aima-subname">AI meeting intelligence</div></div></div>',
            unsafe_allow_html=True,
        )
    with h_col2:
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
            new_base = st.text_input(
                "Provider Endpoint URL:",
                value=current_base,
                help="Base URL without model path",
            )
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
            st.session_state["selected_model"] = st.selectbox("Active AI Model:", options=models_list, index=idx)

            if st.button("🔄 Refresh Models List", use_container_width=True):
                st.session_state["available_models"] = fetch_available_models(
                    st.session_state["base_url"], st.session_state.get("api_key", "")
                )
                st.rerun()

    st.markdown(
        '<div class="hero-panel"><div class="eyebrow">Meeting workspace</div><div class="hero-title">Turn the conversation into clear next steps.</div><p class="hero-copy">Upload a recording and AIMA will shape the discussion into an executive brief, decisions, owners, and a searchable transcript.</p></div>',
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([0.36, 0.64], gap="large")

    # Input and processing workspace
    with col_left:
        st.markdown('<div class="section-heading"><div class="section-title">Create meeting brief</div><div class="section-caption">Add a recording and choose how the document should look.</div></div>', unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<div class="card-kicker">01 · Source recording</div>', unsafe_allow_html=True)
            audio_file = st.file_uploader(
                "Upload Meeting Recording",
                type=["mp3", "wav", "m4a", "ogg", "aac", "mp4"],
                help="Supports MP3, WAV, M4A, OGG, AAC, MP4.",
            )
            if audio_file:
                st.audio(audio_file)
                st.caption(f"Ready to process · {audio_file.name}")
            else:
                st.caption("MP3, WAV, M4A, OGG, AAC, or MP4")

            st.markdown('<div class="card-kicker" style="margin-top:1.1rem">02 · Output format</div>', unsafe_allow_html=True)
            doc_choice = st.radio(
                "Document Style:",
                ["Default Executive Layout", "Upload Tagged .docx", "Convert Finished Sample .docx"],
                label_visibility="collapsed",
            )

            if doc_choice == "Default Executive Layout":
                st.session_state["active_template_bytes"] = None
            elif doc_choice == "Upload Tagged .docx":
                uploaded_tpl = st.file_uploader("Template (.docx)", type=["docx"], key="tagged_docx")
                if uploaded_tpl:
                    st.session_state["active_template_bytes"] = uploaded_tpl.getvalue()
            elif doc_choice == "Convert Finished Sample .docx":
                sample_file = st.file_uploader("Sample Finished (.docx)", type=["docx"], key="sample_docx")
                if sample_file:
                    try:
                        converted_io = convert_sample_docx_to_template(sample_file.getvalue())
                        st.session_state["active_template_bytes"] = converted_io.getvalue()
                        st.toast("Template generated from sample!", icon="📄")
                    except Exception as err:
                        st.error(f"Sample parsing failed: {err}")

            run_clicked = st.button("⚡ Generate meeting brief", type="primary", use_container_width=True)

        st.markdown('<div class="section-heading" style="margin-top:1.35rem"><div class="section-title">Processing activity</div><div class="section-caption">Live pipeline events and model telemetry.</div></div>', unsafe_allow_html=True)
        status_text = st.empty()
        progress_bar = st.progress(0)
        log_container = st.empty()
        initial_html = f'<div class="terminal-container">{"<br>".join(st.session_state["logs_list"])}</div>'
        log_container.markdown(initial_html, unsafe_allow_html=True)

        stats = st.session_state.get("usage_stats")
        if stats:
            st.markdown('<div class="card-kicker" style="margin-top:1rem">Latest run</div>', unsafe_allow_html=True)
            has_thoughts = stats.get("thoughts_tokens", 0) > 0
            stat_cols = st.columns(4 if has_thoughts else 3)
            metrics = [
                ("Prompt", f"{stats['prompt_tokens']:,}"),
                ("Output", f"{stats['completion_tokens']:,}"),
            ]
            if has_thoughts:
                metrics.append(("Thinking", f"{stats['thoughts_tokens']:,}"))
            metrics.append(("Total tokens", f"{stats['total_tokens']:,}"))
            for col, (label, value) in zip(stat_cols, metrics):
                with col:
                    st.metric(label, value)
            detail_cols = st.columns(2)
            with detail_cols[0]:
                st.metric("Latency", f"{stats['latency']:.2f}s")
            with detail_cols[1]:
                speed_str = f"{stats['speed']:.1f} tok/s" if stats['speed'] > 0 else "N/A"
                st.metric("Speed", speed_str)

        if run_clicked:
            active_key = st.session_state.get("api_key")
            active_base = st.session_state.get("base_url", DEFAULT_BASE_URL)
            active_mod = st.session_state.get("selected_model", DEFAULT_MODEL)

            if not active_key:
                st.error("Missing API Key. Open ⚙️ Settings in the top header to configure one.")
                return
            if not audio_file:
                st.error("Please upload an audio file first.")
                return

            st.session_state["logs_list"] = []
            log_event(log_container, st.session_state["logs_list"], "Starting execution sequence...", "INFO")
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
                st.session_state["meeting_result"] = report
                st.session_state["usage_stats"] = usage_metrics
                st.session_state["saved_template_bytes"] = st.session_state.get("active_template_bytes")
                st.rerun()
            except Exception as e:
                progress_bar.empty()
                status_text.empty()
                log_event(log_container, st.session_state["logs_list"], f"Pipeline failed: {str(e)}", "ERROR")
                st.error(f"Error: {e}")
                return

    # Results workspace
    with col_right:
        if "meeting_result" not in st.session_state:
            with st.container(border=True):
                st.markdown(
                    '<div class="empty-state"><div class="empty-icon">✦</div><div class="empty-title">Your meeting brief will appear here</div><p class="empty-copy">Start with a recording on the left. AIMA will organize the important parts into a polished, reviewable brief without making you scrub through the audio again.</p><div class="empty-steps"><span class="empty-step">Executive summary</span><span class="empty-step">Decisions & topics</span><span class="empty-step">Action owners</span><span class="empty-step">Diarized transcript</span></div></div>',
                    unsafe_allow_html=True,
                )
        else:
            result: MeetingMinutesReport = st.session_state["meeting_result"]
            active_template = st.session_state.get("saved_template_bytes")
            attendee_count = len(result.attendees)
            agenda_count = len(result.agenda_and_decisions)
            action_count = len(result.action_items)
            transcript_count = len(result.transcript)

            with st.container(border=True):
                header_col, download_col = st.columns([0.72, 0.28], vertical_alignment="top")
                with header_col:
                    st.markdown(f'<div class="eyebrow">Meeting brief</div><div class="result-title">{result.title}</div><div class="result-meta">📅 {result.date} &nbsp;·&nbsp; 👥 {attendee_count} attendee{"s" if attendee_count != 1 else ""}</div>', unsafe_allow_html=True)
                with download_col:
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

                st.markdown("<div style=\"height:0.9rem\"></div>", unsafe_allow_html=True)
                metric_cols = st.columns(4)
                for col, label, value in zip(metric_cols, ["Attendees", "Topics", "Action items", "Transcript lines"], [attendee_count, agenda_count, action_count, transcript_count]):
                    with col:
                        st.metric(label, value)

                st.markdown('<div style="height:1.1rem"></div>', unsafe_allow_html=True)
                raw_speakers = sorted(list({entry.speaker for entry in result.transcript}))
                inferred_lookup = {ds.speaker_id: ds.inferred_name for ds in getattr(result, "detected_speakers", [])}
                with st.expander("👥 Review speaker names", expanded=False):
                    with st.form("speaker_mapping_form"):
                        cols = st.columns(min(len(raw_speakers), 3) if raw_speakers else 1)
                        confirmed_mapping = {}
                        for idx, spk in enumerate(raw_speakers):
                            col = cols[idx % len(cols)]
                            default_guess = inferred_lookup.get(spk, "")
                            if default_guess.lower() == "unknown":
                                default_guess = ""
                            with col:
                                confirmed_name = st.text_input(f"Label: {spk}", value=default_guess if default_guess else spk, key=f"spk_{spk}")
                                confirmed_mapping[spk] = confirmed_name
                        if st.form_submit_button("⚡ Update names across brief", use_container_width=True):
                            st.session_state["meeting_result"] = apply_speaker_replacements(result, confirmed_mapping)
                            st.rerun()

                tab_overview, tab_actions, tab_transcript, tab_raw = st.tabs(["Overview", "Action items", "Transcript", "Raw data"])
                with tab_overview:
                    st.markdown('<div class="summary-label">Executive summary</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="summary-card">{result.executive_summary}</div>', unsafe_allow_html=True)
                    st.markdown("### Agenda & decisions")
                    if result.agenda_and_decisions:
                        for item in result.agenda_and_decisions:
                            decision_count = len(item.decisions_made)
                            label = f"{item.topic} · {decision_count} decision{'s' if decision_count != 1 else ''}"
                            with st.expander(label, expanded=True):
                                st.write(item.discussion_summary)
                                if item.decisions_made:
                                    st.markdown("**Decisions reached**")
                                    for dec in item.decisions_made:
                                        st.markdown(f"- {dec}")
                    else:
                        st.info("No agenda topics were detected.")

                with tab_actions:
                    st.markdown('<div class="summary-label">Ownership map</div>', unsafe_allow_html=True)
                    if result.action_items:
                        cards = []
                        for item in result.action_items:
                            priority_value = str(item.priority or "Unspecified")
                            priority_class = priority_value.lower() if priority_value.lower() in ["high", "medium", "low"] else "default"
                            cards.append(f'<div class="action-card"><div><div class="action-task">{item.task}</div><div class="action-meta">Owner: {item.owner} &nbsp;·&nbsp; Due: {item.deadline}</div></div><span class="priority priority-{priority_class}">{priority_value}</span></div>')
                        st.markdown("".join(cards), unsafe_allow_html=True)
                    else:
                        st.info("No action items detected in the discussion.")

                with tab_transcript:
                    st.markdown('<div class="summary-label">Diarized conversation</div>', unsafe_allow_html=True)
                    if result.transcript:
                        entries = []
                        for entry in result.transcript:
                            speaker = str(entry.speaker or "Unknown")
                            initials = "".join(part[0] for part in speaker.split()[:2]).upper() or "?"
                            timestamp = f'<span class="transcript-time">{entry.timestamp}</span>' if entry.timestamp else ""
                            entries.append(f'<div class="transcript-entry"><div class="speaker-avatar">{initials}</div><div><div class="transcript-speaker">{speaker}{timestamp}</div><div class="transcript-text">{entry.text}</div></div></div>')
                        st.markdown("".join(entries), unsafe_allow_html=True)
                    else:
                        st.info("No transcript lines were returned.")

                with tab_raw:
                    st.markdown('<div class="summary-label">Export metadata</div>', unsafe_allow_html=True)
                    json_bytes = json.dumps(result.model_dump(), indent=2)
                    st.download_button(label="Export raw JSON", data=json_bytes, file_name="meeting_metadata.json", mime="application/json")
                    st.json(result.model_dump())


if __name__ == "__main__":
    main()
if __name__ == "__main__":
    main()
