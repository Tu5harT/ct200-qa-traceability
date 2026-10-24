import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.ingest import ingest
from app.versioning import match_nodes


@pytest.fixture
def versions():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        v1 = ingest(s, "data/ct200_manual.pdf")
        v2 = ingest(s, "data/ct200_manual_v2.pdf")
        yield {n.section_id: n for n in v1.nodes}, {n.section_id: n for n in v2.nodes}


def test_changed_and_added_sections_are_flagged(versions):
    v1, v2 = versions
    changed = sorted(k for k, n in v2.items() if n.status == "changed")
    added = [k for k, n in v2.items() if n.status == "added"]
    assert changed == ["2.1.1.1", "3.2", "4.2", "4.3"]
    assert added == ["5.3"]


def test_unchanged_section_is_the_same_logical_node(versions):
    v1, v2 = versions
    # 3.1 moved across a page break in v2 but the text is the same
    assert v2["3.1"].status == "unchanged"
    assert v2["3.1"].logical_id == v1["3.1"].logical_id
    assert v2["3.1"].prev_node_id == v1["3.1"].id
    assert v2["3.1"].id != v1["3.1"].id  # still its own row


def test_first_version_nodes_are_new(versions):
    v1, _ = versions
    assert all(n.status == "new" for n in v1.values())


class FakeNode:
    def __init__(self, section_id, heading):
        self.section_id = section_id
        self.heading = heading


def test_renumbered_section_matched_by_heading():
    old = [FakeNode("3.4", "Auto Shutoff")]
    new = [FakeNode("3.3", "Auto Shutoff")]
    assert match_nodes(old, new) == {new[0]: old[0]}


def test_duplicate_headings_are_not_guessed():
    # both renumbered, both called "Error Codes" -> can't tell which is which, so no match
    old = [FakeNode("4.2", "Error Codes"), FakeNode("7.1", "Error Codes")]
    new = [FakeNode("4.5", "Error Codes"), FakeNode("7.3", "Error Codes")]
    assert match_nodes(old, new) == {}


def test_change_kind():
    from app.staleness import change_kind

    assert change_kind("inflates in 40 mmHg steps", "inflates in 30 mmHg steps") == "numbers changed"
    assert change_kind("the device shows E3", "the monitor shows E3") == "wording only"
    # known gap: meaning flips but no digit changes
    assert change_kind("falls below 15%", "rises above 15%") == "wording only"
