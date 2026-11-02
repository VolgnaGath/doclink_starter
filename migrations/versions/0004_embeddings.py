"""Content-addressed vectors and document provenance.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("input_hash", sa.String(), nullable=False),
        sa.Column("text_hash", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("revision", sa.String(), nullable=False),
        sa.Column("snapshot", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("vector", Vector(1024), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("tokens > 0 AND chunks > 0 AND duration_ms >= 0"),
        sa.CheckConstraint("NOT truncated"),
        sa.CheckConstraint("abs(vector_norm(vector) - 1) < 0.0001"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_hash"),
    )
    op.create_index(
        "ix_embeddings_vector_hnsw",
        "embeddings",
        ["vector"],
        postgresql_using="hnsw",
        postgresql_ops={"vector": "vector_cosine_ops"},
    )
    op.create_table(
        "document_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_id", sa.Uuid(), nullable=False),
        sa.Column("original_text_hash", sa.String(), nullable=False),
        sa.Column("original_chars", sa.Integer(), nullable=False),
        sa.Column("prepared_chars", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["embedding_id"], ["embeddings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "embedding_id", "original_text_hash"),
    )
    op.create_index("ix_document_embeddings_document_id", "document_embeddings", ["document_id"])
    op.create_index("ix_document_embeddings_embedding_id", "document_embeddings", ["embedding_id"])


def downgrade() -> None:
    op.drop_table("document_embeddings")
    op.drop_table("embeddings")
