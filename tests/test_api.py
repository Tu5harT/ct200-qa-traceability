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
