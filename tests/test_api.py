import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app, get_db


@pytest.fixture
def client():
    # one shared in-memory db for the whole test (StaticPool keeps the same connection)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def test_db():
        with TestSession() as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def find(client, section, version):
    sections = client.get("/search", params={"q": "", "version": version}).json()["results"]
    return next(s for s in sections if s["section"] == section)


def test_browse_before_ingest_is_404(client):
    assert client.get("/sections").status_code == 404


def test_ingest_only_reads_from_data_folder(client):
    assert client.post("/ingest", json={"filename": "../app/main.py"}).status_code == 404


def test_sections_and_node(client):
    client.post("/ingest", json={"filename": "ct200_manual.pdf"})

    sections = client.get("/sections").json()["sections"]
    assert [s["section"] for s in sections] == ["1", "2", "3", "4", "5", "6", "7", "8"]

    node = client.get(f"/nodes/{sections[2]['id']}").json()
    assert node["heading"] == "Device Operation"
    assert [c["section"] for c in node["children"]] == ["3.1", "3.2", "3.4", "3.3"]
    assert len(node["hash"]) == 64


def test_search_finds_cuff_despite_ligatures(client):
    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    results = client.get("/search", params={"q": "cuff"}).json()["results"]
    assert "3.2" in [r["section"] for r in results]


def test_changes_between_v1_and_v2(client):
    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    r = client.post("/ingest", json={"filename": "ct200_manual_v2.pdf"}).json()
    assert r["version"] == 2

    # default version is now 2, but v1 is still there
    assert len(client.get("/sections").json()["sections"]) == 8
    old_32 = find(client, "3.2", version=1)

    changes = client.get(f"/nodes/{old_32['id']}/changes").json()
    assert changes["changed"] is True
    assert "'40' -> '30'" in changes["diff"]
    assert [h["version"] for h in changes["history"]] == [1, 2]

    old_31 = find(client, "3.1", version=1)
    assert client.get(f"/nodes/{old_31['id']}/changes").json()["changed"] is False


def test_selection_keeps_old_text_after_v2(client):
    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    old_32 = find(client, "3.2", version=1)

    sel = client.post("/selections", json={"name": "inflation", "node_ids": [old_32["id"]]})
    assert sel.status_code == 201
    sel_id = sel.json()["id"]

    client.post("/ingest", json={"filename": "ct200_manual_v2.pdf"})

    # still the v1 text, even though v2 is now the latest
    after = client.get(f"/selections/{sel_id}").json()
    assert after["version"] == 1
    assert "40 mmHg increments" in after["nodes"][0]["body"]


def test_selection_validation(client):
    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    client.post("/ingest", json={"filename": "ct200_manual_v2.pdf"})
    v1_node = find(client, "3.2", version=1)["id"]
    v2_node = find(client, "3.2", version=2)["id"]

    assert client.post("/selections", json={"name": "x", "node_ids": []}).status_code == 422
    assert client.post("/selections", json={"name": "x", "node_ids": [99999]}).status_code == 404
    mixed = client.post("/selections", json={"name": "x", "node_ids": [v1_node, v2_node]})
    assert mixed.status_code == 400


class FakeStore:
    # just enough of a mongo collection for these tests
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        doc["_id"] = len(self.docs) + 1
        self.docs.append(doc)

    def find(self, query):
        def matches(doc, key, value):
            if "." in key:  # "sources.logical_id" -> any item in the list matches
                field, sub = key.split(".")
                return any(item.get(sub) == value for item in doc.get(field, []))
            return doc.get(key) == value

        return [dict(d) for d in self.docs if all(matches(d, k, v) for k, v in query.items())]

    def find_one(self, query, sort=None):
        found = self.find(query)
        return found[-1] if found else None


