"""Cross-version extraction provenance and explicitly preserved terminal errors."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("extractions", sa.Column("source_extraction_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_extraction_source", "extractions", "extractions", ["source_extraction_id"], ["id"]
    )
    op.add_column("extractions", sa.Column("input_hash", sa.String()))
    op.add_column(
        "extractions",
        sa.Column("preserved_error", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("extractions", "preserved_error")
    op.drop_column("extractions", "input_hash")
    op.drop_constraint("fk_extraction_source", "extractions", type_="foreignkey")
    op.drop_column("extractions", "source_extraction_id")
