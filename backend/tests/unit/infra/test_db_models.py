"""Smoke checks on SQLAlchemy model registry — no DB connection required."""

from __future__ import annotations

from app.domain.llm import EMBEDDING_DIM
from app.infra.db.base import Base
from app.infra.db.models import BannedEntityRow


def test_naming_convention_present() -> None:
    nc = Base.metadata.naming_convention
    assert nc["pk"] == "pk_%(table_name)s"
    assert nc["uq"] == "uq_%(table_name)s_%(column_0_N_name)s"


def test_eleven_tables_registered() -> None:
    expected = {
        "users",
        "conversations",
        "messages",
        "profiles",
        "events",
        "relationships",
        "episodic_memories",
        "memory_deprecations",
        "banned_entities",
        "eval_runs",
        "llm_traces",
    }
    assert expected.issubset(set(Base.metadata.tables.keys()))


def test_episodic_embedding_dim_matches_constant() -> None:
    table = Base.metadata.tables["episodic_memories"]
    embedding_col = table.c["embedding"]
    assert embedding_col.type.dim == EMBEDDING_DIM


def test_relationships_self_loop_check() -> None:
    # Naming convention rewrites "no_self_loop" -> "ck_relationships_no_self_loop".
    table = Base.metadata.tables["relationships"]
    constraints = {c.name for c in table.constraints if c.name}
    assert any(n.endswith("no_self_loop") for n in constraints), constraints


def test_banned_entities_unique_user_entity() -> None:
    table = Base.metadata.tables["banned_entities"]
    constraints = {c.name for c in table.constraints if c.name}
    assert "uq_banned_user_entity" in constraints


def test_messages_composite_index_present() -> None:
    # Doc 02 invariant 16: queries filter by user_id / conversation_id.
    # The composite (conversation_id, created_at) is what chat reads use.
    table = Base.metadata.tables["messages"]
    index_names = {ix.name for ix in table.indexes}
    assert "ix_messages_conversation_created" in index_names


def test_banned_entity_row_can_be_built() -> None:
    row = BannedEntityRow(user_id="u1", entity="岳西")
    assert row.user_id == "u1"
