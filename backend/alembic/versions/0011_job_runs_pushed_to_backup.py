"""job_runs.pushed_to_backup：钉钉告警备推落账（v2 S3'，纯新增列）

Revision ID: 0011_job_runs_pushed_to_backup
Revises: 0010_jobs
Create Date: 2026-09-17

注：线上库此前一直走 create_all（无 alembic_version 表），本次为首次采纳迁移链——
先 `alembic stamp 0010_jobs` 再 `alembic upgrade head`（部署步骤见上线手册 §十六）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_job_runs_pushed_to_backup"
down_revision: Union[str, None] = "0010_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_runs", sa.Column("pushed_to_backup", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("job_runs", "pushed_to_backup")
