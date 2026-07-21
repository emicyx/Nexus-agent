"""skills: skill_configs + agent_skills

Revision ID: 0004_skills
Revises: 0003_hierarchical
Create Date: 2026-07-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_skills"
down_revision: Union[str, None] = "0003_hierarchical"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("skill_key", sa.String(64), nullable=True),
        sa.Column("config_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_skill_configs_name", "skill_configs", ["name"])

    op.create_table(
        "agent_skills",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_configs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skill_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "skill_id"),
    )


def downgrade() -> None:
    op.drop_table("agent_skills")
    op.drop_index("ix_skill_configs_name", table_name="skill_configs")
    op.drop_table("skill_configs")
