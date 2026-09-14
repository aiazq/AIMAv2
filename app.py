import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import datetime
import io
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
from pydantic import BaseModel, Field, field_validator, model_validator

import media_pipeline

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

    /* Brand mark. The wordmark's "IMA" is dark navy (11,20,35): 18.4:1 on white
       but 1.02:1 on a dark surface, i.e. invisible. Streamlit defaults the theme
       to "auto" (follows the OS), so the plate is the thing that keeps the
       wordmark legible rather than the page background.
       Light mode: no border, background of the app's own white page -> reads as a
       clean cut-out instead of a box.
       Dark mode: the logo's native off-white becomes a deliberate light chip, so
       it gets a hairline border to look intentional rather than pasted on.
       Height is pinned so the plate matches the header row (34px art + 5px
       padding + 1px border = 46px); otherwise it overflows and de-centres. */
    .aima-brand {
        display: inline-flex;
        align-items: center;
        background: #ffffff;
        border: 1px solid transparent;
        border-radius: 9px;
        padding: 5px 12px;
        box-shadow: none;
        line-height: 0;
        margin: 0;
    }

    @media (prefers-color-scheme: dark) {
        .aima-brand {
            background: #f6f7fb;
            border-color: rgba(255, 255, 255, 0.14);
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
        }
    }

    .aima-brand img {
        height: 34px;
        width: auto;
        display: block;
    }

    /* Tagline beside the mark. Sized/weighted to sit as a peer of the wordmark
       rather than compete with it, with a divider for separation.
       Colour is pinned to the logo's own navy (#0B1423): the plate is always
       light, so this stays legible in both themes. */
    .aima-brand-text {
        margin-left: 12px;
        padding-left: 12px;
        border-left: 1px solid rgba(11, 20, 35, 0.18);
        font-size: 1.02rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        color: #0b1423;
        white-space: nowrap;
        line-height: 1.2;
    }

    /* Streamlit wraps markdown in a container with its own bottom margin, which
       drops the plate below the row's vertical centre. Trim it on the header cell. */
    div[data-testid="stHorizontalBlock"]:has(.aima-brand) div[data-testid="stMarkdownContainer"] {
        margin: 0 !important;
    }
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

