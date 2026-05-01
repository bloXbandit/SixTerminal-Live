# Six Terminal Live

**Local desktop tool for natural-language schedule editing.**

Six Terminal Live lets you edit Primavera P6 schedules using plain English. Drop in an XER or P6 XML file, type what you want to change, and get back a clean P6 XML file ready to import into P6.

---

## What it does

- **Targeted edits** — Change activity names, durations, calendars, or codes for individual activities or bulk groups
- **Logic surgery** — Add or remove predecessor/successor relationships using natural language
- **WBS restructuring** — Rename WBS nodes, create new ones, move activities between them
- **Zero-to-one creation** — Generate a complete P6 XML schedule from a plain English description
- **Accepts XER or P6 XML** — Works with whatever the GC submitted
- **Always outputs P6 XML** — Safe, structured, guaranteed clean import into P6

---

## Architecture

```
User (natural language)
        ↓
  LLM Interpreter (Claude)
  Translates instruction → JSON Edit Command
        ↓
  Edit Engine (Python)
  Applies command to parsed schedule tree
        ↓
  P6 XML Writer
  Serializes valid P6 XML output
        ↓
  _edited.xml → Import into P6
```

---

## Project structure

```
SixTerminal-Live/
├── engine/
│   ├── xer_reader.py       # Parse XER → internal schedule model
│   ├── xml_reader.py       # Parse P6 XML → internal schedule model
│   ├── xml_writer.py       # Serialize schedule model → valid P6 XML
│   ├── edit_engine.py      # Apply JSON edit commands to schedule model
│   └── schedule_model.py   # Internal data model (Project, WBS, Activity, Relation)
├── interpreter/
│   └── llm_interpreter.py  # Natural language → JSON edit command via Claude API
├── ui/
│   ├── app.py              # Local Flask web server (desktop UI)
│   ├── templates/
│   │   └── index.html      # Chat interface with drag-drop file upload
│   └── static/             # CSS / JS
├── tests/
│   └── test_engine.py      # Unit tests for edit operations
├── samples/                # Sample XER/XML files for testing
├── main.py                 # Entry point — launches local server + opens browser
├── requirements.txt
└── README.md
```

---

## Quickstart

```bash
pip install -r requirements.txt
python main.py
# Opens http://localhost:5100 in your browser
```

Drop in an XER or XML file, type your instruction, download the edited XML.

---

## Output format

All edits are exported as **P6 XML**. To apply changes:
1. Open Primavera P6
2. File → Import → Primavera PM (XML)
3. Select the `_edited.xml` file
4. P6 handles all internal ID generation and schedule recalculation

---

## Requirements

- Python 3.10+
- Anthropic API key (set as `ANTHROPIC_API_KEY` environment variable)
- Primavera P6 (for importing the output XML)
