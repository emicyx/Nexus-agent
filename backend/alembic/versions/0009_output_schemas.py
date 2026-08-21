"""output_schemas: 补齐迁移链与模型的漂移

背景：output_schema_configs 表与 task_configs.output_schema_id 在功能上线时
只改了模型、没进迁移链，一直被 create_all 掩盖（create_all 只补缺失的表、
不会给已存在的表加列）。干净库走 alembic upgrade head 时暴露：
task_configs.output_schema_id does not exist。

Revision ID: 0009_output_schemas
Revises: 0008_chat_session_summaries
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009_output_schemas"
down_revision: Union[str, None] = "0008_chat_session_summaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "output_schema_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("schema_fields", JSONB(), nullable=False),
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
    op.create_index(
        "ix_output_schema_configs_name",
        "output_schema_configs",
        ["name"],
        unique=True,
    )
    op.add_column(
        "task_configs",
        sa.Column("output_schema_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_task_configs_output_schema",
        "task_configs",
        "output_schema_configs",
        ["output_schema_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_task_configs_output_schema", "task_configs", type_="foreignkey"
    )
    op.drop_column("task_configs", "output_schema_id")
    op.drop_index("ix_output_schema_configs_name", table_name="output_schema_configs")
    op.drop_table("output_schema_configs")
