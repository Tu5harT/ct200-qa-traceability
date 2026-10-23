import hashlib
import os

from app.db import Document, Node, Version
from app.parser import parse_pdf
from app.versioning import link_versions


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ingest(session, path, doc_name="ct200_manual"):
    doc = session.query(Document).filter_by(name=doc_name).first()
    if doc is None:
        doc = Document(name=doc_name)
        session.add(doc)
        session.flush()

    # same file again -> give back the version we already have
    fhash = file_hash(path)
    for v in doc.versions:
        if v.file_hash == fhash:
            return v

    parsed = parse_pdf(path)
    previous = doc.versions[-1] if doc.versions else None

    version = Version(
        document=doc,
        number=len(doc.versions) + 1,
        filename=os.path.basename(path),
        file_hash=fhash,
        title=parsed["title"],
        warnings=parsed["warnings"],
    )
    session.add(version)

    # parser gives parents as section ids ("2.1"), the db needs row ids.
    # parents always come before children in the list, so one pass is enough
    by_section = {}
    new_nodes = []
    for n in parsed["nodes"]:
        node = Node(
            version=version,
            section_id=n["id"],
            number=n["number"],
            heading=n["heading"],
            level=n["level"],
            parent=by_section.get(n["parent"]),
            body=n["body"],
            hash=n["hash"],
            page=n["page"],
            order=n["order"],
        )
        session.add(node)
        by_section[n["id"]] = node
        new_nodes.append(node)

    link_versions(previous.nodes if previous else [], new_nodes, version.number)
    session.commit()
    return version


if __name__ == "__main__":
    import sys

    from app.db import SessionLocal, create_tables

    create_tables()
    with SessionLocal() as session:
        v = ingest(session, sys.argv[1])
        print(f"{v.filename} -> version {v.number}, {len(v.nodes)} nodes")
        for w in v.warnings:
            print(" warning:", w)
        for n in v.nodes:
            if n.status not in ("new", "unchanged"):
                print(f" {n.status}: {n.section_id} {n.heading}")
