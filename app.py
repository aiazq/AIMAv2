import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import datetime
import io
from html import escape
import json
import os
import random
import re
import time
import httpx
import streamlit as st
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docxtpl import DocxTemplate
from pydantic import BaseModel, Field, field_validator

# -----------------------------------------------------------------------------
# Configuration & Styling
# -----------------------------------------------------------------------------
_favicon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "aima_favicon.png")
st.set_page_config(
    page_title="AIMA — AI Meeting Assistant",
    page_icon=_favicon if os.path.exists(_favicon) else "🎙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    :root,
    .stApp,
    [data-testid="stAppViewContainer"] {
        --aima-bg: var(--background-color, var(--st-color-background, #edf3f8));
        --aima-ink: var(--text-color, var(--st-color-text, #13283f));
        --aima-blue: #2e6ea8;
        --aima-blue-dark: #1b4b78;
        --aima-sky: var(--secondary-background-color, var(--st-color-secondary-background, #e8f2fb));
        --aima-line: color-mix(in srgb, var(--aima-ink) 22%, transparent);
        --aima-panel: var(--secondary-background-color, var(--st-color-secondary-background, #f7fbff));
        --aima-muted: color-mix(in srgb, var(--aima-ink) 62%, transparent);
        --aima-green: #2b8b68;
        --aima-shadow: rgba(25, 55, 83, 0.16);
    }

    html, body, [data-testid="stAppViewContainer"] {
        background: var(--aima-bg) !important;
        color: var(--aima-ink);
    }

    .block-container {
        max-width: 1380px !important;
        padding-top: 4.4rem !important;
        padding-bottom: 2.5rem !important;
        padding-left: clamp(0.75rem, 3vw, 2.75rem) !important;
        padding-right: clamp(0.75rem, 3vw, 2.75rem) !important;
    }

    [data-testid="stHeader"] { background: transparent !important; }
    div[data-testid="stPopover"] { margin-top: 0.65rem; }

    button[aria-label="Show password text"],
    button[aria-label="Hide password text"],
    input[type="password"]::-ms-reveal,
    input[type="password"]::-ms-clear {
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
    }

    .aima-brand {
        display: inline-flex; align-items: center; gap: 0.7rem;
        background: linear-gradient(180deg, var(--aima-panel) 0%, var(--aima-bg) 100%);
        border: 1px solid var(--aima-line); border-radius: 8px; padding: 7px 12px;
        box-shadow: inset 0 1px 0 color-mix(in srgb, var(--aima-ink) 12%, transparent), 0 2px 5px var(--aima-shadow);
    }
    .aima-brand img { width: 34px; height: 34px; object-fit: contain; display: block; }
    .aima-brand-text { color: var(--aima-ink); font-size: 0.98rem; font-weight: 800; line-height: 1.15; white-space: nowrap; }
    .aima-brand-sub { color: var(--aima-muted); display: block; font-size: 0.69rem; font-weight: 600; margin-top: 0.18rem; }

    .retro-titlebar {
        margin: 0.65rem 0 0.95rem; padding: 0.43rem 0.75rem;
        color: #ffffff; background: linear-gradient(180deg, #4b91c8 0%, #245e96 52%, #1c4a78 100%);
        border: 1px solid #173d63; border-radius: 5px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.35), 0 2px 4px var(--aima-shadow);
        font-size: 0.72rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase;
    }
    .status-strip {
        display: flex; align-items: center; justify-content: space-between; gap: 0.75rem;
        margin: 0.15rem 0 1rem; padding: 0.52rem 0.72rem; background: var(--aima-panel);
        border: 1px solid var(--aima-line); border-radius: 4px; color: var(--aima-muted); font-size: 0.73rem;
        box-shadow: inset 0 1px 2px rgba(21, 59, 92, 0.05);
    }
    .status-strip strong { color: var(--aima-blue-dark); }
    .status-dot { color: var(--aima-green); font-size: 0.9rem; vertical-align: -0.04em; }

    .section-label { color: var(--aima-blue-dark); font-size: 0.74rem; font-weight: 900; letter-spacing: 0.06em; text-transform: uppercase; margin: 0 0 0.28rem; }
    .section-help { color: var(--aima-muted); font-size: 0.76rem; line-height: 1.45; margin: 0 0 0.7rem; }
    .field-caption { color: var(--aima-muted); font-size: 0.72rem; margin: 0.3rem 0 0.65rem; }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--aima-panel) !important;
        border: 1px solid var(--aima-line) !important; border-radius: 7px !important;
        box-shadow: 0 3px 8px var(--aima-shadow), inset 0 1px 0 color-mix(in srgb, var(--aima-ink) 10%, transparent);
    }
    div[data-testid="stVerticalBlockBorderWrapper"] > div { border-radius: 7px !important; }

    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, var(--aima-panel) 0%, var(--aima-sky) 100%);
        border: 1px solid var(--aima-line); border-radius: 5px; padding: 0.5rem 0.7rem;
        box-shadow: inset 0 1px 0 color-mix(in srgb, var(--aima-ink) 10%, transparent), 0 1px 2px var(--aima-shadow);
    }
    div[data-testid="stMetricLabel"] { color: var(--aima-muted); font-size: 0.67rem; font-weight: 700; }
    div[data-testid="stMetricValue"] { color: var(--aima-ink); font-size: 1.15rem; font-weight: 800; }

    .empty-desk { text-align: center; padding: 2.3rem 1rem 2.6rem; }
    .empty-desk-icon { display: inline-grid; place-items: center; width: 3.3rem; height: 3.3rem; border-radius: 50%; color: #fff; background: linear-gradient(180deg, #77afd8, #2a669b); border: 2px solid #d7ebfa; box-shadow: inset 0 1px 0 rgba(255,255,255,0.55), 0 3px 7px var(--aima-shadow); font-size: 1.45rem; }
    .empty-desk-title { color: var(--aima-ink); font-size: 1.32rem; font-weight: 900; margin: 0.85rem 0 0.4rem; }
    .empty-desk-copy { color: var(--aima-muted); max-width: 34rem; margin: 0 auto 1.25rem; line-height: 1.55; font-size: 0.86rem; }
    .desk-steps { display: flex; justify-content: center; flex-wrap: wrap; gap: 0.45rem; }
    .desk-step { padding: 0.35rem 0.62rem; background: var(--aima-sky); border: 1px solid var(--aima-line); border-radius: 4px; color: var(--aima-blue-dark); font-size: 0.7rem; font-weight: 800; }

    .result-heading { color: var(--aima-ink); font-size: clamp(1.25rem, 2.1vw, 1.85rem); font-weight: 900; line-height: 1.12; margin: 0; }
    .result-meta { color: var(--aima-muted); font-size: 0.76rem; margin-top: 0.35rem; line-height: 1.5; }
    .result-ribbon { color: #ffffff; background: linear-gradient(180deg, #5d9dd0, #2a659b); border: 1px solid #204e7a; border-radius: 4px; padding: 0.4rem 0.58rem; text-align: center; font-size: 0.69rem; font-weight: 800; box-shadow: inset 0 1px 0 rgba(255,255,255,0.35); }
    .summary-box { background: var(--aima-panel); border: 1px solid var(--aima-line); border-left: 4px solid #3c83b8; border-radius: 4px; padding: 0.9rem 1rem; color: var(--aima-ink); line-height: 1.6; font-size: 0.88rem; box-shadow: inset 0 1px 3px rgba(21, 59, 92, 0.04); }
    .summary-label { color: var(--aima-blue-dark); font-size: 0.7rem; font-weight: 900; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 0.35rem; }

    .action-card { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0.4rem 0.8rem; align-items: center; margin: 0.45rem 0; padding: 0.72rem 0.8rem; background: linear-gradient(180deg, var(--aima-panel), var(--aima-sky)); border: 1px solid var(--aima-line); border-radius: 4px; box-shadow: inset 0 1px 0 color-mix(in srgb, var(--aima-ink) 10%, transparent); }
    .action-task { color: var(--aima-ink); font-weight: 800; line-height: 1.38; font-size: 0.84rem; }
    .action-meta { color: var(--aima-muted); font-size: 0.71rem; margin-top: 0.26rem; }
    .priority { border-radius: 3px; padding: 0.25rem 0.45rem; font-size: 0.65rem; font-weight: 900; white-space: nowrap; text-transform: uppercase; }
    .priority-high { color: #9e2f2f; background: #fde6e6; border: 1px solid #efb7b7; }
    .priority-medium { color: #8d650b; background: #fff4d5; border: 1px solid #ead18a; }
    .priority-low { color: #24724f; background: #e2f5ea; border: 1px solid #a9d9bc; }
    .priority-default { color: #526273; background: #edf1f5; border: 1px solid #cad4dd; }

    .attendee-card { padding: 0.65rem 0.8rem; margin: 0.4rem 0; background: var(--aima-panel); border: 1px solid var(--aima-line); border-radius: 4px; }
    .attendee-name { color: var(--aima-ink); font-size: 0.84rem; font-weight: 800; }
    .attendee-role { color: var(--aima-muted); font-size: 0.71rem; margin-top: 0.15rem; }

    .transcript-entry { display: grid; grid-template-columns: 2.1rem minmax(0, 1fr); gap: 0.65rem; padding: 0.7rem 0; border-bottom: 1px solid var(--aima-line); }
    .transcript-entry:last-child { border-bottom: 0; }
    .speaker-avatar { display: grid; place-items: center; width: 2rem; height: 2rem; color: #fff; background: linear-gradient(180deg, #77afd8, #2b689f); border: 1px solid #23577f; border-radius: 4px; font-size: 0.65rem; font-weight: 900; box-shadow: inset 0 1px 0 rgba(255,255,255,0.4); }
    .transcript-speaker { color: var(--aima-ink); font-size: 0.8rem; font-weight: 900; }
    .transcript-time { color: var(--aima-muted); font-size: 0.68rem; margin-left: 0.45rem; font-weight: 600; }
    .transcript-text { color: var(--aima-ink); font-size: 0.82rem; line-height: 1.55; margin-top: 0.2rem; }

    .terminal-container { font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; font-size: 0.72rem; background: #101b29; color: #c9d7e4; padding: 0.8rem; border: 1px solid #243b52; border-radius: 4px; height: 260px; overflow-y: auto; line-height: 1.48; white-space: pre-wrap; display: flex; flex-direction: column; box-shadow: inset 0 2px 5px rgba(0,0,0,0.18); }
    .log-quip { color: #91adbf !important; font-size: 0.67rem !important; font-style: italic; padding-left: 0.45rem; display: block; margin: 2px 0; }
    .log-info { color: #66c3f1; } .log-debug { color: #9aaaba; } .log-warn { color: #f2c35d; } .log-error { color: #f27d7d; } .log-success { color: #68d49a; }

    button[kind="primary"] { background: linear-gradient(180deg, #72b1df 0%, #2c72a8 48%, #20577f 100%) !important; border: 1px solid #174566 !important; box-shadow: inset 0 1px 0 rgba(255,255,255,0.45), 0 2px 3px var(--aima-shadow) !important; }
    button[kind="secondary"] { background: linear-gradient(180deg, var(--aima-panel), var(--aima-sky)) !important; border: 1px solid var(--aima-line) !important; color: var(--aima-ink) !important; box-shadow: inset 0 1px 0 color-mix(in srgb, var(--aima-ink) 10%, transparent), 0 1px 2px var(--aima-shadow) !important; }
    div[data-baseweb="tab-list"] { gap: 0.2rem; border-bottom: 1px solid var(--aima-line); }
    button[data-baseweb="tab"] { color: var(--aima-blue-dark); font-size: 0.73rem; font-weight: 800; padding: 0.55rem 0.45rem; }

    @media (max-width: 760px) {
        .block-container { padding-top: 3.9rem !important; padding-left: 0.7rem !important; padding-right: 0.7rem !important; }
        .aima-brand-text { font-size: 0.88rem; }
        .retro-titlebar { font-size: 0.64rem; }
        .status-strip { align-items: flex-start; flex-direction: column; }
        .action-card { grid-template-columns: 1fr; }
        .result-ribbon { margin-top: 0.35rem; }
        button[data-baseweb="tab"] { font-size: 0.64rem; padding-left: 0.2rem; padding-right: 0.2rem; }
    }

    /* Streamlit's theme selector varies by release; these selectors cover the
       app root, body, and inherited theme wrapper when a user selects Dark. */
    body[data-theme="dark"],
    [data-theme="dark"],
    .stApp[data-theme="dark"] {
        --aima-bg: #0e1117;
        --aima-ink: #f2f6fa;
        --aima-blue: #78b9e6;
        --aima-blue-dark: #a9d7f4;
        --aima-sky: #1b2b3a;
        --aima-line: #3b5368;
        --aima-panel: #182532;
        --aima-muted: #b0c0cc;
        --aima-shadow: rgba(0, 0, 0, 0.42);
    }


    body[data-theme="dark"] .priority-high,
    html[data-theme="dark"] .priority-high,
    [data-theme="dark"] .priority-high { color: #ffc0c0; background: #512d34; border-color: #814852; }
    body[data-theme="dark"] .priority-medium,
    html[data-theme="dark"] .priority-medium,
    [data-theme="dark"] .priority-medium { color: #f5d98b; background: #4d3c1c; border-color: #80682b; }
    body[data-theme="dark"] .priority-low,
    html[data-theme="dark"] .priority-low,
    [data-theme="dark"] .priority-low { color: #9be0b8; background: #1d4638; border-color: #39785e; }
    body[data-theme="dark"] .priority-default,
    html[data-theme="dark"] .priority-default,
    [data-theme="dark"] .priority-default { color: #c2d0da; background: #293744; border-color: #526879; }
    </style>
    """,
    unsafe_allow_html=True,
)



def _brand_logo_uri(which: str = "aima_lockup.png") -> str:
    """Inline data-URI for a brand asset, or "" when the file is absent.

    Base64-embedded rather than served as a static file so it works on Community
    Cloud with no static-serving config, and so the mark cannot 404 into a broken
    image icon. Callers fall back to a text title when this returns "".
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", which)
    try:
        with open(path, "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""


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
# Data Schemas
# -----------------------------------------------------------------------------
class Attendee(BaseModel):
    name: str = Field(description="Full name of attendee.")
    designation: str = Field(default="", description="Role or title if mentioned, otherwise empty.")


class ActionItem(BaseModel):
    task: str = Field(
        description=("Description of the action item or task, i.e. the work being "
                     "tracked (the 'Agenda Items' column of a minutes table)."),
    )
    owner: str = Field(description="Person, role, or team assigned to this task.")
    department: str = Field(default="", description="Relevant department or team if identifiable.")
    deadline: str = Field(description="Due date, timeframe, or 'TBD' if unspecified.")
    remarks: str = Field(
        default="",
        description="Priority (High/Medium/Low) and any other remarks for this item.",
    )

    @field_validator("remarks", mode="before")
    @classmethod
    def _accept_legacy_priority(cls, v, info):
        """Accept the legacy 'priority' field name as an alias for 'remarks' (D13)."""
        if v is None:
            data = info.data if hasattr(info, "data") else {}
            return data.get("priority", "") or ""
        return v

    model_config = {"populate_by_name": True, "extra": "ignore"}


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
    meeting_time: str = Field(default="", description="Meeting time range if mentioned.")
    minute_taker: str = Field(default="", description="Minute taker(s) if specified.")
    attendees: list[Attendee] = Field(description="Detected participants with designations.")
    detected_speakers: list[DetectedSpeaker] = Field(
        default_factory=list,
        description="List of detected speakers and any names inferred from introductions or dialog.",
    )
    executive_summary: str = Field(description="Executive summary of the meeting in English.")
    agenda_and_decisions: list[AgendaItem] = Field(description="Topic breakdowns and decisions in English.")
    action_items: list[ActionItem] = Field(description="Action items extracted in English.")
    next_meeting_date: str = Field(default="TBD", description="Date of the next meeting if agreed.")
    next_meeting_time: str = Field(default="TBD", description="Time of next meeting if agreed.")
    next_meeting_agenda_focus: str = Field(default="", description="Agenda focus of upcoming meeting.")
    closing_remarks: str = Field(default="The meeting was concluded.", description="Meeting closing statement.")
    transcript: list[TranscriptEntry] = Field(description="Bilingual speaker-diarized transcript.")


# AI Semantic Transformation Schemas
#
# A SECTION of the document may hold a LIST rather than a single value: the
# client writes a placeholder paragraph ("<Action Items Table goes here>", "The
# complete transcript ... goes here") and expects that section to be filled with
# every item. ParagraphRule therefore carries an optional `collection`: when set,
# the assembler expands the paragraph into a `{%p for ... %}` loop.
#
# Without this, such a paragraph is indistinguishable from leftover dummy text
# and gets PURGEd — the heading survives and the whole section silently vanishes.
class ParagraphRule(BaseModel):
    p_id: str = Field(description="Paragraph ID (e.g. 'p_0').")
    action: str = Field(
        description=("'KEEP_STATIC', 'REPLACE_TEMPLATE', or 'PURGE'. "
                     "Use 'REPLACE_TEMPLATE' + collection for a placeholder that "
                     "should become a repeating list."),
    )
    cleaned_template_text: str = Field(
        default="",
        description=(
            "Template text for this paragraph, using variables only "
            "(e.g. 'Meeting Title: {{ title }}'). For a repeating section use the "
            "collection's item variable, e.g. '{{ item.task }} — {{ item.owner }}'. "
            "Do NOT add any '{%' or '%}' tags; the assembler writes the loop tags."
        ),
    )
    collection: str | None = Field(
        default=None,
        description=(
            "Set ONLY when this paragraph is a placeholder for a repeating list, "
            "e.g. a '<... goes here>' line under an ACTION ITEMS or TRANSCRIPT "
            "heading, or a 'TODO'/'TBD' row the client expects to be filled. "
            "Allowed values: 'action_items' (loop variable 'item'), "
            "'attendees' ('attendee'), 'transcript' ('entry'), "
            "'agenda_and_decisions' ('agenda'), 'detected_speakers' ('speaker'). "
            "The text must then reference that loop variable. "
            "Leave null for ordinary one-off values."
        ),
    )


# Per-collection loop variable name. The LLM is told these names, but we treat the
# list itself as authoritative: if the model writes a different variable in the
# body, the loop variable is renamed to match it (a mismatch renders as empty
# strings in docxtpl, with no error at all).
_DOC_COLLECTIONS = {
    "action_items": "item",
    "attendees": "attendee",
    "transcript": "entry",
    "agenda_and_decisions": "agenda",
    "detected_speakers": "speaker",
}


class TableRule(BaseModel):
    t_id: str = Field(description="Table ID (e.g. 't_0').")
    table_type: str = Field(description="'ATTENDEES_TABLE', 'ACTION_ITEMS_TABLE', 'STATIC_TABLE', or 'PURGE'.")
    header_rows_count: int = Field(default=1, description="Number of header rows to keep.")


class DocumentAnalysisPlan(BaseModel):
    paragraphs: list[ParagraphRule] = Field(default_factory=list)
    tables: list[TableRule] = Field(default_factory=list)


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


def call_llm_json(endpoint_base: str, api_key: str, model_name: str, prompt: str,
                  max_attempts: int = 4) -> str:
    """Call an OpenAI-compatible (or Gemini) endpoint and return the raw JSON text.

    Transient failures are retried with exponential backoff. Free-tier Gemini
    projects answer a burst of calls with 429 RESOURCE_EXHAUSTED and a
    "reset after 49s" hint; without a retry the whole document conversion dies on
    a limit that clears itself. Permanent failures (401/403/400/404) are raised
    immediately — retrying those only wastes the user's time and hides the cause.
    """
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
            # Gateways that stream by default (9router/LiteLLM/vLLM proxies) return
            # text/event-stream here, which breaks res.json(). Be explicit.
            "stream": False,
            "temperature": 0.1,
        }

    # Status codes worth another go: rate limits and server-side hiccups.
    RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
    last_error = "no attempt made"

    for attempt in range(max_attempts):
        if attempt:
            # 1s, 2s, 4s — enough for a "reset after 2s" burst, and a single retry
            # after a long Retry-After is handled by the cap below.
            delay = min(2 ** (attempt - 1), 8)
            time.sleep(delay)

        try:
            with httpx.Client(timeout=90.0) as client:
                res = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            last_error = f"{type(e).__name__}: {e}"
            continue

        if res.status_code != 200:
            last_error = f"HTTP {res.status_code} from provider: {res.text[:400]}"
            if res.status_code in RETRYABLE:
                continue
            raise RuntimeError(last_error)

        if not (res.headers.get("content-type") or "").lower().startswith(
                "application/json"):
            # A proxy that ignored stream:false and sent text/event-stream. Often
            # clears on retry; if it never does, fail with the body for diagnosis.
            last_error = (
                "Provider returned a non-JSON response "
                f"(content-type={res.headers.get('content-type')!r}): "
                f"{res.text[:300]}"
            )
            continue

        res_json = res.json()
        if "candidates" in res_json:
            raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
        elif "choices" in res_json:
            raw_text = res_json["choices"][0]["message"]["content"]
        else:
            last_error = f"Unexpected response shape: {str(res_json)[:300]}"
            continue

        # Models often wrap JSON in a markdown fence even when asked not to.
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        if not cleaned:
            last_error = "Provider returned an empty completion"
            continue
        return cleaned

    raise RuntimeError(
        f"LLM call failed after {max_attempts} attempt(s). Last error: {last_error}")


# -----------------------------------------------------------------------------
# Fail-Safe Template Generation Engine
# -----------------------------------------------------------------------------
def build_document_skeleton(doc: Document) -> dict:
    """Describe the document to the classifier.

    Table cell text is included via the tables' own header/preview fields so the
    model sees the whole document, not just loose body paragraphs (D9).
    """
    paragraphs_meta = []
    for idx, p in enumerate(doc.paragraphs):
        full_text = "".join(r.text for r in p.runs).strip()
        if not full_text:
            continue
        paragraphs_meta.append({
            "id": f"p_{idx}",
            "text": full_text,
            "style": p.style.name if p.style else "Normal",
        })

    tables_meta = []
    for t_idx, table in enumerate(doc.tables):
        rows_sample = []
        for row in table.rows[:4]:
            rows_sample.append([cell.text.strip() for cell in row.cells])

        tables_meta.append({
            "id": f"t_{t_idx}",
            "cols_count": len(table.columns),
            "total_rows": len(table.rows),
            "headers": rows_sample[0] if rows_sample else [],
            "preview_rows": rows_sample[1:],
        })

    return {"paragraphs": paragraphs_meta, "tables": tables_meta}


# -----------------------------------------------------------------------------
# Deterministic table assembler
# -----------------------------------------------------------------------------
# Header keyword -> logical field. Order matters: first match wins, so more
# specific patterns must come first. The special field "@index" becomes the
# row number (loop.index); any other value becomes "<loopvar>.<field>".
_COLUMN_KEYWORDS = [
    # row number
    (("sr", "s.#", "s/n", "sno", "no.", "number", "serial", "#"), "@index"),
    # attendee columns
    (("designation", "role", "title", "position", "desig", "rank"), "designation"),
    (("name", "participant", "attendee", "person", "member"), "name"),
    # action-item columns
    # NOTE: in this document's minutes "AP" is the ASSIGNED PERSON column, NOT
    # "Action Point". Confirmed against the sample document, where every AP value
    # is a person ("Person A, Person B", "Person C", "All staff").
    # The work itself lives in the "Agenda Items" column.
    (("owner", "responsible", "resp", "assigned", "assignee", "who", "ap"), "owner"),
    # the work being tracked: "Agenda Items", "Discussion", "Topic" -> item.task
    (("agenda item", "agenda", "discussion", "topic", "subject", "point"), "task"),
    # fallback aliases for the task column
    (("task", "action", "activity", "description", "work"), "task"),
    (("department", "dept", "division", "section", "unit"), "department"),
    (("deadline", "dead line", "due", "date", "timeline", "target"), "deadline"),
    (("remark", "comment", "note", "status", "priority"), "remarks"),
]

# Keys that must match the header EXACTLY (never as a prefix/substring).
# Without this, the key "ap" would also capture headers like "Approved".
_EXACT_ONLY = {"#", "ap"}

# Which fields are meaningful for each table type. Anything else is not mapped.
_TABLE_FIELDS = {
    "ATTENDEES_TABLE": {"@index", "name", "designation"},
    "ACTION_ITEMS_TABLE": {"@index", "task", "owner", "department",
                           "deadline", "remarks"},
}


def _norm_header(text: str) -> str:
    return re.sub(r"[^a-z0-9.#/ ]+", "", (text or "").lower()).strip()


def map_header_to_variable(header: str, table_type: str) -> str | None:
    """Map a column header to a logical field by KEYWORD, not position (D4).

    Returns None when the column has no known semantic for this table type, so the
    column is left as a literal rather than being silently filled with wrong data.

    Matching is exact-first, then longest-key-first substring. Longest-first matters
    so that "agenda item" wins over the shorter "agenda" on the same header.
    """
    h = _norm_header(header)
    if not h:
        return None
    allowed = _TABLE_FIELDS.get(table_type)
    if allowed is None:
        return None

    fields = [(keys, field) for keys, field in _COLUMN_KEYWORDS if field in allowed]
    # 1) exact match wins. For _EXACT_ONLY keys we allow only trailing punctuation
    #    ("AP." / "AP:"), never a longer word ("Approved"), so the key stays safe.
    h_stripped = h.rstrip(".:#/- ")
    for keys, field in fields:
        for k in keys:
            if h == k or (k in _EXACT_ONLY and h_stripped == k):
                return field
    # 2) otherwise longest key first, so "agenda item" beats "agenda"
    candidates = sorted(
        ((k, field) for keys, field in fields for k in keys if k not in _EXACT_ONLY),
        key=lambda kf: len(kf[0]),
        reverse=True,
    )
    for k, field in candidates:
        if h.startswith(k) or k in h:
            return field
    return None


def _clear_cell_keep_format(cell):
    """Empty a cell but keep its first run (and therefore its run properties)."""
    p = cell.paragraphs[0]
    runs = list(p.runs)
    if runs:
        runs[0].text = ""
        for r in runs[1:]:
            r._element.getparent().remove(r._element)
    for extra in cell.paragraphs[1:]:
        extra._p.getparent().remove(extra._p)


def set_cell_clean(cell, text: str):
    """Replace a cell's contents with `text`, preserving the first run's formatting.

    Keeps run count at 1 so docxtpl never sees a Jinja token split across runs (D1).
    """
    p = cell.paragraphs[0]
    runs = list(p.runs)
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._element.getparent().remove(r._element)
    else:
        p.add_run(text)
    for extra in cell.paragraphs[1:]:
        extra._p.getparent().remove(extra._p)


def _prototype_data_row(table, header_rows_count: int):
    """Deepcopy a sample data row so generated rows inherit the client's styling (D6)."""
    if len(table.rows) > header_rows_count:
        return copy.deepcopy(table.rows[-1]._tr)
    return copy.deepcopy(table.rows[-1]._tr)


def build_table_row_loop(table, header_rows_count: int, var_name: str,
                         table_type: str, collection: str = None) -> list[str]:
    """Convert a sample table into a docxtpl row loop.

    Layout produced:  header... | for-row | template-row | endfor-row

    docxtpl requires `{%tr for %}` and `{%tr endfor %}` to each occupy their OWN
    row: patch_xml collapses an entire <w:tr> into a single Jinja line, so putting
    both tags in the same row swallows the intervening cells and yields a bare,
    invalid `endfor` (D1).

    Column mapping is by header keyword (D4). Columns with no known semantic are
    left empty rather than being filled with the wrong data.

    `collection` is the context key iterated over (e.g. "action_items"); it may
    differ from `var_name` (e.g. "item"), so it must be passed explicitly.

    Returns the list of variables actually placed (used for validation/logging).
    """
    collection = collection or (var_name + "s")
    if header_rows_count < 1:
        header_rows_count = 1
    if header_rows_count >= len(table.rows):
        raise ValueError(
            f"table has {len(table.rows)} rows but header_rows_count="
            f"{header_rows_count}; nothing left to convert"
        )

    proto_tr = _prototype_data_row(table, header_rows_count)

    # 1) keep only the header rows
    while len(table.rows) > header_rows_count:
        tr = table.rows[-1]._tr
        tr.getparent().remove(tr)

    # 2) append three cloned rows (for / template / endfor)
    anchor = table.rows[-1]._tr
    for _ in range(3):
        nt = copy.deepcopy(proto_tr)
        anchor.addnext(nt)
        anchor = nt

    rows = list(table.rows)
    for_row, tpl_row, end_row = rows[-3], rows[-2], rows[-1]
    header_cells = rows[0].cells

    # 3) map each column by header keyword, de-duplicating
    mapping = {}
    for ci, hc in enumerate(header_cells):
        field = map_header_to_variable(hc.text, table_type)
        if field and field not in mapping.values():
            mapping[ci] = field

    if not mapping:
        raise ValueError(
            "no column could be mapped to a template variable; headers="
            + repr([c.text for c in header_cells])
        )

    # 4) for-row: opening tag alone
    for c in for_row.cells:
        _clear_cell_keep_format(c)
    set_cell_clean(for_row.cells[0], f"{{%tr for {var_name} in {collection} %}}")

    # 5) template row
    for c in tpl_row.cells:
        _clear_cell_keep_format(c)
    placed = []
    for ci, field in mapping.items():
        if ci >= len(tpl_row.cells):
            continue
        expr = "loop.index" if field == "@index" else "%s.%s" % (var_name, field)
        set_cell_clean(tpl_row.cells[ci], "{{ %s }}" % expr)
        placed.append(expr)

    # 6) endfor-row: closing tag alone
    for c in end_row.cells:
        _clear_cell_keep_format(c)
    set_cell_clean(end_row.cells[0], "{%tr endfor %}")

    return placed


def clear_sample_rows(table, header_rows_count: int):
    """Remove leftover sample data rows from a STATIC table so client dummy data
    does not ship into every generated document (D5)."""
    if header_rows_count < 1:
        header_rows_count = 1
    while len(table.rows) > header_rows_count:
        tr = table.rows[-1]._tr
        tr.getparent().remove(tr)


def purge_rogue_jinja_tags(doc: Document):
    """Strip stray block loop tags that break docxtpl.

    Scans BODY PARAGRAPHS AND TABLE CELLS - the previous version only walked
    doc.paragraphs, so rogue tags inside table cells survived into the template (D10).
    """
    def clean_paragraph(p):
        full = "".join(r.text for r in p.runs)
        if not full:
            return
        if "{%" in full and ("for" in full or "endfor" in full):
            cleaned = re.sub(
                r"\{%(?:tr|tc|p|r)?\s*(?:for\b.*?|endfor)\s*%\}", "", full
            ).strip()
            if cleaned == full:
                return
            runs = list(p.runs)
            if runs:
                runs[0].text = cleaned
                for r in runs[1:]:
                    r._element.getparent().remove(r._element)

    for p in doc.paragraphs:
        clean_paragraph(p)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    clean_paragraph(p)


def _para_full_text(p) -> str:
    """Text of a paragraph including any rendered soft line breaks.

    `p.text` drops <w:br/> (it only walks <w:t>), so text that wraps with
    Shift+Enter looks truncated to anything comparing against the skeleton.
    """
    return "".join(node.text or "" for node in p._p.iter(qn("w:t"))) or p.text


# A paragraph that occupies a whole section but describes no content — it is where
# the client intends the generated list to be poured in.
_PLACEHOLDER_RE = re.compile(
    r"("
    r"[<\[{]{1,2}\s*(?:insert|add|put|write)?[^>\]}]{0,60}goes?\s+here[^>\]}]{0,20}[>\]}]{1,2}"
    r"|goes?\s+here"
    r"|placeholder"
    r"|lorem\s+ipsum"
    r"|^t\.?b\.?d\.?$"
    r"|^t\.?o\.?d\.?o\.?$"
    r"|\binsert\b[^.]{0,40}\bhere\b"
    r")",
    re.IGNORECASE,
)

# Last-resort mapping from a section HEADING to the list that belongs under it, so
# a heading plus a placeholder resolves even when the heading wording is unusual.
_HEADING_COLLECTION_HINTS = (
    ("action item", "action_items"),
    ("action point", "action_items"),
    ("transcript", "transcript"),
    ("diariz", "transcript"),
    ("attendee", "attendees"),
    ("participant", "attendees"),
    ("agenda", "agenda_and_decisions"),
    ("speaker", "detected_speakers"),
)


def _norm_heading(h: str) -> str:
    """Normalise a heading for matching: strip trailing punctuation, collapse
    whitespace, drop a leading numbering like '3.' or '4)'."""
    h = re.sub(r"\s+", " ", (h or "").strip())
    h = re.sub(r"^[\d]+[.)]\s*", "", h)
    return h.strip(" :.-\u2014\t").lower()


def _looks_like_placeholder(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 200:
        return False
    return bool(_PLACEHOLDER_RE.search(t))


def _collection_for_heading(heading: str) -> str | None:
    h = _norm_heading(heading)
    for key, collection in _HEADING_COLLECTION_HINTS:
        if key in h:
            return collection
    return None


def _placeholder_collections(doc) -> dict:
    """Map every placeholder paragraph's ID -> the collection its heading implies.

    Each placeholder is paired with the last heading-ish line above it, so a
    document with several placeholder sections resolves each one independently.
    Returns {p_id: collection} for placeholders whose heading maps to a known list.
    """
    out: dict = {}
    last_heading: str | None = None
    p_index = 0
    for child in doc.element.body.iterchildren():
        if child.tag != qn("w:p"):
            continue
        p = Paragraph(child, doc)
        pid = f"p_{p_index}"
        p_index += 1
        txt = p.text.strip()
        if not txt:
            continue
        if _looks_like_placeholder(txt):
            collection = (
                _collection_for_heading(last_heading or "")
                or _collection_for_heading(txt)
            )
            if collection:
                out[pid] = collection
            continue
        # Treat a short, colon-suffixed or ALL-CAPS line as a heading.
        if len(txt) <= 80 and (
            txt.endswith(":") or txt.isupper() or len(txt.split()) <= 6
        ):
            last_heading = txt
    return out


def _default_body_for(collection: str) -> str:
    """A sensible one-item line per collection, used when the model gave a
    placeholder paragraph no usable text of its own."""
    return {
        "action_items": (
            "{{ loop.index }}. {{ item.task }} — {{ item.owner }} "
            "({{ item.department }}, due {{ item.deadline }}){% if item.remarks %} "
            "{{ item.remarks }}{% endif %}"
        ),
        "transcript": (
            "[{{ entry.timestamp }}] {{ entry.speaker }}: {{ entry.original_text }} "
            "({{ entry.translated_text }})"
        ),
        "attendees": "{{ attendee.name }} — {{ attendee.designation }}",
        "agenda_and_decisions": (
            "{{ agenda.topic }}: {{ agenda.discussion_summary }}"
        ),
        "detected_speakers": "{{ speaker.speaker_id }}: {{ speaker.inferred_name }}",
    }.get(collection, "{{ %s }}" % _DOC_COLLECTIONS.get(collection, "item"))


def _clear_runs_keep_format(p):
    """Drop a paragraph's runs, keeping the paragraph itself and its pPr."""
    for r in list(p.runs):
        r._element.getparent().remove(r._element)


def expand_paragraph_collection_loop(paragraph, collection: str, body_text: str,
                                     doc) -> tuple[str, str]:
    """Turn ONE placeholder paragraph into a docxtpl `{%p for %}` loop.

    docxtpl expands `{%p ... %}` tags that sit ALONE in a paragraph into whole
    paragraph loops. The opening tag, the body and the closing tag must therefore
    live in THREE SEPARATE paragraphs: putting them in one paragraph (or putting
    `for` and `endfor` together) raises
    `TemplateSyntaxError: Encountered unknown tag 'endfor'` — verified empirically.

    We clone the placeholder paragraph twice so the loop body inherits the
    client's own formatting, then insert the clone and the closing-tag paragraph
    directly after the original. Inserts happen after the loop that walks
    doc.paragraphs, so this does not disturb its indices.

    Returns (loop_variable, body_text).
    """
    var = _DOC_COLLECTIONS[collection]

    # Match the variable the body actually uses, so the tag and the body agree.
    # A mismatch is invisible: docxtpl renders `{{ item.task }}` as '' when the
    # loop variable is named something else.
    # `loop` is Jinja's own special variable and must never be used as the target.
    for cand in re.findall(r"\{\{\s*(\w+)\.", body_text):
        if cand != "loop":
            var = cand
            break

    # Empty the placeholder first, keep its pPr, then use THAT as the mould for the
    # body and closing-tag paragraphs. If the mould were taken before clearing, the
    # clones would each still carry the original placeholder text and the emitted
    # paragraphs would read e.g.
    #   'The complete transcript goes here{%p endfor %}'
    # (still compiles, so only an exact-equality assertion catches it).
    _clear_runs_keep_format(paragraph)
    base_p = copy.deepcopy(paragraph._p)

    # 1) opening tag, alone in the original paragraph
    paragraph.add_run(f"{{%p for {var} in {collection} %}}")

    # 2) body paragraph = an empty clone, carrying only the item text
    body_p = copy.deepcopy(base_p)
    body_para = Paragraph(body_p, paragraph._parent)
    body_para.add_run(body_text)

    # 3) closing tag, alone in a second empty clone
    end_p = copy.deepcopy(base_p)
    end_para = Paragraph(end_p, paragraph._parent)
    end_para.add_run("{%p endfor %}")

    paragraph._p.addnext(end_p)
    paragraph._p.addnext(body_p)
    return var, body_text


def generate_template_from_sample_ai(
    sample_bytes: bytes,
    base_url: str,
    api_key: str,
    model_name: str,
    status_container=None,
) -> io.BytesIO:
    doc = Document(io.BytesIO(sample_bytes))

    if status_container:
        status_container.info("Step 1/3: Parsing document skeleton...")
    skeleton = build_document_skeleton(doc)

    prompt = f"""
You are an expert Word document template engineer.
Analyze this document skeleton and generate a clean plan to replace sample meeting details with Jinja variables.

DOCUMENT SKELETON:
{json.dumps(skeleton, indent=2)}

AVAILABLE VARIABLES:
- title: str
- date: str
- meeting_time: str
- minute_taker: str
- executive_summary: str
- agenda_and_decisions: list[{{topic: str, discussion_summary: str, decisions_made: list[str]}}]
- attendees: list[{{name: str, designation: str}}]
- action_items: list[{{task: str, owner: str, department: str, deadline: str, remarks: str}}]
- detected_speakers: list[{{speaker_id: str, inferred_name: str}}]
- transcript: list[{{speaker: str, timestamp: str, original_text: str, translated_text: str}}]
- next_meeting_date: str
- next_meeting_time: str
- next_meeting_agenda_focus: str
- closing_remarks: str

STRICT RULES:
1. PARAGRAPHS:
   - If a paragraph has static headers (e.g. 'ATTENDEES', 'Agenda Points:', '3. NEXT MEETING', '4. CLOSING'): action = 'KEEP_STATIC'.
   - If it has mixed labels and sample values (e.g. 'Meeting Title: Internal meeting... Date: September 11...'):
     action = 'REPLACE_TEMPLATE'
     cleaned_template_text = 'Meeting Title: {{{{ title }}}} Date: {{{{ date }}}} Time: {{{{ meeting_time }}}} Minute Taker: {{{{ minute_taker }}}}'
   - For '3. NEXT MEETING': replace values with 'Date: {{{{ next_meeting_date }}}} Time: {{{{ next_meeting_time }}}} Agenda Focus: {{{{ next_meeting_agenda_focus }}}}'.
   - For '4. CLOSING': replace with '4. CLOSING {{{{ closing_remarks }}}}'.
   - Dummy paragraphs that are purely old sample discussions: action = 'PURGE'.

   - REPEATING SECTIONS (IMPORTANT): a placeholder paragraph that stands in for a
     LIST of items must NOT be purged. Look for a heading followed by a
     placeholder such as '<Action Items Table goes here>', 'The complete
     transcript ... goes here', '<<insert ...>>', 'TODO', or a single stub line
     in a section whose content is obviously a list. For these:
       action = 'REPLACE_TEMPLATE'
       collection = the matching list
       cleaned_template_text = ONE line showing how ONE item should be written
     Allowed collections and the loop variable to use in the text:
       * 'action_items'          -> item    fields: item.task, item.owner,
                                              item.department, item.deadline,
                                              item.remarks
       * 'transcript'            -> entry   fields: entry.speaker, entry.timestamp,
                                              entry.original_text,
                                              entry.translated_text
       * 'attendees'             -> attendee fields: attendee.name,
                                              attendee.designation
       * 'agenda_and_decisions'  -> agenda  fields: agenda.topic,
                                              agenda.discussion_summary,
                                              agenda.decisions_made
       * 'detected_speakers'     -> speaker fields: speaker.speaker_id,
                                              speaker.inferred_name
     Example, for '<Action Items Table goes here>' under an ACTION ITEMS heading:
       action = 'REPLACE_TEMPLATE', collection = 'action_items',
       cleaned_template_text = '{{{{ loop.index }}}}. {{{{ item.task }}}} — {{{{ item.owner }}}} ({{{{ item.department }}}}, due {{{{ item.deadline }}}})'
     Leave 'collection' null for every ordinary single-value paragraph.
     The section HEADING itself (e.g. 'ACTION ITEMS') stays 'KEEP_STATIC'.
   - NEVER write '{{% for' or '{{% endfor' inside paragraphs; the assembler adds
     the loop tags itself. Only the single-item text is yours to write.

2. TABLES:
   - Identify table_type:
     * 'ATTENDEES_TABLE' if it lists participants (Name, Designation).
     * 'ACTION_ITEMS_TABLE' if it lists agenda points/tasks (Agenda Items, AP, Dead line, Remarks).
     * 'STATIC_TABLE' otherwise.
   - header_rows_count: usually 1.
   - Column semantics are resolved by HEADER TEXT at conversion time, so do NOT
     guess column positions. Note that in these minutes the 'AP' column holds the
     ASSIGNED PERSON (owner), and 'Agenda Items' holds the work itself: put the
     person from the 'AP' column in `owner` and the work from 'Agenda Items' in
     `task`.

Return pure valid JSON conforming strictly to the DocumentAnalysisPlan schema:
{json.dumps(DocumentAnalysisPlan.model_json_schema())}
"""

    if status_container:
        status_container.info("Step 2/3: Semantic classification via AI...")

    raw_plan_json = call_llm_json(base_url, api_key, model_name, prompt)
    plan_dict = json.loads(raw_plan_json)

    for wrapper in ["plan", "document_analysis_plan", "data", "result"]:
        if wrapper in plan_dict and isinstance(plan_dict[wrapper], dict):
            plan_dict = plan_dict[wrapper]
            break

    plan = DocumentAnalysisPlan(**plan_dict)

    if status_container:
        status_container.info("Step 3/3: Deterministically assembling template rows...")

    p_map = {p.p_id: p for p in plan.paragraphs}
    t_map = {t.t_id: t for t in plan.tables}

    # 1. Mutate Paragraphs
    paragraphs_to_remove = []
    collection_lists = []
    conversion_notes = []
    placeholder_hints = _placeholder_collections(doc)
    for idx, p in enumerate(doc.paragraphs):
        pid = f"p_{idx}"
        if pid not in p_map:
            continue
        rule = p_map[pid]

        # --- Deterministic override, applied BEFORE the model's action ---
        # The model is NOT reliable here: across identical runs it classified the
        # client's "<Action Items Table goes here>" as KEEP_STATIC, PURGE and
        # REPLACE_TEMPLATE. PURGE and KEEP_STATIC both lose the section for good
        # (deleted, or the literal placeholder text ships into every generated
        # document unfilled), and that IS the reported bug. So when a paragraph is a
        # section placeholder that resolves to a list we hold, we build the loop
        # regardless of what the model said. This is the safety net that makes the
        # feature deterministic.
        hint = placeholder_hints.get(pid)
        if hint and rule.action != "REPLACE_TEMPLATE":
            collection_lists.append(
                (p, pid, hint, _default_body_for(hint)))
            conversion_notes.append(
                f"{pid}: model said {rule.action} but text is a section "
                f"placeholder; recovered as a '{hint}' loop")
            continue
        if hint is None and _looks_like_placeholder(_para_full_text(p)) \
                and not rule.collection:
            # Unresolvable placeholder: never print it into client output.
            paragraphs_to_remove.append(p)
            conversion_notes.append(f"{pid}: removed unresolvable placeholder")
            continue

        if rule.action == "PURGE":
            paragraphs_to_remove.append(p)
        elif rule.action == "REPLACE_TEMPLATE" and rule.cleaned_template_text:
            if rule.collection:
                if rule.collection not in _DOC_COLLECTIONS:
                    raise ValueError(
                        f"Paragraph {pid} was assigned unknown collection "
                        f"{rule.collection!r}; allowed: "
                        f"{', '.join(sorted(_DOC_COLLECTIONS))}"
                    )
                # Placeholder for a repeating section -> paragraph loop.
                collection_lists.append(
                    (p, pid, rule.collection, rule.cleaned_template_text))
            else:
                # Clear runs and insert clean template text
                if p.runs:
                    p.runs[0].text = rule.cleaned_template_text
                    for r in p.runs[1:]:
                        r.text = ""
                else:
                    p.add_run(rule.cleaned_template_text)

    for p in paragraphs_to_remove:
        p_elem = p._p
        if p_elem.getparent() is not None:
            p_elem.getparent().remove(p_elem)

    # 2. Purge stray jinja tags left in the SAMPLE (body + cells).
    #    This MUST run before the row loops are generated below, otherwise it would
    #    strip the `{%tr for %}` / `{%tr endfor %}` tags we are about to create.
    purge_rogue_jinja_tags(doc)

    # 2b. Expand the repeating-section placeholders into `{%p for %}` loops.
    #     The opening tag, body and closing tag go in three SEPARATE paragraphs —
    #     docxtpl requires this, exactly as for the table row loops. Expanding here
    #     (after the Purge above, like the table loops) also means the tags we
    #     create cannot be stripped by it. Each expansion appends TWO paragraphs,
    #     so we expand in reverse so an earlier insert cannot shift a later target
    #     out of position.
    for p, pid, collection, body_text in reversed(collection_lists):
        var, _ = expand_paragraph_collection_loop(p, collection, body_text, doc)
        conversion_notes.append(
            f"{pid}: {{%p for {var} in {collection} %}} -> {collection}")

    # 3. Mutate Tables (deterministic row-loop generation)
    loop_specs = {
        "ATTENDEES_TABLE": ("attendee", "attendees"),
        "ACTION_ITEMS_TABLE": ("item", "action_items"),
    }

    for t_idx, table in enumerate(doc.tables):
        tid = f"t_{t_idx}"
        rule = t_map.get(tid)
        if not rule:
            continue

        ttype = rule.table_type

        # Tolerate alternate spellings the model may emit.
        if ttype in ("ACTION_TABLE", "ACTION_ITEMS", "ACTIONITEMS_TABLE"):
            ttype = "ACTION_ITEMS_TABLE"
        if ttype in ("ATTENDEES", "ATTENDEE_TABLE", "PARTICIPANTS_TABLE"):
            ttype = "ATTENDEES_TABLE"

        if ttype in ("ATTENDEES_TABLE", "ACTION_ITEMS_TABLE"):
            var_name, collection = loop_specs[ttype]
            try:
                placed = build_table_row_loop(
                    table, rule.header_rows_count, var_name, ttype, collection
                )
                conversion_notes.append(
                    f"{tid} ({ttype}): mapped {len(placed)} columns -> {', '.join(placed)}"
                )
            except ValueError as tbl_err:
                # Loud failure beats a silently blank table (D3).
                raise ValueError(f"Table {tid} ({ttype}) could not be converted: {tbl_err}")

        elif ttype == "STATIC_TABLE":
            # Keep the layout, drop the client's sample data rows (D5).
            clear_sample_rows(table, rule.header_rows_count)

        elif ttype == "PURGE":
            tbl = table._tbl
            if tbl.getparent() is not None:
                tbl.getparent().remove(tbl)

    out_stream = io.BytesIO()
    doc.save(out_stream)
    template_bytes = out_stream.getvalue()

    # Self-Testing Compilation: verifies template before handing to user
    try:
        test_tpl = DocxTemplate(io.BytesIO(template_bytes))
        test_ctx = _build_render_context(
            {
                "title": "Test",
                "date": "2026-09-12",
                "meeting_time": "10:00",
                "minute_taker": "Test Taker",
                "executive_summary": "Test summary",
                "agenda_and_decisions": [
                    {"topic": "Test Topic", "discussion_summary": "Test discussion",
                     "decisions_made": ["Decision 1"]},
                ],
                "attendees": [{"name": "Test User", "designation": "CEO"}],
                "detected_speakers": [{"speaker_id": "Speaker 1", "inferred_name": "Test User"}],
                "action_items": [
                    {"task": "Task", "owner": "Owner", "department": "Admin",
                     "deadline": "TBD", "remarks": ""},
                ],
                "transcript": [
                    {"speaker": "Test User", "timestamp": "00:01",
                     "original_text": "Hello", "translated_text": "Hello"},
                ],
                "next_meeting_date": "TBD",
                "next_meeting_time": "TBD",
                "next_meeting_agenda_focus": "TBD",
                "closing_remarks": "Test closing",
            }
        )
        # autoescape=True must match the production render, or the validator
        # cannot catch the special-character corruption (D11/D12).
        test_tpl.render(test_ctx, autoescape=True)
        if status_container:
            status_container.success("Template verified and compiled successfully!")
    except Exception as validation_err:
        if status_container:
            status_container.error(f"Template compilation failed during self-test: {validation_err}")
        raise

    return io.BytesIO(template_bytes)


class _AttendeeView(dict):
    """Allows dictionary to be rendered as both an object (a.name) and string ({{ a }})."""
    def __str__(self):
        name = self.get("name", "")
        desig = self.get("designation", "")
        return f"{name} ({desig})" if desig else name


def _build_render_context(context: dict) -> dict:
    """Normalize a render context for docxtpl. Shared by production and self-test so
    the validator exercises the same shapes real data takes (D12)."""
    # Attendees Normalization
    normalized_attendees = []
    for a in context.get("attendees", []) or []:
        if isinstance(a, dict):
            normalized_attendees.append(_AttendeeView(a))
        else:
            normalized_attendees.append(_AttendeeView({"name": str(a), "designation": ""}))
    context["attendees"] = normalized_attendees

    # Action Items Normalization (legacy 'priority' -> remarks)
    for item in context.get("action_items", []) or []:
        if not item.get("remarks"):
            item["remarks"] = item.get("priority", "") or ""

    # Transcript Aliasing
    for item in context.get("transcript", []) or []:
        item["text"] = item.get("translated_text") or item.get("original_text", "")

    return context


def render_template_docx(template_bytes: bytes, data: MeetingMinutesReport) -> io.BytesIO:
    doc = DocxTemplate(io.BytesIO(template_bytes))
    context = _build_render_context(data.model_dump())

    # autoescape=True is REQUIRED: with docxtpl's default (False), user data
    # containing '&', '<' or '>' is silently mangled or dropped, producing invalid
    # or lossy XML. E.g. 'Org & Partner Ltd' -> 'Org  Partner Ltd' (D11).
    doc.render(context, autoescape=True)
    out_stream = io.BytesIO()
    doc.save(out_stream)
    out_stream.seek(0)
    return out_stream


def build_default_docx(data: MeetingMinutesReport) -> io.BytesIO:
    doc = Document()
    doc.add_heading(data.title, level=0)
    doc.add_paragraph(f"Date: {data.date}")
    if data.meeting_time:
        doc.add_paragraph(f"Time: {data.meeting_time}")
    if data.minute_taker:
        doc.add_paragraph(f"Minute Taker: {data.minute_taker}")

    doc.add_heading("1. Attendees", level=1)
    if data.attendees:
        tbl = doc.add_table(rows=1, cols=3)
        tbl.style = "Table Grid"
        h = tbl.rows[0].cells
        h[0].text = "Sr.#"
        h[1].text = "Name"
        h[2].text = "Designation"
        for i, a in enumerate(data.attendees, 1):
            r = tbl.add_row().cells
            r[0].text = str(i)
            r[1].text = a.name
            r[2].text = a.designation
    else:
        doc.add_paragraph("No attendees specified.")

    doc.add_heading("2. Executive Summary", level=1)
    doc.add_paragraph(data.executive_summary)

    doc.add_heading("3. Agenda Items & Decisions", level=1)
    for idx, item in enumerate(data.agenda_and_decisions, 1):
        doc.add_heading(f"3.{idx} {item.topic}", level=2)
        doc.add_paragraph(f"Summary: {item.discussion_summary}")
        if item.decisions_made:
            doc.add_paragraph("Decisions Reached:", style="List Bullet")
            for dec in item.decisions_made:
                doc.add_paragraph(dec, style="List Bullet 2")

    doc.add_heading("4. Action Items", level=1)
    if data.action_items:
        table = doc.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "Task"
        hdr[1].text = "Owner"
        hdr[2].text = "Department"
        hdr[3].text = "Deadline"
        hdr[4].text = "Remarks / Priority"

        for ai in data.action_items:
            row_cells = table.add_row().cells
            row_cells[0].text = ai.task
            row_cells[1].text = ai.owner
            row_cells[2].text = ai.department
            row_cells[3].text = ai.deadline
            row_cells[4].text = ai.remarks

    doc.add_heading("5. Speaker-Diarized Transcript (English)", level=1)
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
        "1. Identify attendees with their designations/roles if mentioned in 'attendees'.\n"
        "2. Produce a full diarized transcript identifying distinct speakers.\n"
        "3. Transcribe speech verbatim in 'original_text' (preserving native language/words), "
        "and provide an accurate English translation in 'translated_text'.\n"
        "4. Listen for verbal introductions, greetings, or names addressed in conversation to infer the real name of each speaker in 'detected_speakers'.\n"
        "5. Generate a comprehensive English executive summary.\n"
        "6. List all topics and decisions made in English.\n"
        "7. Extract all action items with owners, departments, deadlines, and priorities/remarks in English.\n"
        "8. Extract any next meeting logistics (date, time, agenda focus) and closing remarks.\n"
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
            # Several OpenAI-compatible gateways (9router, LiteLLM, vLLM proxies)
            # stream by DEFAULT, which returns text/event-stream and breaks
            # response.json(). Ask for a single JSON body explicitly.
            "stream": False,
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

    existing_attendee_names = {a.name for a in updated.attendees}
    for old_spk, new_spk in name_map.items():
        if new_spk.strip() and new_spk.strip() not in existing_attendee_names:
            updated.attendees.append(Attendee(name=new_spk.strip(), designation="Participant"))
            existing_attendee_names.add(new_spk.strip())

    return updated


# -----------------------------------------------------------------------------
# Main Application UI
# -----------------------------------------------------------------------------
def main():
    # -------------------------------------------------------------------------
    # Header / 2006-inspired control-desk chrome
    # -------------------------------------------------------------------------
    st.markdown('<div style="height:0.65rem"></div>', unsafe_allow_html=True)
    h_col1, h_col2, h_col3 = st.columns([0.61, 0.2, 0.19], vertical_alignment="center")
    with h_col1:
        logo_uri = _brand_logo_uri()
        if logo_uri:
            st.markdown(
                '<div class="aima-brand">'
                f'<img src="{logo_uri}" alt="AIMA">'
                '<span><span class="aima-brand-text">AI Meeting Assistant</span><span class="aima-brand-sub">Meeting control desk · bilingual minutes</span></span>'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div class="aima-brand"><span class="aima-brand-text">🎙️ AIMA · AI Meeting Assistant</span></div>', unsafe_allow_html=True)
    with h_col2:
        if st.button("↻ Start Over", use_container_width=True, help="Clear the current meeting and reset the workspace", key="header_start_over"):
            for key in [
                "meeting_result", "usage_stats", "saved_template_bytes", "active_template_bytes",
                "converted_template_download", "logs_list", "transcript_view_mode",
            ]:
                st.session_state.pop(key, None)
            st.session_state["logs_list"] = ['<span class="log-debug">[System] Session reset. Ready.</span>']
            st.rerun()
    with h_col3:
        with st.popover("⚙ Settings", use_container_width=True):
            st.markdown("**Provider & Model Settings**")
            has_key = bool(st.session_state.get("api_key"))
            st.caption(f"Status: {'🟢 Key is Set' if has_key else '🔴 No Key Set'}")
            new_key = st.text_input(
                "API Key / Token:",
                value="",
                type="password",
                placeholder="••••••••••••••••" if has_key else "Paste key...",
                key="settings_api_key",
            )
            if new_key.strip():
                st.session_state["api_key"] = new_key.strip()
                st.rerun()

            current_base = st.session_state.get("base_url", DEFAULT_BASE_URL)
            new_base = st.text_input("Provider Endpoint URL:", value=current_base, key="settings_base_url")
            if new_base != current_base:
                st.session_state["base_url"] = new_base.strip()
                st.session_state["available_models"] = fetch_available_models(
                    st.session_state["base_url"], st.session_state.get("api_key", "")
                )
                st.rerun()

            st.markdown("---")
            models_list = st.session_state.get("available_models", [DEFAULT_MODEL])
            curr_model = st.session_state.get("selected_model", DEFAULT_MODEL)
            model_idx = models_list.index(curr_model) if curr_model in models_list else 0
            st.session_state["selected_model"] = st.selectbox(
                "Active AI Model:", options=models_list, index=model_idx, key="settings_active_model"
            )
            if st.button("↻ Refresh model list", use_container_width=True, key="settings_refresh_models"):
                st.session_state["available_models"] = fetch_available_models(
                    st.session_state["base_url"], st.session_state.get("api_key", "")
                )
                st.rerun()

    st.markdown(
        '<div class="retro-titlebar">AIMA / MEETING CONTROL DESK &nbsp;·&nbsp; CAPTURE → ORGANIZE → EXPORT</div>'
        '<div class="status-strip"><span><span class="status-dot">●</span> <strong>Workspace ready</strong> · Your meeting intelligence console</span><span>WEB + MOBILE READY</span></div>',
        unsafe_allow_html=True,
    )

    left_col, right_col = st.columns([0.38, 0.62], gap="large")

    # -------------------------------------------------------------------------
    # Left rail: the workflow
    # -------------------------------------------------------------------------
    with left_col:
        with st.container(border=True):
            st.markdown('<div class="section-label">01 / Import meeting audio</div><p class="section-help">Bring in the recording. AIMA separates speakers, translates when needed, and builds the brief.</p>', unsafe_allow_html=True)
            audio_file = st.file_uploader(
                "Select meeting recording",
                type=["mp3", "wav", "m4a", "ogg", "aac", "mp4"],
                help="Supports MP3, WAV, M4A, OGG, AAC, MP4.",
                label_visibility="collapsed",
                key="meeting_audio_upload",
            )
            if audio_file:
                st.audio(audio_file)
                st.markdown(f'<p class="field-caption">Loaded: <strong>{escape(str(audio_file.name))}</strong></p>', unsafe_allow_html=True)
            else:
                st.markdown('<p class="field-caption">Accepted formats: MP3 · WAV · M4A · OGG · AAC · MP4</p>', unsafe_allow_html=True)

            st.markdown('<div class="section-label">02 / Document output</div><p class="section-help">Use the clean minutes layout or bring a Word template from your team.</p>', unsafe_allow_html=True)
            doc_choice = st.radio(
                "Template strategy:",
                ["Default Clean Format", "Upload Tagged .docx", "AI Convert Sample Finished .docx"],
                horizontal=False,
                label_visibility="collapsed",
                key="document_template_strategy",
            )

            if doc_choice == "Default Clean Format":
                st.session_state["active_template_bytes"] = None
            elif doc_choice == "Upload Tagged .docx":
                uploaded_tpl = st.file_uploader("Upload Word Template (.docx)", type=["docx"], key="tagged_docx")
                if uploaded_tpl:
                    st.session_state["active_template_bytes"] = uploaded_tpl.getvalue()
                    st.caption("✅ Custom tagged template armed")
            else:
                sample_file = st.file_uploader("Upload Finished Sample (.docx)", type=["docx"], key="sample_docx")
                if sample_file:
                    template_status = st.empty()
                    if st.button("✦ Build universal template from sample", use_container_width=True, key="build_ai_template"):
                        active_key = st.session_state.get("api_key")
                        active_base = st.session_state.get("base_url", DEFAULT_BASE_URL)
                        active_model = st.session_state.get("selected_model", DEFAULT_MODEL)
                        if not active_key:
                            st.error("API Key required for AI template generation. Configure it in ⚙ Settings.")
                        else:
                            try:
                                converted_io = generate_template_from_sample_ai(
                                    sample_bytes=sample_file.getvalue(),
                                    base_url=active_base,
                                    api_key=active_key,
                                    model_name=active_model,
                                    status_container=template_status,
                                )
                                converted_bytes = converted_io.getvalue()
                                st.session_state["active_template_bytes"] = converted_bytes
                                st.session_state["converted_template_download"] = converted_bytes
                            except Exception as err:
                                template_status.error(f"AI conversion error: {err}")
                if st.session_state.get("converted_template_download"):
                    st.download_button(
                        label="↓ Download generated template",
                        data=st.session_state["converted_template_download"],
                        file_name="AI_Generated_Meeting_Template.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key="download_generated_template",
                    )

            st.markdown('<div class="section-label">03 / Generate minutes</div>', unsafe_allow_html=True)
            run_clicked = st.button("▶ Process meeting audio", type="primary", use_container_width=True, key="process_meeting_audio")

        st.markdown('<div class="retro-titlebar">LIVE PIPELINE CONSOLE</div>', unsafe_allow_html=True)
        with st.container(border=True):
            status_text = st.empty()
            progress_bar = st.progress(0)
            log_container = st.empty()
            reversed_initial = "<br>".join(reversed(st.session_state["logs_list"]))
            log_container.markdown(f'<div id="aima-terminal-box" class="terminal-container">{reversed_initial}</div>', unsafe_allow_html=True)

            stats = st.session_state.get("usage_stats")
            if stats:
                st.markdown('<div class="section-label" style="margin-top:0.8rem">Latest run telemetry</div>', unsafe_allow_html=True)
                has_thoughts = stats.get("thoughts_tokens", 0) > 0
                metric_data = [("Prompt", f"{stats['prompt_tokens']:,}"), ("Output", f"{stats['completion_tokens']:,}")]
                if has_thoughts:
                    metric_data.append(("Thinking", f"{stats['thoughts_tokens']:,}"))
                metric_data.append(("Total", f"{stats['total_tokens']:,}"))
                metric_cols = st.columns(len(metric_data))
                for col, (label, value) in zip(metric_cols, metric_data):
                    with col:
                        st.metric(label, value)
                detail_cols = st.columns(2)
                with detail_cols[0]:
                    st.metric("Latency", f"{stats['latency']:.2f}s")
                with detail_cols[1]:
                    speed = f"{stats['speed']:.1f} tok/s" if stats['speed'] > 0 else "N/A"
                    st.metric("Speed", speed)

        if run_clicked:
            active_key = st.session_state.get("api_key")
            active_base = st.session_state.get("base_url", DEFAULT_BASE_URL)
            active_model = st.session_state.get("selected_model", DEFAULT_MODEL)
            if not active_key:
                st.error("Missing API Key. Open ⚙ Settings in the header.")
                return
            if not audio_file:
                st.error("Please select a meeting recording in Step 1.")
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
                    model_name=active_model,
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
            except Exception as err:
                progress_bar.empty()
                status_text.empty()
                log_event(log_container, st.session_state["logs_list"], f"Execution failed: {str(err)}", "ERROR")
                st.error(f"Error: {err}")
                return

    # -------------------------------------------------------------------------
    # Right workspace: blank desk or meeting brief
    # -------------------------------------------------------------------------
    with right_col:
        if "meeting_result" not in st.session_state:
            with st.container(border=True):
                st.markdown(
                    '<div class="empty-desk"><div class="empty-desk-icon">✦</div><div class="empty-desk-title">Your meeting brief will land here</div><p class="empty-desk-copy">Choose a recording on the left and AIMA will turn the conversation into a polished, reviewable set of minutes.</p><div class="desk-steps"><span class="desk-step">Summary</span><span class="desk-step">Decisions</span><span class="desk-step">Owners</span><span class="desk-step">Bilingual transcript</span></div></div>',
                    unsafe_allow_html=True,
                )
        else:
            result: MeetingMinutesReport = st.session_state["meeting_result"]
            active_template = st.session_state.get("saved_template_bytes")
            attendee_count = len(result.attendees)
            topic_count = len(result.agenda_and_decisions)
            action_count = len(result.action_items)
            transcript_count = len(result.transcript)

            with st.container(border=True):
                header_left, header_right = st.columns([0.72, 0.28], vertical_alignment="top")
                with header_left:
                    st.markdown(f'<div class="section-label">Meeting brief / ready for review</div><div class="result-heading">{escape(str(result.title))}</div><div class="result-meta">📅 {escape(str(result.date))} &nbsp;·&nbsp; 👥 {attendee_count} attendees &nbsp;·&nbsp; 🌐 English + original language</div>', unsafe_allow_html=True)
                with header_right:
                    st.markdown('<div class="result-ribbon">MINUTES READY</div>', unsafe_allow_html=True)
                    if active_template:
                        try:
                            doc_io = render_template_docx(active_template, result)
                            template_used = True
                        except Exception as ex:
                            st.warning(f"Template rendering issue: {ex}. Using clean layout.")
                            doc_io = build_default_docx(result)
                            template_used = False
                    else:
                        doc_io = build_default_docx(result)
                        template_used = False
                    st.download_button(
                        label="↓ Download filled template" if template_used else "↓ Download standard minutes",
                        data=doc_io,
                        file_name=f"{result.title.replace(' ', '_')}_Minutes.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        type="primary",
                        key="download_minutes_docx",
                    )

                st.markdown('<div style="height:0.8rem"></div>', unsafe_allow_html=True)
                metrics = st.columns(4)
                for col, label, value in zip(metrics, ["Attendees", "Topics", "Actions", "Transcript lines"], [attendee_count, topic_count, action_count, transcript_count]):
                    with col:
                        st.metric(label, value)

                raw_speakers = sorted({entry.speaker for entry in result.transcript})
                inferred_lookup = {ds.speaker_id: ds.inferred_name for ds in getattr(result, "detected_speakers", [])}
                with st.expander("👥 Verify speaker identities", expanded=False):
                    with st.form("speaker_mapping_form"):
                        speaker_cols = st.columns(min(len(raw_speakers), 3) if raw_speakers else 1)
                        confirmed_mapping = {}
                        for index, speaker in enumerate(raw_speakers):
                            column = speaker_cols[index % len(speaker_cols)]
                            default_guess = inferred_lookup.get(speaker, "")
                            if default_guess.lower() == "unknown":
                                default_guess = ""
                            with column:
                                confirmed_mapping[speaker] = st.text_input(
                                    f"Label: {speaker}",
                                    value=default_guess if default_guess else speaker,
                                    key=f"speaker_label_{speaker}",
                                )
                        if st.form_submit_button("Update names across brief", use_container_width=True):
                            st.session_state["meeting_result"] = apply_speaker_replacements(result, confirmed_mapping)
                            st.rerun()

                tab_overview, tab_actions, tab_attendees, tab_transcript, tab_raw = st.tabs(
                    ["Overview", "Action items", "Attendees", "Transcript", "Raw data"]
                )

                with tab_overview:
                    st.markdown('<div class="summary-label">Executive summary</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="summary-box">{escape(str(result.executive_summary))}</div>', unsafe_allow_html=True)
                    st.markdown("### Agenda & decisions")
                    if result.agenda_and_decisions:
                        for item in result.agenda_and_decisions:
                            decision_count = len(item.decisions_made)
                            with st.expander(f"{item.topic} · {decision_count} decision{'s' if decision_count != 1 else ''}", expanded=True):
                                st.write(item.discussion_summary)
                                if item.decisions_made:
                                    st.markdown("**Decisions reached**")
                                    for decision in item.decisions_made:
                                        st.markdown(f"- {decision}")
                    else:
                        st.info("No agenda topics were detected.")
                    if result.next_meeting_date != "TBD" or result.next_meeting_agenda_focus:
                        st.markdown("### Next meeting logistics")
                        st.write(f"**Date:** {result.next_meeting_date} | **Time:** {result.next_meeting_time}")
                        if result.next_meeting_agenda_focus:
                            st.write(f"**Agenda focus:** {result.next_meeting_agenda_focus}")

                with tab_actions:
                    st.markdown('<div class="summary-label">Action register</div>', unsafe_allow_html=True)
                    if result.action_items:
                        cards = []
                        for item in result.action_items:
                            remarks = str(item.remarks or "")
                            priority_match = re.search(r"\\b(high|medium|low)\\b", remarks, flags=re.IGNORECASE)
                            priority = priority_match.group(1).title() if priority_match else "Tracked"
                            priority_class = priority.lower() if priority.lower() in ["high", "medium", "low"] else "default"
                            meta = f"Owner: {item.owner}"
                            if item.department:
                                meta += f" · {item.department}"
                            meta += f" · Due: {item.deadline}"
                            if remarks and priority == "Tracked":
                                meta += f" · {remarks}"
                            cards.append(f'<div class="action-card"><div><div class="action-task">{escape(str(item.task))}</div><div class="action-meta">{escape(meta)}</div></div><span class="priority priority-{priority_class}">{escape(priority)}</span></div>')
                        st.markdown("".join(cards), unsafe_allow_html=True)
                    else:
                        st.info("No action items detected in the discussion.")

                with tab_attendees:
                    st.markdown('<div class="summary-label">Attendee roster</div>', unsafe_allow_html=True)
                    if result.attendees:
                        attendee_cards = []
                        for attendee in result.attendees:
                            role = escape(str(attendee.designation)) if attendee.designation else "Role not specified"
                            attendee_cards.append(f'<div class="attendee-card"><div class="attendee-name">{escape(str(attendee.name))}</div><div class="attendee-role">{role}</div></div>')
                        st.markdown("".join(attendee_cards), unsafe_allow_html=True)
                    else:
                        st.info("No attendees recorded.")

                with tab_transcript:
                    view_mode = st.radio(
                        "Transcript view:",
                        ["English Translation", "Original Spoken Language", "Bilingual Side-by-Side"],
                        horizontal=True,
                        key="transcript_view_mode",
                    )
                    entries = []
                    for entry in result.transcript:
                        speaker = str(entry.speaker or "Unknown")
                        initials = "".join(part[0] for part in speaker.split()[:2]).upper() or "?"
                        timestamp = f'<span class="transcript-time">{escape(str(entry.timestamp))}</span>' if entry.timestamp else ""
                        if view_mode == "English Translation":
                            text = escape(str(entry.translated_text))
                        elif view_mode == "Original Spoken Language":
                            text = escape(str(entry.original_text))
                        else:
                            text = f'<strong>Original:</strong> {escape(str(entry.original_text))}<br><strong>English:</strong> {escape(str(entry.translated_text))}'
                        entries.append(f'<div class="transcript-entry"><div class="speaker-avatar">{escape(initials)}</div><div><div class="transcript-speaker">{escape(speaker)}{timestamp}</div><div class="transcript-text">{text}</div></div></div>')
                    st.markdown("".join(entries) if entries else '<p class="field-caption">No transcript lines were returned.</p>', unsafe_allow_html=True)

                with tab_raw:
                    st.markdown('<div class="summary-label">Export metadata</div>', unsafe_allow_html=True)
                    json_bytes = json.dumps(result.model_dump(), indent=2)
                    st.download_button(
                        label="↓ Export raw JSON",
                        data=json_bytes,
                        file_name="meeting_metadata.json",
                        mime="application/json",
                        key="download_meeting_json",
                    )
                    st.json(result.model_dump())


if __name__ == "__main__":
    main()
