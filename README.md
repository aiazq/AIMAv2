# 🎙️ AIMA — AI Meeting Assistant

**AIMA** transforms multi-speaker meeting recordings into structured minutes, speaker-diarized transcripts, key decisions, and actionable items. It exports directly into formatted Microsoft Word (`.docx`) files using either a default executive layout or your team's custom Jinja-tagged templates.

Powered by **Streamlit** and **Gemini 3.6 Flash**.

---

## ✨ Features

- **End-to-End Audio Processing:** Handles transcription, voice separation (diarization), summarization, and task extraction in a single pass.
- **Custom DOCX Templating:** Upload your company's Word template (`.docx`) with Jinja2 placeholders, or let AIMA construct a clean, standard executive document.
- **Action Item Extraction:** Captures assignees, task descriptions, deadlines, and priorities directly into an interactive data table.
- **Lightweight Architecture:** Uses cloud multimodal APIs—no local FFmpeg, Whisper, or C++ compile dependencies required.
- **Cloud Ready:** Optimized for 1-click deployment on Streamlit Community Cloud.

---

## 🚀 Quick Start (Local Setup)

### 1. Clone the Repository
```bash
git clone https://github.com/mahmudaq/AIMA.git
cd AIMA
