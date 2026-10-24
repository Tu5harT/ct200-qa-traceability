import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_

from app import llm
from app.db import Document, Node, Selection, SessionLocal, Version, create_tables
from app.ingest import ingest
from app.staleness import check_generation
from app.store import get_store
from app.versioning import word_diff

DATA_DIR = "data"
DOC_NAME = "ct200_manual"

app = FastAPI(title="CT-200 manual API")
create_tables()


def get_db():
    with SessionLocal() as db:
        yield db


def get_version(db, number=None):
    # no number = latest version
    query = db.query(Version).join(Document).filter(Document.name == DOC_NAME)
    if number is None:
        version = query.order_by(Version.number.desc()).first()
    else:
        version = query.filter(Version.number == number).first()
    if version is None:
        raise HTTPException(404, "version not found, ingest a pdf first")
    return version


def get_node(db, node_id):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    return node


def short(node):
    return {"id": node.id, "section": node.section_id, "heading": node.heading, "level": node.level}


def full(node):
    return {
        **short(node),
        "version": node.version.number,
        "parent_id": node.parent_id,
        "page": node.page,
        "status": node.status,
        "logical_id": node.logical_id,
        "hash": node.hash,
        "body": node.body,
        "children": [short(c) for c in node.children],
    }


class IngestRequest(BaseModel):
    filename: str


@app.post("/ingest")
def ingest_pdf(req: IngestRequest, db=Depends(get_db)):
    # only files from data/, we don't want people reading random paths on the server
    path = os.path.join(DATA_DIR, os.path.basename(req.filename))
    if not os.path.isfile(path):
        raise HTTPException(404, f"{req.filename} not found in {DATA_DIR}/")

    version = ingest(db, path, DOC_NAME)
    return {
        "version": version.number,
        "nodes": len(version.nodes),
        "warnings": version.warnings,
        "changes": [
            {"id": n.id, "section": n.section_id, "status": n.status}
            for n in version.nodes
            if n.status in ("changed", "added")
        ],
    }


@app.get("/versions")
def list_versions(db=Depends(get_db)):
    doc = db.query(Document).filter_by(name=DOC_NAME).first()
    if doc is None:
        return []
    return [{"version": v.number, "filename": v.filename, "title": v.title, "warnings": v.warnings} for v in doc.versions]


@app.get("/sections")
def top_sections(version: int = None, db=Depends(get_db)):
    v = get_version(db, version)
    return {"version": v.number, "sections": [short(n) for n in v.nodes if n.parent_id is None]}


@app.get("/nodes/{node_id}")
def read_node(node_id: int, db=Depends(get_db)):
    return full(get_node(db, node_id))


@app.get("/search")
def search(q: str, version: int = None, db=Depends(get_db)):
    v = get_version(db, version)
    pattern = f"%{q}%"
    nodes = (
        db.query(Node)
        .filter(Node.version_id == v.id)
        .filter(or_(Node.heading.ilike(pattern), Node.body.ilike(pattern)))
        .order_by(Node.order)
        .all()
    )
    return {"version": v.number, "results": [short(n) for n in nodes]}


@app.get("/nodes/{node_id}/changes")
def node_changes(node_id: int, db=Depends(get_db)):
    node = get_node(db, node_id)
    latest = get_version(db)

    # every version of this section, oldest first
    history = (
        db.query(Node)
        .join(Version)
        .filter(Node.logical_id == node.logical_id, Version.document_id == latest.document_id)
        .order_by(Version.number)
        .all()
    )
    current = next((n for n in history if n.version_id == latest.id), None)

    result = {
        "node_id": node.id,
        "version": node.version.number,
        "latest_version": latest.number,
        "history": [{"version": n.version.number, "node_id": n.id, "status": n.status} for n in history],
    }
    if current is None:
        result.update(changed=True, removed=True, diff=[])
    else:
        result.update(
            changed=current.hash != node.hash,
            removed=False,
            latest_node_id=current.id,
            diff=word_diff(node.body, current.body),
        )
    return result


