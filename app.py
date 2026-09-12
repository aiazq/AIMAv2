import base64
import datetime
import io
import json
import os
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

# Custom Styling for Workstation View and Button Positioning
st.markdown(
    """
    <style>
    /* Ensure header controls are fully visible below Streamlit's native header */
    .block-container {
        padding-top: 3.2rem !important;
        padding-bottom: 2rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        max-width: 100% !important;
    }

    /* Spacing for the settings popover trigger */
    div[data-testid="stPopover"] {
        margin-top: 0.35rem;
    }

    /* Remove Streamlit password reveal eyes */
    button[aria-label="Show password text"],
    button[aria-label="Hide password text"],
    input[type="password"]::-ms-reveal,
    input[type="password"]::-ms-clear {
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
    }

    /* Telemetry cards */
    div[data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        padding: 0.5rem 0.75rem;
        border-radius: 6px;
        border: 1px solid rgba(128, 128, 128, 0.15);
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
    st.session_state["logs_list"] = ["[System] Ready. Waiting for user input..."]

if "usage_stats" not in st.session_state:
    st.session_state["usage_stats"] = None


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
# Model Discovery & REST Pipeline
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
    formatted_log = f"[{ts}] [{level}] {message}"
    logs_list.append(formatted_log)
    log_container.code("\n".join(logs_list), language="log")


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
    log_event(log_container, logs_list, f"Ingested audio: {file_size_mb:.2f} MB ({mime_type})")

    status_text.text("Encoding audio buffer...")
    progress_bar.progress(15)

    b64_start = time.time()
    b64_audio = base64.b64encode(audio_file_bytes).decode("utf-8")
    log_event(log_container, logs_list, f"Base64 encoded in {time.time() - b64_start:.2f}s ({len(b64_audio):,} chars)")
    progress_bar.progress(35)

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

    log_event(log_container, logs_list, f"Dispatching inference to: {endpoint_url}")
    status_text.text(f"Running audio inference via {model_name}...")
    progress_bar.progress(55)

    req_start = time.time()
    with httpx.Client(timeout=360.0) as client:
        response = client.post(endpoint_url, headers=headers, json=payload)
    latency = time.time() - req_start

    log_event(
        log_container,
        logs_list,
        f"Provider response: HTTP {response.status_code} in {latency:.2f}s",
        level="INFO" if response.status_code == 200 else "ERROR",
    )

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

    progress_bar.progress(85)
    status_text.text("Parsing structured payload & usage metrics...")

    res_json = response.json()

    # Extract Usage Telemetry
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    if "usageMetadata" in res_json:  # Gemini Native
        usage = res_json["usageMetadata"]
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", 0)
    elif "usage" in res_json:  # OpenAI / Compatible
        usage = res_json["usage"]
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

    # Extract Content
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
        "total_tokens": total_tokens,
        "latency": latency,
        "speed": tok_per_sec,
        "model": model_name,
    }

    log_event(
        log_container,
        logs_list,
        f"Usage: {total_tokens:,} tokens ({prompt_tokens:,} prompt + {completion_tokens:,} output) | {latency:.2f}s",
    )

    progress_bar.progress(100)
    status_text.text("Processing Complete!")
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
    # Top Header Strip
    h_col1, h_col2 = st.columns([0.82, 0.18], vertical_alignment="center")
    with h_col1:
        st.markdown("### 🎙️ AIMA — AI Meeting Assistant")
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
                help="Base URL without model path (e.g., [https://generativelanguage.googleapis.com/v1beta](https://generativelanguage.googleapis.com/v1beta) or [https://api.openai.com/v1](https://api.openai.com/v1))",
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
    col_left, col_right = st.columns([0.38, 0.62], gap="large")

    # =========================================================================
    # LEFT PANEL: Controls (Top) + Logging & Telemetry (Bottom)
    # =========================================================================
    with col_left:
        st.markdown("#### 🎛️ Input Controls")

        with st.container():
            audio_file = st.file_uploader(
                "Upload Meeting Recording",
                type=["mp3", "wav", "m4a", "ogg", "aac", "mp4"],
                help="Supports MP3, WAV, M4A, OGG, AAC, MP4.",
            )
            if audio_file:
                st.audio(audio_file)

            doc_choice = st.radio(
                "Document Style:",
                ["Default Executive Layout", "Upload Tagged .docx", "Convert Finished Sample .docx"],
                horizontal=False,
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

            run_clicked = st.button("⚡ Process Meeting Recording", type="primary", use_container_width=True)

        st.markdown("---")

        # LEFT BOTTOM: Real-time Execution Console & Telemetry
        st.markdown("#### 📟 Execution Console")
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.empty()
        log_container.code("\n".join(st.session_state["logs_list"]), language="log")

        # Usage Statistics Panel
        stats = st.session_state.get("usage_stats")
        if stats:
            st.markdown("##### 📊 Telemetry & Usage Stats")
            u_col1, u_col2 = st.columns(2)
            with u_col1:
                st.metric("Prompt Tokens", f"{stats['prompt_tokens']:,}")
                st.metric("Total Tokens", f"{stats['total_tokens']:,}")
            with u_col2:
                st.metric("Output Tokens", f"{stats['completion_tokens']:,}")
                st.metric("Latency", f"{stats['latency']:.2f}s", f"{stats['speed']:.1f} tok/s" if stats['speed'] > 0 else None)

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
            log_event(log_container, st.session_state["logs_list"], "Starting execution sequence...")

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
                log_event(log_container, st.session_state["logs_list"], f"Pipeline failed: {str(e)}", level="ERROR")
                st.error(f"Error: {e}")
                return

    # =========================================================================
    # RIGHT PANEL: Document View & Deliverables
    # =========================================================================
    with col_right:
        if "meeting_result" not in st.session_state:
            st.info("👈 Upload meeting audio and select your template on the left to generate the minutes document.")
            st.markdown(
                """
                ```
                +-------------------------------------------------------------+
                |                    MEETING DOCUMENT VIEWER                  |
                |                                                             |
                |  • Executive Summary                                        |
                |  • Diarized Speaker Identifications & Mapping               |
                |  • Action Items Matrix (Owner, Deadline, Priority)          |
                |  • Full Annotated Transcript                                |
                |  • Download Ready (.docx)                                   |
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

            # Structured Content Tabs
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
                st.markdown("### Full Dialogue Record")
                for entry in result.transcript:
                    ts = f"`{entry.timestamp}` " if entry.timestamp else ""
                    st.markdown(f"{ts}**{entry.speaker}**: {entry.text}")

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
