"""Durable operational configuration and upload pipelines."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("id = 1"),
    )
    op.create_table(
        "processing_pipelines",
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id")),
        sa.Column("error", sa.String()),
        sa.Column("result_id", sa.Uuid(), sa.ForeignKey("linking_results.id")),
    )
    op.create_index("ix_processing_pipelines_batch_id", "processing_pipelines", ["batch_id"])


def downgrade() -> None:
    op.drop_table("processing_pipelines")
    op.drop_table("operational_config")
