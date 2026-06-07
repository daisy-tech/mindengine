"""initial schema (M1): users + chat + 4-layer memory + correction + eval + traces

Includes:
- CREATE EXTENSION vector  (pgvector, decision D1)
- HNSW ANN index on episodic_memories.embedding (cosine)

Revision ID: 0001
Revises:
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.domain.llm import EMBEDDING_DIM

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100)),
        sa.Column("personality", sa.String(16), nullable=False, server_default="balanced"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_dev", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"])
    op.create_index("ix_conversations_user_created", "conversations", ["user_id", "created_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("conversation_id", sa.String(64), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta_json", postgresql.JSONB),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])

    op.create_table(
        "profiles",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("data_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source_message_id", sa.String(64)),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_events_user_id", "events", ["user_id"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_status", "events", ["status"])
    op.create_index("ix_events_user_occurred", "events", ["user_id", "occurred_at"])
    op.create_index("ix_events_user_status", "events", ["user_id", "status"])

    op.create_table(
        "relationships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("attributes_json", postgresql.JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("via", sa.String(36)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("via IS NULL OR via <> id", name="no_self_loop"),
    )
    op.create_index("ix_relationships_user_id", "relationships", ["user_id"])
    op.create_index("ix_relationships_user_name", "relationships", ["user_id", "name"])

    op.create_table(
        "episodic_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source_message_id", sa.String(64)),
        sa.Column("source", sa.String(32)),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_episodic_memories_user_id", "episodic_memories", ["user_id"])
    op.create_index("ix_episodic_memories_status", "episodic_memories", ["status"])
    op.create_index("ix_episodic_memories_created_at", "episodic_memories", ["created_at"])
    op.create_index("ix_episodic_user_status", "episodic_memories", ["user_id", "status"])
    # HNSW ANN index (pgvector ≥ 0.5).
    op.execute(
        "CREATE INDEX ix_episodic_embedding_hnsw ON episodic_memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "memory_deprecations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("ref_id", sa.String(100), nullable=False),
        sa.Column("original_text", sa.Text()),
        sa.Column("new_text", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column("correction_conversation_id", sa.String(64)),
        sa.Column("correction_turn_id", sa.String(64)),
        sa.Column("llm_confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("restored_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_deprecations_user_id", "memory_deprecations", ["user_id"])
    op.create_index("ix_deprecations_ref_id", "memory_deprecations", ["ref_id"])
    op.create_index("ix_deprecations_deprecated_at", "memory_deprecations", ["deprecated_at"])
    op.create_index("ix_deprecations_user_source", "memory_deprecations", ["user_id", "source"])
    op.create_index("ix_deprecations_user_at", "memory_deprecations", ["user_id", "deprecated_at"])

    op.create_table(
        "banned_entities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("entity", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "entity", name="uq_banned_user_entity"),
    )
    op.create_index("ix_banned_user_id", "banned_entities", ["user_id"])
    op.create_index("ix_banned_entity", "banned_entities", ["entity"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_type", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pass_rate", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("report_json", postgresql.JSONB),
        sa.Column("created_by", sa.String(36)),
    )

    op.create_table(
        "llm_traces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("extra", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_llm_traces_trace_id", "llm_traces", ["trace_id"])
    op.create_index("ix_llm_traces_user_id", "llm_traces", ["user_id"])
    op.create_index("ix_llm_traces_created_at", "llm_traces", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_traces")
    op.drop_table("eval_runs")
    op.drop_table("banned_entities")
    op.drop_table("memory_deprecations")
    op.execute("DROP INDEX IF EXISTS ix_episodic_embedding_hnsw")
    op.drop_table("episodic_memories")
    op.drop_table("relationships")
    op.drop_table("events")
    op.drop_table("profiles")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
    # Keep the vector extension; it's expensive to drop+recreate.
