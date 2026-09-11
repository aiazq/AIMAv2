import io
import json
import os
import tempfile
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
    page_title="AI Meeting Minutes & Action Items Assistant",
    page_icon="🎙️",
    layout="wide",
)

# Fetch API Key from Streamlit Secrets or Environment Variable
API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

if not API_KEY:
    st.sidebar.warning("⚠️ `GEMINI_API_KEY` not detected in secrets.")
    API_KEY = st.sidebar.text_input("Enter your Gemini API Key:", type="password")


# -----------------------------------------------------------------------------
# Pydantic Schemas for Structured JSON Output
# -----------------------------------------------------------------------------
class ActionItem(BaseModel):
    task: str = Field(description="Description of the action item or task.")
    owner: str = Field(description="Person, role, or team assigned to this task.")
    deadline: str = Field(description="Due date, timeframe, or 'TBD' if unspecified.")
    priority: str = Field(description="High, Medium, or Low.")


class TranscriptEntry(BaseModel):
    speaker: str = Field(description="Name or label of the speaker (e.g., 'Speaker 1' or identified name).")
    timestamp: str = Field(description="Approximate timestamp (e.g., '01:23') or empty string.")
    text: str = Field(description="What the speaker said.")


class AgendaItem(BaseModel):
    topic: str = Field(description="Agenda topic discussed.")
    discussion_summary: str = Field(description="Summary of discussions regarding this topic.")
    decisions_made: list[str] = Field(description="Key conclusions or decisions reached on this topic.")


class MeetingMinutesReport(BaseModel):
    title: str = Field(description="Concise, descriptive title for the meeting.")
    date: str = Field(description="Date of the meeting or 'Undated' if not mentioned.")
    attendees: list[str] = Field(description="List of all detected participants or speakers.")
    executive_summary: str = Field(description="High-level 2-3 paragraph executive summary of the entire meeting.")
    agenda_and_decisions: list[AgendaItem] = Field(description="Topic-by-topic breakdowns and decisions.")
    action_items: list[ActionItem] = Field(description="List of action items and commitments extracted from the conversation.")
    transcript: list[TranscriptEntry] = Field(description="Full speaker-diarized transcript.")


# -----------------------------------------------------------------------------
# Gemini Multimodal Audio Processor
# -----------------------------------------------------------------------------
def analyze_meeting_audio(audio_file_bytes: bytes, mime_type: str, api_key: str) -> MeetingMinutesReport:
    """Uploads the audio to Gemini File API and generates structured meeting data."""
    client = genai.Client(api_key=api_key)

    # Save to a temporary file to upload via Gemini Files API
    with tempfile.NamedTemporaryFile(delete=False, suffix="." + mime_type.split("/")[-1]) as tmp:
        tmp.write(audio_file_bytes)
        tmp_path = tmp.name

    try:
        uploaded_file = client.files.upload(file=tmp_path)

        prompt = """
        You are an expert executive meeting assistant. Listen carefully to this meeting audio recording and:
        1. Produce a full, accurate transcript with speaker diarization (differentiating voices, tagging Speaker 1, 2, or real names if introduced).
        2. Generate a comprehensive executive summary.
        3. Identify all topics discussed, key arguments, and specific decisions made.
        4. Extract all explicit and implicit action items with assigned owners, deadlines (or TBD), and priorities.
        5. Return the result strictly adhering to the requested JSON schema.
        """

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MeetingMinutesReport,
                temperature=0.2,
            ),
        )

        result_dict = json.loads(response.text)
        return MeetingMinutesReport(**result_dict)

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# -----------------------------------------------------------------------------
# DOCX Generation Functions
# -----------------------------------------------------------------------------
def render_template_docx(template_bytes: bytes, data: MeetingMinutesReport) -> io.BytesIO:
    """Renders data into a user-supplied docxtpl template."""
    doc = DocxTemplate(io.BytesIO(template_bytes))
    context = data.model_dump()
    doc.render(context)
    out_stream = io.BytesIO()
    doc.save(out_stream)
    out_stream.seek(0)
    return out_stream


