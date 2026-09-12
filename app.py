import io
import json
import os
import tempfile
import time
import streamlit as st
from docx import Document
from docxtpl import DocxTemplate
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Configuration & Setup
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="AIMA — AI Meeting Assistant",
    page_icon="🎙️",
    layout="wide",
)

# Secrets & Defaults Initialization
DEFAULT_MODEL = "gemini-3.6-flash"
SECRET_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

if "api_key" not in st.session_state:
    st.session_state["api_key"] = SECRET_KEY

if "model_name" not in st.session_state:
    st.session_state["model_name"] = DEFAULT_MODEL


# -----------------------------------------------------------------------------
# Pydantic Schemas for Structured JSON Output
# -----------------------------------------------------------------------------
class ActionItem(BaseModel):
    task: str = Field(description="Description of the action item or task.")
    owner: str = Field(description="Person, role, or team assigned to this task.")
    deadline: str = Field(description="Due date, timeframe, or 'TBD' if unspecified.")
    priority: str = Field(description="High, Medium, or Low.")


class TranscriptEntry(BaseModel):
    speaker: str = Field(description="Name or label of the speaker.")
    timestamp: str = Field(description="Approximate timestamp (e.g., '01:23') or empty.")
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
# Gemini Audio Analysis with Progress Steps
# -----------------------------------------------------------------------------
def analyze_meeting_audio(
    audio_file_bytes: bytes,
    mime_type: str,
    api_key: str,
    model_name: str,
    progress_bar,
    status_text,
) -> MeetingMinutesReport:
    client = genai.Client(api_key=api_key)

    status_text.text("Step 1/3: Uploading audio buffer to Gemini File API...")
    progress_bar.progress(20)

    with tempfile.NamedTemporaryFile(delete=False, suffix="." + mime_type.split("/")[-1]) as tmp:
        tmp.write(audio_file_bytes)
        tmp_path = tmp.name

    try:
        uploaded_file = client.files.upload(file=tmp_path)
        progress_bar.progress(45)

        status_text.text(f"Step 2/3: Transcribing and diarizing speakers via {model_name}...")
        prompt = """
        You are an expert executive meeting assistant. Listen carefully to this meeting audio recording and:
        1. Produce a full transcript with speaker diarization (e.g., Speaker 1, or real names if introduced).
        2. Generate an executive summary.
        3. Identify all topics discussed and decisions made.
        4. Extract all explicit action items with owners, deadlines, and priorities.
        5. Return strictly valid JSON adhering to the provided schema.
        """

        response = client.models.generate_content(
            model=model_name,
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MeetingMinutesReport,
                temperature=0.2,
            ),
        )

        progress_bar.progress(85)
        status_text.text("Step 3/3: Formatting data into structured reports...")

        result_dict = json.loads(response.text)
        report = MeetingMinutesReport(**result_dict)

        progress_bar.progress(100)
        status_text.text("Analysis Complete!")
        time.sleep(0.5)
        return report

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# -----------------------------------------------------------------------------
# Automated DOCX Sample to Jinja Template Converter
# -----------------------------------------------------------------------------
def convert_sample_docx_to_template(sample_bytes: bytes) -> io.BytesIO:
    """Reads a user's completed DOCX sample, detects common headings, and replaces

    the sample text with jinja2 tags to create a reusable template.
    """
    doc = Document(io.BytesIO(sample_bytes))

    # Iterate over paragraphs and replace common static sample lines with template tags
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

    # Inspect tables for action items or agenda structures
    for table in doc.tables:
        if len(table.rows) >= 2:
            first_row_txt = " ".join(c.text.lower() for c in table.rows[0].cells)
            if any(w in first_row_txt for w in ["task", "action", "owner", "assignee", "due"]):
                # Keep header, replace subsequent rows with a jinja row pattern
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


# -----------------------------------------------------------------------------
# DOCX Renderers
# -----------------------------------------------------------------------------
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
        table.style = "Light Shading Accent 1" if "Light Shading Accent 1" in [s.name for s in doc.styles] else "Table Grid"
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
        st.caption("Upload meeting audio to generate diarized transcripts, agendas, and auto-filled Word reports.")

    with settings_col:
        with st.popover("⚙️ Settings"):
            st.markdown("### API & Model Configuration")

            # API Key Input (masked by default)
            current_key = st.session_state.get("api_key", "")
            new_key = st.text_input("Gemini API Key:", value=current_key, type="password")
            if new_key != current_key:
                st.session_state["api_key"] = new_key

            # Model Name Input
            current_model = st.session_state.get("model_name", DEFAULT_MODEL)
            new_model = st.text_input("Model Endpoint:", value=current_model)
            if new_model != current_model:
                st.session_state["model_name"] = new_model

            st.caption("Values persist across the current browser session.")

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
                        st.success("Successfully generated a dynamic template from your sample docx!")
                    except Exception as err:
                        st.error(f"Failed to parse sample docx: {err}")

    st.markdown("---")

    if st.button("🚀 Process Meeting Audio", type="primary", use_container_width=True):
        active_api_key = st.session_state.get("api_key")
        active_model = st.session_state.get("model_name", DEFAULT_MODEL)

        if not active_api_key:
            st.error("Missing Gemini API Key. Open ⚙️ Settings in the top-right corner to provide one.")
            return

        if not audio_file:
            st.error("Please upload an audio file.")
            return

        progress_bar = st.progress(0)
        status_text = st.empty()

        try:
            audio_bytes = audio_file.read()
            mime = audio_file.type if audio_file.type else "audio/mp3"

            report = analyze_meeting_audio(
                audio_bytes,
                mime,
                active_api_key,
                active_model,
                progress_bar,
                status_text,
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
