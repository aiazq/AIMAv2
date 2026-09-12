import base64
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
)

# Default complete endpoint URL and model
DEFAULT_ENDPOINT_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
SECRET_KEY = st.secrets.get("API_KEY", os.environ.get("API_KEY", st.secrets.get("GEMINI_API_KEY", "")))
SECRET_ENDPOINT = st.secrets.get("ENDPOINT_URL", os.environ.get("ENDPOINT_URL", DEFAULT_ENDPOINT_URL))

if "api_key" not in st.session_state:
    st.session_state["api_key"] = SECRET_KEY

if "endpoint_url" not in st.session_state:
    st.session_state["endpoint_url"] = SECRET_ENDPOINT


# -----------------------------------------------------------------------------
# Pydantic Schemas for Structured Output
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


class MeetingMinutesReport(BaseModel):
    title: str = Field(description="Descriptive title for the meeting.")
    date: str = Field(description="Date of the meeting or 'Undated'.")
    attendees: list[str] = Field(description="Detected participants.")
    executive_summary: str = Field(description="Executive summary of the meeting.")
    agenda_and_decisions: list[AgendaItem] = Field(description="Topic breakdowns and decisions.")
    action_items: list[ActionItem] = Field(description="Action items extracted.")
    transcript: list[TranscriptEntry] = Field(description="Full speaker-diarized transcript.")


# -----------------------------------------------------------------------------
# Universal API Dispatcher (Google Gemini REST & OpenAI-Compatible Audio Chat)
# -----------------------------------------------------------------------------
def analyze_meeting_audio_rest(
    audio_file_bytes: bytes,
    mime_type: str,
    api_key: str,
    endpoint_url: str,
    progress_bar,
    status_text,
) -> MeetingMinutesReport:
    """Dispatches audio to the designated complete endpoint URL."""
    status_text.text("Step 1/3: Encoding audio buffer...")
    progress_bar.progress(20)

    b64_audio = base64.b64encode(audio_file_bytes).decode("utf-8")
    progress_bar.progress(40)

    prompt = (
        "You are an executive meeting assistant. Listen carefully to this meeting audio recording:\n"
        "1. Produce a full diarized transcript identifying distinct speakers.\n"
        "2. Generate an executive summary.\n"
        "3. List all topics and decisions made.\n"
        "4. Extract all action items with owners, deadlines, and priorities.\n"
        "Return the output as pure valid JSON conforming strictly to this structure:\n"
        + json.dumps(MeetingMinutesReport.model_json_schema())
    )

    status_text.text(f"Step 2/3: Contacting endpoint ({endpoint_url})...")

    # Determine protocol based on endpoint structure
    is_gemini_native = "googleapis.com" in endpoint_url

    if is_gemini_native:
        # Full Gemini REST payload
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
        # OpenAI-compatible / Custom endpoint payload with input audio
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": b64_audio,
                                "format": mime_type.split("/")[-1].replace("mpeg", "mp3"),
                            },
                        },
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

    with httpx.Client(timeout=300.0) as client:
        response = client.post(endpoint_url, headers=headers, json=payload)

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} from provider: {response.text}")

    progress_bar.progress(85)
    status_text.text("Step 3/3: Parsing structured meeting minutes...")

    res_json = response.json()

    # Extract raw text depending on provider response layout
    if "candidates" in res_json:
        raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
    elif "choices" in res_json:
        raw_text = res_json["choices"][0]["message"]["content"]
    else:
        raw_text = json.dumps(res_json)

    # Clean potential markdown fences from non-enforcing endpoints
    cleaned_json = raw_text.strip()
    if cleaned_json.startswith("```json"):
        cleaned_json = cleaned_json[7:]
    if cleaned_json.startswith("```"):
        cleaned_json = cleaned_json[3:]
    if cleaned_json.endswith("```"):
        cleaned_json = cleaned_json[:-3]

    parsed_data = json.loads(cleaned_json.strip())
    report = MeetingMinutesReport(**parsed_data)

    progress_bar.progress(100)
    status_text.text("Processing Complete!")
    time.sleep(0.5)
    return report