def build_default_docx(data: MeetingMinutesReport) -> io.BytesIO:
    """Builds a professionally formatted Word document from scratch if no template is provided."""
    doc = Document()

    # Title & Metadata
    title_p = doc.add_heading(data.title, level=0)
    doc.add_paragraph(f"Date: {data.date}")
    doc.add_paragraph(f"Attendees: {', '.join(data.attendees) if data.attendees else 'Not explicitly mentioned'}")
    doc.add_paragraph()

    # Executive Summary
    doc.add_heading("1. Executive Summary", level=1)
    doc.add_paragraph(data.executive_summary)

    # Agenda & Discussions
    doc.add_heading("2. Agenda Items & Decisions", level=1)
    for idx, item in enumerate(data.agenda_and_decisions, 1):
        doc.add_heading(f"2.{idx} {item.topic}", level=2)
        doc.add_paragraph(f"Summary: {item.discussion_summary}")
        if item.decisions_made:
            doc.add_paragraph("Decisions Reached:", style="List Bullet")
            for dec in item.decisions_made:
                p = doc.add_paragraph(dec, style="List Bullet 2")

    # Action Items Table
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

    # Transcript
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
# Streamlit UI
# -----------------------------------------------------------------------------
def main():
    st.title("🎙️ AI Meeting Assistant & Minutes Generator")
    st.markdown(
        "Upload audio from your team discussions, meetings, or interviews. "
        "The assistant generates structured notes, transcripts with diarization, and formatted Word documents."
    )

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("1. Meeting Audio File")
        audio_file = st.file_uploader(
            "Upload audio recording",
            type=["mp3", "wav", "m4a", "ogg", "aac", "mp4"],
            help="Supports standard formats like MP3, WAV, M4A, etc. Gemini natively processes multi-speaker audio.",
        )
        if audio_file:
            st.audio(audio_file)

    with col2:
        st.subheader("2. Custom DOCX Template (Optional)")
        template_file = st.file_uploader(
            "Upload a .docx template",
            type=["docx"],
            help=(
                "Optional: Upload a Word template using Jinja tags like {{ title }}, {{ executive_summary }}, "
                "or loops like {% for item in action_items %} {{ item.task }} ({{ item.owner }}) {% endfor %}. "
                "If omitted, a standard professional document will be generated."
            ),
        )

        with st.expander("ℹ️ How to write custom `.docx` templates"):
            st.markdown(
                """
                In your Word (`.docx`) file, insert variables using `docxtpl` / Jinja2 syntax:
                - `{{ title }}`
                - `{{ date }}`
                - `{{ executive_summary }}`
                - `{% for a in attendees %}{{ a }}, {% endfor %}`
                - Action items table:
                  `{% for item in action_items %}`
                  `{{ item.task }} | {{ item.owner }} | {{ item.deadline }}`
                  `{% endfor %}`
                """
            )

    st.markdown("---")

    if st.button("🚀 Process Meeting", type="primary", use_container_width=True):
        if not API_KEY:
            st.error("Please supply a valid Gemini API Key in the sidebar or via Streamlit secrets.")
            return

        if not audio_file:
            st.error("Please upload an audio file before proceeding.")
            return

        with st.spinner("Processing audio with Gemini (Transcribing, Diarizing & Extracting Actions)..."):
            try:
                audio_bytes = audio_file.read()
                mime = audio_file.type if audio_file.type else "audio/mp3"
                result = analyze_meeting_audio(audio_bytes, mime, API_KEY)
                st.session_state["meeting_result"] = result
                st.success("Meeting analyzed successfully!")
            except Exception as e:
                st.error(f"Error while processing audio: {e}")
                return

    # -------------------------------------------------------------------------
    # Render Results and Export Options
    # -------------------------------------------------------------------------
    if "meeting_result" in st.session_state:
        result: MeetingMinutesReport = st.session_state["meeting_result"]

        st.markdown("## 📋 Extracted Meeting Summary")

        tab1, tab2, tab3, tab4 = st.tabs(["📌 Overview", "✅ Action Items", "📝 Full Transcript", "💾 Export DOCX"])

        with tab1:
            st.header(result.title)
            st.caption(f"**Date:** {result.date} | **Attendees:** {', '.join(result.attendees)}")
            st.markdown("### Executive Summary")
            st.write(result.executive_summary)

            st.markdown("### Agenda & Key Decisions")
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
                table_data = [item.model_dump() for item in result.action_items]
                st.dataframe(table_data, use_container_width=True)
            else:
                st.info("No action items detected.")

        with tab3:
            st.subheader("Diarized Transcript")
            for entry in result.transcript:
                ts_badge = f"`{entry.timestamp}` " if entry.timestamp else ""
                st.markdown(f"{ts_badge}**{entry.speaker}**: {entry.text}")

        with tab4:
            st.subheader("Generate & Download Word Document")

            col_a, col_b = st.columns(2)
            with col_a:
                if template_file:
                    st.info("A custom template file is loaded. Click below to render into your template.")
                    try:
                        doc_io = render_template_docx(template_file.getvalue(), result)
                        st.download_button(
                            label="📥 Download Filled Template (.docx)",
                            data=doc_io,
                            file_name=f"Meeting_Minutes_{result.date or 'Summary'}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                    except Exception as err:
                        st.error(f"Failed to render into custom template: {err}")
                else:
                    st.write("Using default clean executive template.")
                    default_doc_io = build_default_docx(result)
                    st.download_button(
                        label="📥 Download Standard Document (.docx)",
                        data=default_doc_io,
                        file_name=f"Meeting_Minutes_{result.date or 'Summary'}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )

            with col_b:
                json_str = json.dumps(result.model_dump(), indent=2)
                st.download_button(
                    label="📥 Download Raw JSON Data",
                    data=json_str,
                    file_name="meeting_data.json",
                    mime="application/json",
                )


if __name__ == "__main__":
    main()
