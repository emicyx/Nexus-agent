"""hybrid search: tsv generated column + GIN index

Revision ID: 0005_hybrid_search
Revises: 0004_skills
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005_hybrid_search"
down_revision: Union[str, None] = "0004_skills"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tsv：由 content 用 chinese 配置生成的 STORED 生成列
    # to_tsvector('chinese', content) 是 immutable，可用于生成列
    op.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('chinese', content)) STORED;"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_tsv "
        "ON document_chunks USING gin (tsv);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_tsv;")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS tsv;")
