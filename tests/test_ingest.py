import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base, Node
from app.ingest import ingest

V1 = "data/ct200_manual.pdf"
V2 = "data/ct200_manual_v2.pdf"


@pytest.fixture
def session():
    # fresh in-memory db for every test, so tests don't touch ct200.db
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_v2_does_not_replace_v1(session):
    v1 = ingest(session, V1)
    v2 = ingest(session, V2)

    assert (v1.number, v2.number) == (1, 2)
    assert len(v1.nodes) == 27
    assert len(v2.nodes) == 28  # 5.3 Data Export is new
    # old text is still there
    old_32 = next(n for n in v1.nodes if n.section_id == "3.2")
    assert "40 mmHg increments" in old_32.body


def test_same_file_twice_is_not_a_new_version(session):
    first = ingest(session, V1)
    again = ingest(session, V1)
    assert first.id == again.id
    assert session.query(Node).count() == 27


def test_parents_are_linked_in_db(session):
    v1 = ingest(session, V1)
    node = next(n for n in v1.nodes if n.section_id == "2.1.1.1")
    assert node.parent.section_id == "2.1"
    assert node.parent.version_id == v1.id
    section_3 = next(n for n in v1.nodes if n.section_id == "3")
    assert [c.section_id for c in section_3.children] == ["3.1", "3.2", "3.4", "3.3"]
