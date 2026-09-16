"""jobs + job_runs：定时任务与推送（v2 S2，纯新增）

jobs：调度配置（trigger/crew/输入模板/推送策略/熔断参数）
job_runs：执行留痕（status/tokens/结果快照/pushed_to——eval 零外发断言锚点）

Revision ID: 0010_jobs
Revises: 0009_output_schemas
Create Date: 2026-09-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010_jobs"
down_revision: Union[str, None] = "0009_output_schemas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trigger_type", sa.String(length=16), nullable=False),
        sa.Column("trigger_config", JSONB(), nullable=False),
        sa.Column(
            "crew_id",
            sa.Integer(),
            sa.ForeignKey("crew_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_template", sa.Text(), nullable=False),
        sa.Column("output_config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("cost_cap_tokens", sa.Integer(), nullable=True),
        sa.Column("max_consecutive_failures", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_name", "jobs", ["name"], unique=True)
    op.create_index("ix_jobs_crew_id", "jobs", ["crew_id"])

    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_summary", JSONB(), nullable=True),
        sa.Column("pushed_to", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_runs_job_id", "job_runs", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_job_id", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_index("ix_jobs_crew_id", table_name="jobs")
    op.drop_index("ix_jobs_name", table_name="jobs")
    op.drop_table("jobs")
