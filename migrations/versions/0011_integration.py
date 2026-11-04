"""Durable external inbox and transactional delivery outbox."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def timestamp(name: str, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.create_table(
        "integration_state",
        sa.Column("source", sa.String(), primary_key=True),
        timestamp("watermark", True),
        timestamp("next_poll_at"),
        timestamp("last_poll_at", True),
        sa.Column("error", sa.String()),
    )
    op.create_table(
        "integration_inbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.Uuid(), nullable=False),
        sa.Column("metadata_snapshot", JSONB(), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        timestamp("run_after", default=True),
        sa.Column("error", sa.String()),
        sa.UniqueConstraint("source", "external_id"),
        sa.CheckConstraint("status IN ('pending','done','error','conflict')"),
    )
    op.create_table(
        "integration_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("link_id", sa.Uuid(), sa.ForeignKey("links.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column(
            "review_action_id", sa.Uuid(), sa.ForeignKey("review_actions.id"), nullable=False
        ),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        timestamp("created_at", default=True),
        timestamp("run_after", default=True),
        timestamp("delivered_at", True),
        sa.Column("error", sa.String()),
        sa.UniqueConstraint("link_id", "operation"),
        sa.CheckConstraint("operation IN ('create','remove')"),
        sa.CheckConstraint("status IN ('pending','done','error','blocked')"),
    )
    op.create_table(
        "integration_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("reference", sa.String(), nullable=False),
        timestamp("started_at"),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("status_code", sa.Integer()),
        sa.Column("error", sa.String()),
    )


def downgrade() -> None:
    for name in (
        "integration_attempts",
        "integration_events",
        "integration_inbox",
        "integration_state",
    ):
        op.drop_table(name)