# Single source of truth for the release shown at the foot of the page. Bump this
# and the git tag together — a badge that disagrees with the tag tells the user
# they are running code they are not.
APP_VERSION = "v0.2.1"

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
def _clean_str(value, default=""):
    """Coerce anything the model might emit for a text field into a string."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values())
    return value if isinstance(value, str) else str(value)


def _normalize(data, str_fields=(), list_fields=()):
    """Normalise one item of model output before validation.

    A key present with an explicit null is REMOVED so the field's own default
    applies (rather than being flattened to ""). This matters for fields whose
    default is meaningful, e.g. date -> 'Undated'.
    """
    if not isinstance(data, dict):
        return data
    data = dict(data)
    for k in str_fields:
        if k not in data:
            continue
        if data[k] is None:
            data.pop(k)
        else:
            data[k] = _clean_str(data[k], "")
    for k in list_fields:
        if k not in data:
            continue
        v = data.pop(k) if data[k] is None else data[k]
        if v is None:
            continue
        if isinstance(v, str):
            data[k] = [v]
        elif isinstance(v, dict):
            data[k] = [v]
        elif isinstance(v, (list, tuple)):
            data[k] = list(v)
    return data


class Attendee(BaseModel):
    name: str = Field(default="", description="Full name of attendee.")
    designation: str = Field(default="", description="Role or title if mentioned, otherwise empty.")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data):
        return _normalize(data, str_fields=("name", "designation"))

    model_config = {"extra": "ignore"}


class ActionItem(BaseModel):
    task: str = Field(
        default="",
        description=("Description of the action item or task, i.e. the work being "
                     "tracked (the 'Agenda Items' column of a minutes table)."),
    )
    owner: str = Field(default="", description="Person, role, or team assigned to this task.")
    department: str = Field(default="", description="Relevant department or team if identifiable.")
    deadline: str = Field(default="", description="Due date, timeframe, or 'TBD' if unspecified.")
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

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data):
        return _normalize(
            data, str_fields=("task", "owner", "department", "deadline")
        )

    model_config = {"populate_by_name": True, "extra": "ignore"}


class TranscriptEntry(BaseModel):
    """One diarized transcript segment.

    Every field is optional on input and coerced to a string. The model is asked
    for all four, but a long transcript occasionally omits `translated_text` on a
    single segment or emits an explicit null — and a required field then rejected
    the ENTIRE report after the provider call had already succeeded, discarding
    minutes of audio work over one missing key.

    A missing translation falls back to the original text so the segment still
    renders (and is visibly untranslated), rather than silently disappearing.
    """

    speaker: str = Field(default="Unknown speaker", description="Name or label of the speaker.")
    timestamp: str = Field(default="", description="Approximate timestamp (e.g., '01:23') or empty string.")
    original_text: str = Field(default="", description="Speech in original spoken language.")
    translated_text: str = Field(default="", description="Accurate English translation (same as original if already English).")

    @model_validator(mode="before")
    @classmethod
    def _coerce_missing_text(cls, data):
        if not isinstance(data, dict):
            return data

        def _s(key):
            v = data.get(key)
            if v is None:
                return ""
            if isinstance(v, (list, tuple)):
                return " ".join(str(x) for x in v)
            return v if isinstance(v, str) else str(v)

        data = dict(data)
        original = _s("original_text")
        translated = _s("translated_text")
        if not original and translated:
            original = translated
        if not translated and original:
            translated = original
        data["original_text"] = original
        data["translated_text"] = translated

        sp = data.get("speaker")
        if sp is None or (isinstance(sp, str) and not sp.strip()):
            data["speaker"] = "Unknown speaker"
        elif not isinstance(sp, str):
            data["speaker"] = str(sp)

        ts = data.get("timestamp")
        if ts is None:
            data["timestamp"] = ""
        elif not isinstance(ts, str):
            data["timestamp"] = str(ts)
        return data

    model_config = {"extra": "ignore"}


class AgendaItem(BaseModel):
    topic: str = Field(default="", description="Agenda topic discussed.")
    discussion_summary: str = Field(default="", description="Summary of discussions regarding this topic.")
    decisions_made: list[str] = Field(default_factory=list, description="Key conclusions reached.")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data):
        data = _normalize(
            data, str_fields=("topic", "discussion_summary"), list_fields=("decisions_made",)
        )
        if isinstance(data, dict) and "decisions_made" in data:
            data["decisions_made"] = [
                _clean_str(d) for d in data["decisions_made"] if d is not None
            ]
        return data

    model_config = {"extra": "ignore"}


class DetectedSpeaker(BaseModel):
    speaker_id: str = Field(default="", description="Unique label used in transcription, e.g., 'Speaker 1'.")
    inferred_name: str = Field(default="", description="Inferred full or first name, or 'Unknown'.")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data):
        return _normalize(data, str_fields=("speaker_id", "inferred_name"))

    model_config = {"extra": "ignore"}


class MeetingMinutesReport(BaseModel):
    """The single stitched output of a run.

    Container fields are optional with empty defaults. The model occasionally
    omits a whole section, and a required container rejected the entire report
    after the provider call had already succeeded — losing all of the work.
    """

    title: str = Field(default="", description="Descriptive title for the meeting.")
    date: str = Field(default="Undated", description="Date of the meeting or 'Undated'.")
    meeting_time: str = Field(default="", description="Meeting time range if mentioned.")
    minute_taker: str = Field(default="", description="Minute taker(s) if specified.")
    attendees: list[Attendee] = Field(default_factory=list, description="Detected participants with designations.")
    detected_speakers: list[DetectedSpeaker] = Field(
        default_factory=list,
        description="List of detected speakers and any names inferred from introductions or dialog.",
    )
    executive_summary: str = Field(default="", description="Executive summary of the meeting in English.")
    agenda_and_decisions: list[AgendaItem] = Field(default_factory=list, description="Topic breakdowns and decisions in English.")
    action_items: list[ActionItem] = Field(default_factory=list, description="Action items extracted in English.")
    next_meeting_date: str = Field(default="TBD", description="Date of the next meeting if agreed.")
    next_meeting_time: str = Field(default="TBD", description="Time of next meeting if agreed.")
    next_meeting_agenda_focus: str = Field(default="", description="Agenda focus of upcoming meeting.")
    closing_remarks: str = Field(default="The meeting was concluded.", description="Meeting closing statement.")
    transcript: list[TranscriptEntry] = Field(default_factory=list, description="Bilingual speaker-diarized transcript.")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data):
        return _normalize(
            data,
            str_fields=(
                "title", "date", "meeting_time", "minute_taker", "executive_summary",
                "next_meeting_date", "next_meeting_time", "next_meeting_agenda_focus",
                "closing_remarks",
            ),
            list_fields=(
                "attendees", "detected_speakers", "agenda_and_decisions",
                "action_items", "transcript",
            ),
        )

    model_config = {"extra": "ignore"}


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
    extra_parts: list[tuple[bytes, str]] | None = None,
) -> tuple[MeetingMinutesReport | None, dict | None]:
    extra_parts = extra_parts or []

    # Every part travels in ONE request, in user order, so the model sees a single
    # continuous meeting rather than N unrelated recordings.
    all_parts = [(audio_file_bytes, mime_type)] + list(extra_parts)

    raw_total_mb = sum(len(b) for b, _ in all_parts) / (1024 * 1024)
    log_event(
        log_container,
        logs_list,
        f"Ingested {len(all_parts)} part(s): {raw_total_mb:.2f} MB total",
        "INFO",
    )

    ok, budget_msg = media_pipeline.validate_batch(
        [len(b) for b, _ in all_parts], media_pipeline.MAX_TOTAL_MB
    )
    if not ok:
        raise RuntimeError(budget_msg)

    # Transcode each part down to mono speech bitrate. This is what keeps the run
    # inside the container's memory budget: base64 + JSON serialization costs
    # ~3.7x raw bytes, and 270 MB raw would peak near 1 GB.
    encoded: list[tuple[bytes, str]] = []
    for idx, (raw_bytes, mime) in enumerate(all_parts, start=1):
        suffix = "." + (mime.split("/")[-1].replace("mpeg", "mp3") or "mp3")
        status_text.markdown(
            f"🔄 *Compressing part {idx}/{len(all_parts)} for efficient upload...*"
        )
        progress_bar.progress(int(5 + 20 * (idx - 1) / max(len(all_parts), 1)))
        compressed = media_pipeline.compress_audio(raw_bytes, suffix)
        if len(compressed) < len(raw_bytes):
            log_event(
                log_container,
                logs_list,
                f"Part {idx}: {len(raw_bytes) / (1024 * 1024):.2f} MB → "
                f"{len(compressed) / (1024 * 1024):.2f} MB "
                f"({media_pipeline.COMPRESS_TARGET_KBPS} kbps mono)",
                "DEBUG",
            )
        else:
            log_event(
                log_container,
                logs_list,
                f"Part {idx}: sent unchanged ({len(raw_bytes) / (1024 * 1024):.2f} MB)",
                "DEBUG",
            )
        encoded.append((compressed, "audio/mp3" if compressed is not raw_bytes else mime))
        # Release the raw slice as soon as it has been transcoded.
        all_parts[idx - 1] = (b"", mime)

    status_text.markdown("🔄 *Encoding audio stream to base64...*")
    progress_bar.progress(30)

    b64_start = time.time()
    b64_parts = [
        (base64.b64encode(b).decode("utf-8"), m) for b, m in encoded
    ]
    b64_duration = time.time() - b64_start
    total_b64 = sum(len(x[0]) for x in b64_parts)
    log_event(
        log_container,
        logs_list,
        f"Base64 complete: {len(b64_parts)} part(s), {total_b64:,} chars in {b64_duration:.2f}s",
        "DEBUG",
    )
    progress_bar.progress(33)

    order_note = (
        "You are given "
        + ("a single continuous recording" if len(b64_parts) == 1
           else f"{len(b64_parts)} audio parts of the SAME meeting, "
                "concatenated in chronological order")
        + ". Treat the audio as ONE continuous meeting:\n"
        "- Keep speaker labels CONSISTENT across all parts. The same person must not "
        "receive a different 'Speaker N' label in a later part.\n"
        "- Produce a SINGLE continuous timeline. Do NOT restart timestamps at 00:00 "
        "for each part.\n"
        "- Do not summarise each part separately. Produce ONE transcript and ONE set "
        "of minutes for the meeting as a whole.\n"
    )

    prompt = (
        "You are an expert executive meeting assistant. Listen carefully to this meeting audio recording:\n"
        + (order_note if len(b64_parts) > 1 else "")
        + "1. Identify attendees with their designations/roles if mentioned in 'attendees'.\n"
        "2. Produce a full diarized transcript identifying distinct speakers.\n"
        "3. Transcribe speech verbatim in 'original_text' (preserving native language/words), "
        "and provide an accurate English translation in 'translated_text'.\n"
        "   CRITICAL: every transcript entry MUST include ALL FOUR keys — speaker, timestamp, "
        "original_text, translated_text. Never omit a key and never emit null. Long "
        "transcripts are where this slips: before returning, verify that the FINAL entries "
        "carry all four keys, not just the early ones.\n"
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
        # One inline_data part per file, appended in the user's chosen order.
        media_parts = [
            {"inline_data": {"mime_type": m, "data": d}} for d, m in b64_parts
        ]
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}] + media_parts,
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
        # One input_audio item per file, same order.
        media_items = []
        for d, m in b64_parts:
            audio_fmt = m.split("/")[-1].replace("mpeg", "mp3")
            media_items.append(
                {"type": "input_audio", "input_audio": {"data": d, "format": audio_fmt}}
            )
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}] + media_items,
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

    # Surface any segment the model under-delivered on, rather than silently
    # substituting a fallback. A run where this is non-zero is a degraded
    # response, not a clean one — the user should know which segments to re-read.
    try:
        repaired = [
            i for i, (raw, entry) in enumerate(
                zip(parsed_data.get("transcript") or [], report.transcript)
            )
            if isinstance(raw, dict)
            and (not raw.get("translated_text") or not raw.get("original_text"))
        ]
        if repaired:
            log_event(
                log_container,
                logs_list,
                f"Repaired {len(repaired)} transcript segment(s) the model returned "
                f"incomplete (index: {', '.join(str(i) for i in repaired[:10])}"
                f"{', …' if len(repaired) > 10 else ''}) — a missing translation falls "
                "back to the original text",
                "WARN",
            )
    except Exception:
        pass

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


def apply_datetime_overrides(
    report: MeetingMinutesReport,
    new_date: datetime.date | None,
    new_time: datetime.time | None,
) -> MeetingMinutesReport:
    """Return a copy of `report` with the meeting date and/or start time replaced.

    The document already carries these fields, so overriding them here reaches
    every consumer — the on-screen header, the default DOCX, and a custom Jinja
    template's `{{ date }}` / `{{ meeting_time }}` — without touching the
    transcript or any other content.

    A `None` argument means "leave this field alone", which is how the caller
    distinguishes "the user cleared the widget" from "the user did not touch it".
    """
    updated = report.model_copy(deep=True)
    if new_date is not None:
        updated.date = format_report_date(new_date)
    if new_time is not None:
        updated.meeting_time = format_report_time(new_time)
    return updated


_EMPTY_DATE_TOKENS = {"", "undated", "tbd", "n/a", "na", "none", "unknown", "null", "nil", "-", "--", "---"}
_EMPTY_TIME_TOKENS = {"", "tbd", "n/a", "na", "none", "unknown", "null", "nil", "-", "--", "---"}

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
    "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
    "%B %d %Y", "%b %d %Y", "%d-%b-%Y", "%d %B, %Y", "%Y%m%d",
)


def parse_report_date(raw, fallback: datetime.date | None = None) -> datetime.date | None:
    """Best-effort parse of the model's free-text date into a real date.

    `result.date` is free text — "2026-09-12", but equally "Undated" (the schema
    default), "12 September 2026", or "2026-09-12 (Saturday)". `st.date_input`
    needs an actual `datetime.date`, and `date.fromisoformat("Undated")` raises,
    so an unparseable value must degrade to `fallback` rather than crash the
    render. It never raises.
    """
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    if raw is None:
        return fallback

    text = str(raw).strip()
    if text.lower() in _EMPTY_DATE_TOKENS:
        return fallback

    # Drop trailing annotations: "2026-09-12 (Saturday)", "2026-09-12 - Day 2"
    cleaned = re.split(r"[(\[]", text, maxsplit=1)[0].strip()
    for candidate in (cleaned, text):
        if not candidate:
            continue
        try:
            return datetime.date.fromisoformat(candidate)
        except ValueError:
            pass
        for fmt in _DATE_FORMATS:
            try:
                return datetime.datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
        for sep in (" to ", " until ", " - ", " – ", " — ", "–", "—"):
            if sep in candidate:
                head = candidate.split(sep, 1)[0].strip()
                if head and head != candidate:
                    try:
                        return datetime.date.fromisoformat(head)
                    except ValueError:
                        for fmt in _DATE_FORMATS:
                            try:
                                return datetime.datetime.strptime(head, fmt).date()
                            except ValueError:
                                continue
    return fallback


def parse_report_time(raw, fallback: datetime.time | None = None) -> datetime.time | None:
    """Best-effort parse of the model's free-text time into a real time.

    `meeting_time` is documented as "Meeting time range if mentioned", so a range
    like "10:00 - 11:30" is expected input and its START is the start time. Never
    raises; unparseable input degrades to `fallback`.
    """
    if isinstance(raw, datetime.time):
        return raw
    if isinstance(raw, datetime.datetime):
        return raw.time()
    if raw is None:
        return fallback

    text = str(raw).strip()
    if text.lower() in _EMPTY_TIME_TOKENS:
        return fallback

    # A range: take the start, which is what "Meeting Start Time" means.
    for sep in (" – ", " — ", " - ", " to ", " until ", "–", "—"):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if head:
                text = head
            break

    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%I:%M%p", "%I %p", "%H%M"):
        try:
            return datetime.datetime.strptime(text.upper(), fmt).time()
        except ValueError:
            continue
    return fallback


def format_report_date(value: datetime.date) -> str:
    return value.strftime("%Y-%m-%d")


def format_report_time(value: datetime.time) -> str:
    return value.strftime("%H:%M")


# -----------------------------------------------------------------------------
# Main Application UI
# -----------------------------------------------------------------------------
def main():
    h_col1, h_col2, h_col3 = st.columns([0.65, 0.18, 0.17], vertical_alignment="center")
    with h_col1:
        _logo = _brand_logo_uri()
        if _logo:
            st.markdown(
                '<div class="aima-brand">'
                f'<img src="{_logo}" alt="AIMA">'
                '<span class="aima-brand-text">AI Meeting Assistant</span>'
                "</div>",
                unsafe_allow_html=True,
            )
        else:
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
                "audio_registry",
                "audio_order",
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
    # LEFT PANEL: Workflow Steps + Console
    # =========================================================================
    with col_left:
        st.markdown("#### 1. Upload Audio")
        audio_files = st.file_uploader(
            "Select meeting recording(s)",
            type=["mp3", "wav", "m4a", "ogg", "aac", "mp4"],
            accept_multiple_files=True,
            help=(
                "Supports MP3, WAV, M4A, OGG, AAC, MP4. Upload one file, or several "
                "parts of the same meeting — they are sent to the model in the order "
                "below and returned as one stitched report."
            ),
            label_visibility="collapsed",
        )

        # Serialize ALL of this session's distinct uploads, keyed by file_id.
        # The widget re-indexes its list whenever files are added or removed, and
        # reading a file's bytes again after that is unreliable — so hold them
        # ourselves and key everything off the stable file_id.
        parts: list[media_pipeline.Part] = []
        bytes_by_key: dict[str, bytes] = {}
        mime_by_key: dict[str, str] = {}

        if audio_files:
            st.session_state.setdefault("audio_registry", {})
            st.session_state.setdefault("audio_order", {})
            registry = st.session_state["audio_registry"]
            order_map = st.session_state["audio_order"]

            for f in audio_files:
                if f.file_id not in registry:
                    registry[f.file_id] = f.read()

            # Drop bookkeeping for files the user removed, wrapped in try/except so
            # the widget's internals can never break the main flow.
            try:
                live = {f.file_id for f in audio_files}
                for dead in [k for k in list(registry) if k not in live]:
                    registry.pop(dead, None)
                    order_map.pop(dead, None)
            except Exception:
                pass

            parts = media_pipeline.order_parts(
                [
                    media_pipeline.Part(
                        key=f.file_id, name=f.name, size=len(registry[f.file_id])
                    )
                    for f in audio_files
                ],
                order_map,
            )
            # Seed a default Order for any file the user hasn't positioned yet.
            highest = max(order_map.values(), default=0)
            for p in parts:
                if p.key not in order_map:
                    highest += 1
                    order_map[p.key] = highest
            parts = media_pipeline.order_parts(parts, order_map)

            bytes_by_key = registry
            mime_by_key = {f.file_id: (f.type or "audio/mp3") for f in audio_files}

            n = len(parts)
            total = media_pipeline.total_bytes(parts)
            st.caption(
                f"🎧 **{n} part{'s' if n != 1 else ''}** • "
                f"{media_pipeline.format_size(total)} total — played back top to bottom"
            )
            if n > 1:
                st.caption(
                    "Each part is playable and can be re-ordered. All parts are treated "
                    "as ONE continuous meeting: they are sent to the model in this order "
                    "and returned as a single merged transcript and minutes document."
                )

            for i, part in enumerate(parts, start=1):
                with st.container(border=True):
                    r_col, a_col, o_col = st.columns([0.34, 0.43, 0.23])
                    with r_col:
                        st.markdown(f"**{i}. {part.name}**")
                        st.caption(media_pipeline.format_size(part.size))
                    with a_col:
                        st.audio(bytes_by_key[part.key])
                    with o_col:
                        new_pos = st.number_input(
                            "Order",
                            min_value=1,
                            max_value=n,
                            value=int(order_map.get(part.key, i)),
                            step=1,
                            key=f"pos_{part.key}",
                            label_visibility="collapsed",
                        )
                        if int(new_pos) != int(order_map.get(part.key, i)):
                            order_map[part.key] = int(new_pos)
                            st.rerun()

            ok, msg = media_pipeline.validate_batch(
                [p.size for p in parts], media_pipeline.MAX_TOTAL_MB
            )
            if not ok:
                st.error(f"⚠️ {msg}")

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
                if st.button("🤖 Build Universal AI Template from Sample", use_container_width=True):
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

            if not audio_files:
                st.error("Please upload at least one audio file in Step 1.")
                return

            batch_ok, batch_msg = media_pipeline.validate_batch(
                [len(bytes_by_key[p.key]) for p in parts], media_pipeline.MAX_TOTAL_MB
            )
            if not batch_ok:
                st.error(f"⚠️ {batch_msg}")
                return

            st.session_state["logs_list"] = []
            log_event(log_container, st.session_state["logs_list"], "Execution initialized...", "INFO")

            try:
                # parts is already in play order (natural sort, then user overrides).
                first = parts[0]
                audio_bytes = bytes_by_key[first.key]
                mime = mime_by_key.get(first.key, "audio/mp3")
                extra = [
                    (bytes_by_key[p.key], mime_by_key.get(p.key, "audio/mp3"))
                    for p in parts[1:]
                ]

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
                    extra_parts=extra,
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
                |  • Attendee Rosters (Names & Designations)                  |
                |  • Diarized Speaker Verification & Re-mapping               |
                |  • Bilingual Transcript (Original Spoken + Translated)      |
                |  • Action Items Matrix (Owner, Department, Due, Remarks)    |
                |  • Universal DOCX Generation & Export                       |
                +-------------------------------------------------------------+
                ```
                """
            )
        else:
            result: MeetingMinutesReport = st.session_state["meeting_result"]
            active_template = st.session_state.get("saved_template_bytes")

            t_col1, t_col2 = st.columns([0.65, 0.35])
            with t_col1:
                st.markdown(f"## {result.title}")
                attendee_names = [a.name for a in result.attendees]
                st.caption(f"📅 **Date:** {result.date} | 👥 **Attendees:** {', '.join(attendee_names)}")
            with t_col2:
                template_used = False
                if active_template:
                    try:
                        doc_io = render_template_docx(active_template, result)
                        template_used = True
                    except Exception as ex:
                        st.warning(f"⚠️ Template rendering issue: {ex}. Using clean layout.")
                        doc_io = build_default_docx(result)
                        template_used = False
                else:
                    doc_io = build_default_docx(result)
                    template_used = False

                download_label = "📥 Download Filled Template (.docx)" if template_used else "📥 Download Standard .docx"
                st.download_button(
                    label=download_label,
                    data=doc_io,
                    file_name=f"{result.title.replace(' ', '_')}_Minutes.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                    type="primary",
                )

            # Meeting Date & Start Time Component
            #
            # Sits ABOVE the speaker panel: date and time describe the meeting as a
            # whole, so settling them first means the speaker mapping applies on top
            # of a correct header.
            #
            # `result.date` is free text from the model and may be 'Undated' (the
            # schema default) or a verbose form, none of which `st.date_input`
            # accepts — so both values are parsed defensively before being used as
            # widget defaults, and a value that cannot be read falls back to today
            # with an explicit warning rather than crashing the render.
            _today = datetime.date.today()
            _parsed_date = parse_report_date(result.date, fallback=_today)
            _parsed_time = parse_report_time(result.meeting_time, fallback=datetime.time(9, 0))
            _date_unreadable = parse_report_date(result.date) is None and bool((result.date or "").strip())

            with st.expander(
                f"📅 Meeting Date & Start Time — {result.date.strip() or 'not set'}"
                f", {result.meeting_time.strip() or 'not set'}",
                expanded=False,
            ):
                if _date_unreadable:
                    st.warning(
                        f"The transcript did not yield a readable date (`{result.date}`). "
                        "Defaulted to today — please confirm."
                    )

                with st.form("datetime_form"):
                    d_col, t_col = st.columns(2)
                    with d_col:
                        chosen_date = st.date_input(
                            "Meeting Date", value=_parsed_date, key="meeting_date_input",
                            help="Correct the meeting date used in the minutes header and exports.",
                        )
                    with t_col:
                        chosen_time = st.time_input(
                            "Meeting Start Time", value=_parsed_time, key="meeting_time_input",
                            step=datetime.timedelta(minutes=1),
                            help="Start time of the meeting. Applied to the header and exports.",
                        )

                    if st.form_submit_button("💾 Save Date & Time", use_container_width=True):
                        st.session_state["meeting_result"] = apply_datetime_overrides(
                            result, chosen_date, chosen_time
                        )
                        st.rerun()

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
            tab_overview, tab_actions, tab_attendees, tab_transcript, tab_raw = st.tabs(
                ["📄 Overview & Agendas", "✅ Action Items", "👥 Attendees", "📝 Diarized Transcript", "🔧 Raw Data"]
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

                if result.next_meeting_date != "TBD" or result.next_meeting_agenda_focus:
                    st.markdown("---")
                    st.markdown("### Next Meeting Logistics")
                    st.write(f"**Date:** {result.next_meeting_date} | **Time:** {result.next_meeting_time}")
                    if result.next_meeting_agenda_focus:
                        st.write(f"**Agenda Focus:** {result.next_meeting_agenda_focus}")

            with tab_actions:
                st.markdown("### Action Items Matrix")
                if result.action_items:
                    st.dataframe([item.model_dump() for item in result.action_items], use_container_width=True)
                else:
                    st.info("No action items detected in the discussion.")

            with tab_attendees:
                st.markdown("### Attendee Roster")
                if result.attendees:
                    st.dataframe([a.model_dump() for a in result.attendees], use_container_width=True)
                else:
                    st.info("No attendees recorded.")

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


# -----------------------------------------------------------------------------
# Version stamp — rendered last so it sits at the foot of the page.
# Reads APP_VERSION, never a literal, so it cannot drift from the constant.
# -----------------------------------------------------------------------------
st.caption(f"AIMA {APP_VERSION}")