# -----------------------------------------------------------------------------
# DOCX Dynamic Parser and Builders
# -----------------------------------------------------------------------------
def convert_sample_docx_to_template(sample_bytes: bytes) -> io.BytesIO:
    doc = Document(io.BytesIO(sample_bytes))

    for p in doc.paragraphs:
        txt = p.text.lower()
        if any(w in txt for w in ["summary", "overview", "background"]):
            p.text = "{{ executive_summary }}"
        elif any(w in txt for w in ["attendees", "participants", "present"]):
            p.text = "{% for a in attendees %}{{ a }}{% if not loop.last %}, {% endif %}{% endfor %}"
        elif any(w in txt for w in ["date", "meeting date"]):
            p.text = "Date: {{ date }}"
        elif any(w in txt for w in ["title", "subject", "meeting:"]):
            p.text = "{{ title }}"

    for table in doc.tables:
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
                    cells[0].text = "{% for item in action_items %}{{ item.task }}"
                    cells[1].text = "{{ item.owner }}"
                    cells[2].text = "{{ item.deadline }}"
                    cells[3].text = "{{ item.priority }}{% endfor %}"
                elif len(cells) >= 3:
                    cells[0].text = "{% for item in action_items %}{{ item.task }}"
                    cells[1].text = "{{ item.owner }}"
                    cells[2].text = "{{ item.deadline }}{% endfor %}"

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
    else:
        doc.add_paragraph("No specific action items identified.")

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
# Main Application UI
# -----------------------------------------------------------------------------
def main():
    top_col, settings_col = st.columns([0.88, 0.12])

    with top_col:
        st.title("🎙️ AIMA — AI Meeting Assistant")
        st.caption("Custom endpoint meeting assistant: transcripts, action items, and DOCX templates.")

    with settings_col:
        with st.popover("⚙️ Settings"):
            st.markdown("### Endpoint & Security")

            # Scoped CSS to permanently strip password-reveal buttons/eyes across browsers
            st.markdown(
                """
                <style>
                button[aria-label="Show password text"],
                button[aria-label="Hide password text"],
                input[type="password"]::-ms-reveal,
                input[type="password"]::-ms-clear {
                    display: none !important;
                    visibility: hidden !important;
                    pointer-events: none !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            # Masked write-only API key field
            has_key = bool(st.session_state.get("api_key"))
            status_indicator = "🟢 Key is securely set" if has_key else "🔴 No key configured"
            st.caption(f"Status: **{status_indicator}**")

            new_key_input = st.text_input(
                "Update API Key / Bearer Token:",
                value="",
                type="password",
                placeholder="Paste new key to set/replace..." if not has_key else "•••••••••••••••• (Leave blank to keep)",
                help="Once entered, the key cannot be inspected or toggled visible.",
            )

            if new_key_input.strip():
                st.session_state["api_key"] = new_key_input.strip()
                st.rerun()

            # Complete Endpoint URL Input
            current_endpoint = st.session_state.get("endpoint_url", DEFAULT_ENDPOINT_URL)
            new_endpoint = st.text_input(
                "Complete Meeting Endpoint URL:",
                value=current_endpoint,
                help="Enter the full target URL (e.g., [https://api.openai.com/v1/chat/completions](https://api.openai.com/v1/chat/completions) or your custom proxy/gateway).",
            )
            if new_endpoint != current_endpoint:
                st.session_state["endpoint_url"] = new_endpoint.strip()

            st.caption("Settings persist for the active session and are never echoed.")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("1. Audio Source")
        audio_file = st.file_uploader(
            "Upload meeting recording",
            type=["mp3", "wav", "m4a", "ogg", "aac", "mp4"],
        )
        if audio_file:
            st.audio(audio_file)

    with col2:
        st.subheader("2. Meeting Document Template")
        doc_choice = st.radio(
            "Template Strategy:",
            ["Default Clean Format", "Upload Jinja2-Tagged .docx", "Convert a Completed Sample .docx into Template"],
            horizontal=True,
        )

        template_bytes = None

        if doc_choice == "Upload Jinja2-Tagged .docx":
            uploaded_tpl = st.file_uploader("Upload Word Template (.docx)", type=["docx"], key="tagged_docx")
            if uploaded_tpl:
                template_bytes = uploaded_tpl.getvalue()

        elif doc_choice == "Convert a Completed Sample .docx into Template":
            sample_file = st.file_uploader(
                "Upload an existing finished meeting Word document",
                type=["docx"],
                help="AIMA will analyze headings, sections, and tables in your sample file and auto-insert dynamic placeholders.",
                key="sample_docx",
            )
            if sample_file:
                with st.spinner("Analyzing document structure and creating template..."):
                    try:
                        template_bytes = convert_sample_docx_to_template(sample_file.getvalue()).getvalue()
                        st.success("Successfully generated dynamic template from sample!")
                    except Exception as err:
                        st.error(f"Failed to parse sample docx: {err}")

    st.markdown("---")

    if st.button("🚀 Process Meeting Audio", type="primary", use_container_width=True):
        active_api_key = st.session_state.get("api_key")
        active_endpoint = st.session_state.get("endpoint_url", DEFAULT_ENDPOINT_URL)

        if not active_api_key:
            st.error("Missing API Key. Open ⚙️ Settings in the top-right corner to configure one.")
            return

        if not active_endpoint:
            st.error("Missing complete endpoint URL. Check your settings.")
            return

        if not audio_file:
            st.error("Please upload an audio file.")
            return

        progress_bar = st.progress(0)
        status_text = st.empty()

        try:
            audio_bytes = audio_file.read()
            mime = audio_file.type if audio_file.type else "audio/mp3"

            report = analyze_meeting_audio_rest(
                audio_file_bytes=audio_bytes,
                mime_type=mime,
                api_key=active_api_key,
                endpoint_url=active_endpoint,
                progress_bar=progress_bar,
                status_text=status_text,
            )
            st.session_state["meeting_result"] = report
            st.session_state["saved_template_bytes"] = template_bytes

        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"Processing failed: {e}")
            return

    # -------------------------------------------------------------------------
    # Render Output Tabs
    # -------------------------------------------------------------------------
    if "meeting_result" in st.session_state:
        result: MeetingMinutesReport = st.session_state["meeting_result"]
        active_template = st.session_state.get("saved_template_bytes")

        st.markdown("## 📋 Extracted Meeting Summary")

        tab1, tab2, tab3, tab4 = st.tabs(["📌 Overview", "✅ Action Items", "📝 Diarized Transcript", "💾 Export Document"])

        with tab1:
            st.header(result.title)
            st.caption(f"**Date:** {result.date} | **Attendees:** {', '.join(result.attendees)}")
            st.markdown("### Executive Summary")
            st.write(result.executive_summary)

            st.markdown("### Agendas & Decisions")
            for item in result.agenda_and_decisions:
                with st.expander(f"Topic: {item.topic}", expanded=True):
                    st.write(item.discussion_summary)
                    if item.decisions_made:
                        st.markdown("**Decisions:**")
                        for dec in item.decisions_made:
                            st.markdown(f"- {dec}")

        with tab2:
            st.subheader("Action Items")
            if result.action_items:
                st.dataframe([item.model_dump() for item in result.action_items], use_container_width=True)
            else:
                st.info("No action items detected.")

        with tab3:
            st.subheader("Full Diarized Transcript")
            for entry in result.transcript:
                ts = f"`{entry.timestamp}` " if entry.timestamp else ""
                st.markdown(f"{ts}**{entry.speaker}**: {entry.text}")

        with tab4:
            st.subheader("Download Word Document")
            col_a, col_b = st.columns(2)

            with col_a:
                if active_template:
                    try:
                        doc_io = render_template_docx(active_template, result)
                        st.download_button(
                            label="📥 Download Filled Template (.docx)",
                            data=doc_io,
                            file_name=f"{result.title.replace(' ', '_')}_Minutes.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                    except Exception as ex:
                        st.error(f"Template rendering issue: {ex}. Falling back to standard layout.")
                        fallback_io = build_default_docx(result)
                        st.download_button(
                            label="📥 Download Standard Layout (.docx)",
                            data=fallback_io,
                            file_name="Meeting_Minutes.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                else:
                    default_io = build_default_docx(result)
                    st.download_button(
                        label="📥 Download Standard Document (.docx)",
                        data=default_io,
                        file_name=f"{result.title.replace(' ', '_')}_Minutes.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )

            with col_b:
                json_bytes = json.dumps(result.model_dump(), indent=2)
                st.download_button(
                    label="📥 Export Raw JSON Data",
                    data=json_bytes,
                    file_name="meeting_output.json",
                    mime="application/json",
                )


if __name__ == "__main__":
    main()
