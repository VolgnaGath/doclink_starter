"""Isolated working sets and explicit document/result provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "working_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "corpus_version_id", sa.Uuid(), sa.ForeignKey("corpus_versions.id"), nullable=False
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("baseline", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "working_copies",
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), primary_key=True),
        sa.Column("working_set_id", sa.Uuid(), sa.ForeignKey("working_sets.id"), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("source_result_id", sa.Uuid(), sa.ForeignKey("linking_results.id")),
        sa.Column("source_result_current", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("working_set_id", "source_document_id"),
    )
    op.create_index("ix_working_copies_working_set_id", "working_copies", ["working_set_id"])


def downgrade() -> None:
    op.drop_table("working_copies")
    op.drop_table("working_sets")
