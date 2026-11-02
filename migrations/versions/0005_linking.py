"""Document outcomes and full candidate rankings, separate from confirmed links.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "linking_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("model_config_id", sa.Uuid(), sa.ForeignKey("model_configs.id")),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), unique=True),
        sa.Column("input_hash", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("method_version", sa.String(), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("input_snapshot", JSONB(), nullable=False),
        sa.Column("provenance", JSONB(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("reasons", JSONB(), nullable=False),
        sa.Column("recommendations", JSONB(), nullable=False),
        sa.Column("error", sa.String()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("outcome IN ('recommendation','review','none','error')"),
        sa.CheckConstraint("method IN ('rules','semantic','combined')"),
    )
    op.create_index("ix_linking_results_source_id", "linking_results", ["source_id"])
    op.create_index("ix_linking_results_input_hash", "linking_results", ["input_hash"])
    op.create_index(
        "uq_linking_success",
        "linking_results",
        ["input_hash"],
        unique=True,
        postgresql_where=sa.text("outcome <> 'error'"),
    )
    op.create_table(
        "link_suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("result_id", sa.Uuid(), sa.ForeignKey("linking_results.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("link_type", sa.String(), nullable=False),
        sa.Column("features", JSONB(), nullable=False),
        sa.Column("penalties", JSONB(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("source_evidence", JSONB(), nullable=False),
        sa.Column("target_evidence", JSONB(), nullable=False),
        sa.UniqueConstraint("result_id", "target_id"),
        sa.UniqueConstraint("result_id", "rank"),
        sa.CheckConstraint("rank > 0"),
        sa.CheckConstraint("decision IN ('recommendation','review','none')"),
        sa.CheckConstraint("link_type IN ('изменение','основание','исполнение')"),
    )
    op.create_index("ix_link_suggestions_result_id", "link_suggestions", ["result_id"])


def downgrade() -> None:
    op.drop_table("link_suggestions")
    op.drop_table("linking_results")
