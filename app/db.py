import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Table, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DB_URL = os.getenv("DB_URL", "sqlite:///ct200.db")

engine = create_engine(DB_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    versions = relationship("Version", back_populates="document", order_by="Version.number")


class Version(Base):
    __tablename__ = "versions"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    number = Column(Integer, nullable=False)  # 1, 2, 3 ... per document
    filename = Column(String)
    file_hash = Column(String)  # so ingesting the same pdf twice doesn't make a new version
    title = Column(String)
    warnings = Column(JSON, default=list)  # parser warnings, kept so people can see them later
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="versions")
    nodes = relationship("Node", back_populates="version", order_by="Node.order")


class Node(Base):
    __tablename__ = "nodes"

    # every node row belongs to exactly one version, so this id points to
    # "section X as it was in version Y". selections will rely on that
    id = Column(Integer, primary_key=True)
    version_id = Column(Integer, ForeignKey("versions.id"), nullable=False)
    section_id = Column(String, nullable=False)  # "4.2", or "4.2-2" if the number repeats
    number = Column(String)
    heading = Column(String)
    level = Column(Integer)
    parent_id = Column(Integer, ForeignKey("nodes.id"))
    body = Column(Text)
    hash = Column(String)
    page = Column(Integer)
    order = Column(Integer)

    # same section across versions shares a logical_id, e.g. "v1:3.2"
    logical_id = Column(String, index=True)
    status = Column(String)  # new / unchanged / changed / added
    prev_node_id = Column(Integer, ForeignKey("nodes.id"))  # matching node in the previous version

    version = relationship("Version", back_populates="nodes")
    parent = relationship("Node", remote_side=[id], back_populates="children", foreign_keys=[parent_id])
    children = relationship("Node", back_populates="parent", order_by="Node.order", foreign_keys=[parent_id])


selection_nodes = Table(
    "selection_nodes",
    Base.metadata,
    Column("selection_id", Integer, ForeignKey("selections.id"), primary_key=True),
    Column("node_id", Integer, ForeignKey("nodes.id"), primary_key=True),
)


class Selection(Base):
    __tablename__ = "selections"

    # points at node rows, and a node row is one section in one version.
    # re-ingesting never edits old rows, so a selection always gives back the text it was made with
    id = Column(Integer, primary_key=True)
    # mongo links to this, not to id. if ct200.db gets reset the ids start at 1 again,
    # and a new selection 1 would pick up the old selection 1's test cases
    uid = Column(String, unique=True, nullable=False, default=lambda: uuid.uuid4().hex)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    nodes = relationship("Node", secondary=selection_nodes, order_by="Node.order")


def create_tables():
    Base.metadata.create_all(engine)
