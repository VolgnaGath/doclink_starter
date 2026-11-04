"""Durable experiment series, frozen runs and many-to-many physical provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def fk(name: str, target: str, nullable: bool = False, primary: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target), nullable=nullable, primary_key=primary)


def upgrade() -> None:
    op.create_table(
        "experiment_series",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        fk("corpus_version_id", "corpus_versions.id"),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        fk("job_id", "jobs.id", True),
        fk("selected_model_id", "model_configs.id", True),
        sa.Column("error", sa.String()),
        sa.Column("dataset", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued','running','done','error')"),
    )
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        fk("series_id", "experiment_series.id"),
        sa.Column("variant", sa.String(), nullable=False),
        fk("model_config_id", "model_configs.id", True),
        *(
            sa.Column(name, sa.String(), nullable=False)
            for name in ("method", "ablation", "status")
        ),
        *(sa.Column(name, JSONB(), nullable=False) for name in ("config", "snapshot", "report")),
        sa.Column("protocol_hash", sa.String()),
        sa.Column("reused_test", sa.Boolean(), nullable=False),
        *(
            sa.Column(name, sa.DateTime(timezone=True))
            for name in ("started_at", "finished_at", "test_started_at")
        ),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("error", sa.String()),
        sa.UniqueConstraint("series_id", "variant"),
        sa.CheckConstraint(
            "status IN ('queued','frozen','running','done','error',"
            "'NO_FEASIBLE_CONFIGURATION','BLOCKED')"
        ),
        sa.CheckConstraint("progress >= 0 AND total >= progress"),
    )
    op.create_index("ix_experiment_runs_series_id", "experiment_runs", ["series_id"])
    op.create_table(
        "experiment_metrics",
        fk("run_id", "experiment_runs.id", primary=True),
        *(sa.Column(name, sa.String(), primary_key=True) for name in ("split", "slice", "metric")),
        sa.Column("value", sa.Float()),
        sa.Column("numerator", sa.Integer()),
        sa.Column("denominator", sa.Integer()),
        sa.CheckConstraint("split IN ('tune','test')"),
    )
    op.create_table(
        "threshold_curves",
        fk("run_id", "experiment_runs.id", primary=True),
        sa.Column("index", sa.Integer(), primary_key=True),
        sa.Column("split", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("values", JSONB(), nullable=False),
        sa.CheckConstraint("split = 'tune' AND purpose = 'tuning'"),
    )
    op.create_table(
        "experiment_results",
        fk("run_id", "experiment_runs.id", primary=True),
        fk("document_id", "documents.id", primary=True),
        fk("result_id", "linking_results.id"),
        sa.Column("timing_id", sa.Integer(), sa.ForeignKey("stage_timings.id"), nullable=False),
        sa.Column("reused", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "experiment_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        fk("run_id", "experiment_runs.id"),
        fk("extraction_id", "extractions.id", True),
        fk("llm_call_id", "llm_calls.id", True),
        fk("embedding_id", "embeddings.id", True),
        sa.Column("timing_id", sa.Integer(), sa.ForeignKey("stage_timings.id")),
        sa.CheckConstraint("num_nonnulls(extraction_id,llm_call_id,embedding_id,timing_id) = 1"),
        *(
            sa.UniqueConstraint("run_id", name)
            for name in ("extraction_id", "llm_call_id", "embedding_id", "timing_id")
        ),
    )
    op.create_index("ix_experiment_artifacts_run_id", "experiment_artifacts", ["run_id"])


def downgrade() -> None:
    for table in (
        "experiment_artifacts",
        "experiment_results",
        "threshold_curves",
        "experiment_metrics",
        "experiment_runs",
        "experiment_series",
    ):
        op.drop_table(table)
