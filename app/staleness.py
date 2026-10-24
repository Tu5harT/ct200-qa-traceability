import re

from app.db import Node
from app.versioning import word_diff

NUMBER = re.compile(r"\d+(?:\.\d+)?")


def change_kind(old, new):
    # shortcut: only looks at digits. "below 15%" -> "above 15%" or "two" -> "three" counts as wording only.
    # good enough to sort what to review first, a reviewer still has to read the diff
    if sorted(NUMBER.findall(old)) != sorted(NUMBER.findall(new)):
        return "numbers changed"
    return "wording only"


def check_source(db, source, latest):
    old = db.get(Node, source["node_id"])
    now = db.query(Node).filter_by(version_id=latest.id, logical_id=source["logical_id"]).first()

    if old is None:
        # sqlite was reset but mongo still has this generation, we can't tell anything
        return {"status": "unknown", "reason": "source text is no longer in the database"}
    if now is None:
        return {"status": "removed"}
    if now.hash == source["hash"]:
        return {"status": "unchanged", "latest_node_id": now.id}
    return {
        "status": "changed",
        "kind": change_kind(old.body, now.body),
        "latest_node_id": now.id,
        "diff": word_diff(old.body, now.body),
    }


def check_generation(db, doc, latest):
    """Adds staleness info to a stored generation, compared with the latest version."""
    sources = []
    new_sections = []
    known = {s["logical_id"] for s in doc["sources"]}

    for source in doc["sources"]:
        result = {"section_id": source["section_id"], **check_source(db, source, latest)}
        sources.append(result)

        # a section added under something we generated from isn't covered by the tests
        if "latest_node_id" in result:
            for child in db.get(Node, result["latest_node_id"]).children:
                if child.logical_id not in known:
                    new_sections.append(child.section_id)

    not_fresh = {s["section_id"] for s in sources if s["status"] != "unchanged"}
    for case in doc["test_cases"]:
        case["stale_because"] = [s for s in case["source_sections"] if s in not_fresh]
        case["stale"] = bool(case["stale_because"])

    doc["checked_against_version"] = latest.number
    doc["stale"] = bool(not_fresh or new_sections)
    doc["source_status"] = sources
    doc["new_sections"] = new_sections
    return doc
