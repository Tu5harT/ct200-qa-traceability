# CT-200 Manual QA API

Backend that turns the CardioTrack CT-200 manual (PDF) into a versioned section tree, lets you pick sections,
asks an LLM for QA test case ideas, and tells you later if those test cases still match the latest manual.

Design notes and the decision log are in [APPROACH.md](APPROACH.md).

## Stack

FastAPI, Pydantic, SQLAlchemy + SQLite (tree, versions, selections), MongoDB (LLM output),
PyMuPDF (PDF text), Groq (LLM), pytest.

## Setup

Needs Python 3.10+ and a local MongoDB running on the default port.

```
python -m venv venv
venv\Scripts\activate            (Windows)   or   source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env           then put your Groq key in .env
```

### Env vars (`.env`)

| Name | Default | What |
|---|---|---|
| `GROQ_API_KEY` | - | required for generation, free key from console.groq.com |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | any Groq chat model that supports JSON mode |
| `MONGO_URL` | `mongodb://localhost:27017` | generations go to db `ct200`, collection `generations` |
| `DB_URL` | `sqlite:///ct200.db` | SQLite file for the tree, versions and selections |

## Run

```
python -m uvicorn app.main:app --reload
```

Swagger page with all endpoints: http://localhost:8000/docs

## Tests

```
python -m pytest -v
```

37 tests. They use an in-memory SQLite, a fake Mongo collection and a fake LLM, so no network or key is needed.
The parser tests (`tests/test_parser.py`) cover the PDF irregularities: duplicate heading, skipped level,
body-size heading, out of order sections, numbered list that looks like headings, page breaks, title on 3 lines.

## v1 -> v2 re-ingestion flow

The quickest way is the demo script, it runs the whole story against the running server and prints each step:

```
python -m uvicorn app.main:app        (terminal 1)
python demo.py                        (terminal 2)
```

It does: ingest v1 -> select sections -> generate test cases (real LLM call) -> generate again (reused) ->
check staleness (fresh) -> ingest v2 -> diff of 3.2 -> check staleness again (3.2 and 4.2 tests are stale,
5.3 is new and not covered) -> look up by the v2 node -> show the selection still holds v1 text.

The demo needs a clean start. To reset:

```
del ct200.db                      (rm ct200.db on mac/linux)
python -c "import app.store as s; s.generations.delete_many({})"
```

### Same flow by hand with curl

```bash
curl -X POST localhost:8000/ingest -H "Content-Type: application/json" -d '{"filename": "ct200_manual.pdf"}'
curl "localhost:8000/search?q=inflation&version=1"            # note the id of 3.2
curl -X POST localhost:8000/selections -H "Content-Type: application/json" -d '{"name": "inflation", "node_ids": [ID]}'
curl -X POST localhost:8000/selections/1/generate
curl "localhost:8000/generations?selection_id=1"              # stale: false

curl -X POST localhost:8000/ingest -H "Content-Type: application/json" -d '{"filename": "ct200_manual_v2.pdf"}'
curl "localhost:8000/nodes/ID/changes"                        # changed: true, diff '40' -> '30'
curl "localhost:8000/generations?selection_id=1"              # stale: true, with which test cases and why
```

(On Windows PowerShell use `curl.exe` and escape the quotes, or just use the `/docs` page.)

## Endpoints

| Method | URL | What |
|---|---|---|
| POST | `/ingest` `{"filename": ...}` | parse a PDF from `data/` and store it as a new version (same file twice = same version) |
| GET | `/versions` | versions with their parser warnings |
| GET | `/sections?version=` | top level sections, latest version by default |
| GET | `/nodes/{id}` | node with children, full text, hash, change status |
| GET | `/search?q=&version=` | search headings and text |
| GET | `/nodes/{id}/changes` | history of this section across versions + word diff against the latest |
| POST | `/selections` `{"name", "node_ids"}` | named selection, pinned to those node+version rows |
| GET | `/selections/{id}` | the selection with the exact text it was made from |
| POST | `/selections/{id}/generate?force=false` | 3-5 test cases from the LLM, reuses the saved ones unless `force=true` |
| GET | `/generations?selection_id=` or `?node_id=` | saved test cases with a staleness check against the latest version |

## Layout

```
app/pdf_reader.py   PDF -> lines (font, bold, position) and tables
app/parser.py       lines -> section tree + warnings
app/db.py           SQLite models
app/ingest.py       parse + store a version
app/versioning.py   match nodes between versions, word diff
app/llm.py          prompt, Groq call, validation, retry
app/store.py        Mongo collection
app/staleness.py    compare saved generations with the latest version
app/main.py         FastAPI endpoints
demo.py             end to end walkthrough
data/               the two sample PDFs
```