def test_generate_twice_reuses_unless_forced(client, monkeypatch):
    from app import llm
    from app.main import get_store
    from tests.test_llm import GOOD

    store = FakeStore()
    app.dependency_overrides[get_store] = lambda: store
    monkeypatch.setattr(llm, "call_groq", lambda messages: GOOD)

    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    sel = client.post("/selections", json={"name": "x", "node_ids": [find(client, "3.2", 1)["id"]]}).json()

    first = client.post(f"/selections/{sel['id']}/generate").json()
    assert first["reused"] is False
    assert first["sources"][0]["section_id"] == "3.2"

    again = client.post(f"/selections/{sel['id']}/generate").json()
    assert again["reused"] is True and again["id"] == first["id"]

    forced = client.post(f"/selections/{sel['id']}/generate", params={"force": True}).json()
    assert forced["id"] != first["id"]
    assert len(store.docs) == 2


def test_bad_llm_output_is_502_and_saved_as_failed(client, monkeypatch):
    from app import llm
    from app.main import get_store

    store = FakeStore()
    app.dependency_overrides[get_store] = lambda: store
    monkeypatch.setattr(llm, "call_groq", lambda messages: "not json")

    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    sel = client.post("/selections", json={"name": "x", "node_ids": [find(client, "3.2", 1)["id"]]}).json()

    r = client.post(f"/selections/{sel['id']}/generate")
    assert r.status_code == 502
    assert store.docs[0]["status"] == "failed"
    assert "test_cases" not in store.docs[0]


def test_tests_go_stale_after_v2(client, monkeypatch):
    import json

    from app import llm
    from app.main import get_store
    from tests.test_llm import case

    store = FakeStore()
    app.dependency_overrides[get_store] = lambda: store
    reply = json.dumps({"test_cases": [case(sections=["3.2"]), case(sections=["3.1"]), case(sections=["5.1"])]})
    monkeypatch.setattr(llm, "call_groq", lambda messages: reply)

    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    ids = [find(client, s, 1)["id"] for s in ("3.1", "3.2", "5")]  # "5" brings 5.1 and 5.2 with it
    sel = client.post("/selections", json={"name": "x", "node_ids": ids}).json()
    client.post(f"/selections/{sel['id']}/generate")

    before = client.get("/generations", params={"selection_id": sel["id"]}).json()[0]
    assert before["stale"] is False

    client.post("/ingest", json={"filename": "ct200_manual_v2.pdf"})
    after = client.get("/generations", params={"selection_id": sel["id"]}).json()[0]

    assert after["stale"] is True
    assert after["checked_against_version"] == 2
    status = {s["section_id"]: s for s in after["source_status"]}
    assert status["3.2"]["kind"] == "numbers changed"
    assert status["3.1"]["status"] == "unchanged"
    assert after["new_sections"] == ["5.3"]  # added under section 5, not covered by any test
    # only the test built on 3.2 is stale
    assert [c["stale"] for c in after["test_cases"]] == [True, False, False]

    # asking with the v2 node of 3.2 still finds tests made from the v1 text
    by_node = client.get("/generations", params={"node_id": find(client, "3.2", 2)["id"]}).json()
    assert len(by_node) == 1


def test_generations_needs_a_filter(client):
    assert client.get("/generations").status_code == 400


def test_leftover_generation_from_old_db_is_ignored(client, monkeypatch):
    # this really happened: ct200.db was deleted, mongo wasn't, and the new selection 1
    # got the old selection 1's test cases
    from datetime import datetime

    from app import llm
    from app.main import get_store
    from tests.test_llm import GOOD

    store = FakeStore()
    store.insert_one({"selection_uid": "from-old-db", "selection_id": 1, "status": "ok",
                      "created_at": datetime(2026, 1, 1), "sources": [], "test_cases": []})
    app.dependency_overrides[get_store] = lambda: store
    monkeypatch.setattr(llm, "call_groq", lambda messages: GOOD)

    client.post("/ingest", json={"filename": "ct200_manual.pdf"})
    sel = client.post("/selections", json={"name": "x", "node_ids": [find(client, "3.2", 1)["id"]]}).json()
    assert sel["id"] == 1

    gen = client.post(f"/selections/{sel['id']}/generate").json()
    assert gen["reused"] is False

    listed = client.get("/generations", params={"selection_id": 1}).json()
    assert [g["id"] for g in listed] == [gen["id"]]
