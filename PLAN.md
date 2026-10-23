# CT-200 Assignment – Work Plan

## What I found in the PDFs (before writing any code)

Both PDFs are "born digital": they have a real text layer and contain **no images**.
So we read the text layer directly (PyMuPDF). OCR would only add mistakes here.
OCR stays as a fallback for pages that have no text layer (explained in the approach doc).

Irregularities in v1 the parser must handle:

| # | Irregularity | Where | Why it is a trap |
|---|---|---|---|
| 1 | Heading in body-size font (11pt instead of 12.9pt) | `3.2 Cuff Inflation Sequence` | A font-size-only parser makes it body text and merges it into 3.1 |
| 2 | Skipped level (`2.1.1.1` with no `2.1.1`) | page 2 | Parent must be `2.1`, and we flag the missing level |
| 3 | Out-of-order sections (`3.4` before `3.3`) | page 3 | Keep the reading order, but parent is still `3` |
| 4 | Duplicate heading text ("Error Codes") | `4.2` and `7.1` | Need two different node IDs with different parents |
| 5 | Numbered list that looks like headings (`1. Normal: ...`) | inside 3.3 | Must NOT become top-level sections 1–5 |
| 6 | Tables (one has multi-line cells) | 2.1 specs, 4.2 error codes | Rows must stay together and not become loose lines |
| 7 | Sections that run across pages | `2.` → `2.1`, `3.1` body | Page breaks must not cut a section |
| 8 | Title spread over 3 lines | page 1 | Must become one title |
| 9 | Ligatures and special hyphens (`ﬁ`, `ﬀ`, `‑`) | everywhere | Search for "cuff" has to work |
| 10 | Cross-references ("see 2.1, 4.3") | 3.3 | Must not be read as headings |

What changes from v1 to v2 (this is our test data for versioning and staleness):
- 2.1.1.1: 300 → 250 cycles, 15% → 10% (numbers changed)
- 3.2: 40 → 30 mmHg increments, plus an extra sentence
- 4.2 table: E3 "2 seconds" → "1.5 seconds", new row **E6**
- 4.3: "E1–E5" → "E1–E6"
- **5.3 Data Export**: a new section
- 3.1: unchanged text, but it moved across a page break (it must NOT be flagged as changed)
- v2 has 7 pages instead of 6

## Tech stack
Python 3.13, FastAPI, Pydantic, SQLAlchemy + SQLite, MongoDB (local, already installed),
PyMuPDF, pytest, and one LLM provider (Groq free tier, or Ollama, which is installed locally).

## Steps (🔖 = stop here so you can commit)

### Step 0 – Project setup
- Folder structure, `venv`, `requirements.txt`, `.gitignore`, `data/` holding both PDFs
- 🔖 commit: "project skeleton"

### Step 1 – PDF reading (raw lines)
- Read every line with its font size, bold flag and x position
- Clean up ligatures and special hyphens
- 🔖 commit: "read pdf text layer"

### Step 2 – Parser: build the tree
- Detect headings by **section number + bold font** (not by font size, see #1)
- Level = number of parts in the section number
- Parent = closest earlier heading whose number is a prefix (handles #2 and #3)
- List items are regular font and indented, so they stay body text (#5)
- Tables become text rows inside their section (#6)
- Each node gets: id, section number, heading, level, body, parent, children, content hash
- Parser warnings list (missing level, out of order) instead of failing silently
- 🔖 commit: "parser builds section tree"

### Step 3 – Parser unit tests (at least 3, we'll write ~6)
- Duplicate heading → 2 node IDs with different parents
- 2.1.1.1 → parent is 2.1, plus a warning
- 3.2 detected even though its font is small
- List items in 3.3 are not headings
- 3.4/3.3 order → both under 3
- 🔖 commit: "parser tests"

### Step 4 – Database models (SQLite) and ingestion
- Tables: `documents`, `versions`, `nodes`, `selections`, `selection_items`
- `ingest(pdf)` → new version, never deletes the old one
- 🔖 commit: "db models + ingestion"

### Step 5 – Versioning / matching v1 ↔ v2
- Match by `section number + normalized heading` → same "logical id"
- Compare the content hash: unchanged / changed / added / removed
- Test: 3.1 unchanged, 3.2 changed, 5.3 added
- 🔖 commit: "version matching"

### Step 6 – Browse API
- `GET /sections?version=` top-level list
- `GET /nodes/{id}` node + children + text + hash
- `GET /search?q=`
- `GET /nodes/{id}/changes` changed or not + small diff
- 🔖 commit: "browse api"

### Step 7 – Selection API
- `POST /selections` {name, node_ids} → pinned to the node+version rows
- `GET /selections/{id}` returns the exact old text, even after v2
- 🔖 commit: "selections"

### Step 8 – LLM generation
- Prompt → strict JSON → Pydantic validation
- Bad JSON / wrong shape → 1 retry that sends back the error → still bad → HTTP 502, nothing saved as "good"
- Store in MongoDB: selection id, node ids, node hashes, test cases
- Same selection submitted twice → return the saved result unless `?force=true`
- 🔖 commit: "llm generation"

### Step 9 – Staleness + Retrieval API
- `GET /generations?selection_id=` / `?node_id=`
- For each generation compare the stored hash with the latest version → `fresh` / `stale`
- Also say *what kind* of change: numbers changed (e.g. 40→30 mmHg) vs wording only
- 🔖 commit: "staleness + retrieval"

### Step 10 – End-to-end demo script
- `demo.py`: ingest v1 → select 3.2 → generate → ingest v2 → retrieval shows STALE
- 🔖 commit: "e2e demo"

### Step 11 – Docs
- README (setup, env vars, tests, v1→v2 flow)
- APPROACH.md (parsing, irregularities, matching, prompt, decision log)
- 🔖 commit: "docs"