class SelectionRequest(BaseModel):
    name: str = Field(min_length=1)
    node_ids: list[int] = Field(min_length=1)


def selection_out(sel):
    return {
        "id": sel.id,
        "name": sel.name,
        "version": sel.nodes[0].version.number,
        "created_at": sel.created_at,
        # the exact text the selection was made against
        "nodes": [{**short(n), "hash": n.hash, "body": n.body} for n in sel.nodes],
    }


@app.post("/selections", status_code=201)
def create_selection(req: SelectionRequest, db=Depends(get_db)):
    ids = set(req.node_ids)
    nodes = db.query(Node).filter(Node.id.in_(ids)).all()

    missing = ids - {n.id for n in nodes}
    if missing:
        raise HTTPException(404, f"unknown node ids: {sorted(missing)}")

    # mixing v1 and v2 text in one selection would make the test cases meaningless
    if len({n.version_id for n in nodes}) > 1:
        raise HTTPException(400, "all nodes in a selection must be from the same version")

    sel = Selection(name=req.name, nodes=nodes)
    db.add(sel)
    db.commit()
    return selection_out(sel)


@app.get("/selections/{selection_id}")
def read_selection(selection_id: int, db=Depends(get_db)):
    return selection_out(get_selection(db, selection_id))


def get_selection(db, selection_id):
    sel = db.get(Selection, selection_id)
    if sel is None:
        raise HTTPException(404, "selection not found")
    return sel


def generation_out(doc):
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.post("/selections/{selection_id}/generate")
def generate_tests(selection_id: int, force: bool = False, db=Depends(get_db), store=Depends(get_store)):
    sel = get_selection(db, selection_id)

    # selections are pinned to fixed text, so a second run would just be a re-roll of the same input.
    # give back the saved one, unless the caller really wants a fresh one (force=true keeps the old one too)
    if not force:
        existing = store.find_one({"selection_id": sel.id, "status": "ok"}, sort=[("created_at", -1)])
        if existing:
            return {**generation_out(existing), "reused": True}

    nodes = llm.collect_nodes(sel.nodes)
    doc = {
        "selection_id": sel.id,
        "selection_name": sel.name,
        "version": sel.nodes[0].version.number,
        # exact text the tests come from, this is what staleness gets checked against later
        "sources": [
            {"node_id": n.id, "logical_id": n.logical_id, "section_id": n.section_id, "hash": n.hash}
            for n in nodes
        ],
        "model": llm.MODEL,
        "prompt_version": llm.PROMPT_VERSION,
        "created_at": datetime.now(timezone.utc),
    }

    try:
        cases, attempts = llm.generate(llm.sections_text(nodes), {n.section_id for n in nodes})
    except llm.GenerationFailed as e:
        # keep the failure so we can see what the model sent, but never as usable test cases
        store.insert_one({**doc, "status": "failed", "error": str(e), "raw": e.raw})
        raise HTTPException(502, f"LLM gave invalid output {llm.MAX_ATTEMPTS} times: {e}")

    doc.update(status="ok", attempts=attempts, test_cases=[c.model_dump() for c in cases])
    store.insert_one(doc)
    return {**generation_out(doc), "reused": False}


@app.get("/generations")
def list_generations(selection_id: int = None, node_id: int = None, db=Depends(get_db), store=Depends(get_store)):
    if selection_id is None and node_id is None:
        raise HTTPException(400, "pass selection_id or node_id")

    if selection_id is not None:
        query = {"selection_id": selection_id}
    else:
        # by logical id, so asking with the v2 node also finds tests made from the v1 text
        query = {"sources.logical_id": get_node(db, node_id).logical_id}
    query["status"] = "ok"

    latest = get_version(db)
    docs = sorted(store.find(query), key=lambda d: d["created_at"], reverse=True)
    # staleness is worked out now, not when the tests were saved, so it's always against the latest pdf
    return [check_generation(db, generation_out(d), latest) for d in docs]
