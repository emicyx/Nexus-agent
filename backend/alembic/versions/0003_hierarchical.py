"""hierarchical: crew_configs.manager_agent_id + task_configs.agent_id nullable

Revision ID: 0003_hierarchical
Revises: 0002_documents
Create Date: 2026-07-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_hierarchical"
down_revision: Union[str, None] = "0002_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # crew_configs: add manager_agent_id (FK -> agent_configs.id, SET NULL on delete)
    op.add_column(
        "crew_configs",
        sa.Column("manager_agent_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_crew_manager_agent",
        "crew_configs",
        "agent_configs",
        ["manager_agent_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # task_configs: agent_id nullable (hierarchical tasks may have no pre-assigned agent)
    op.alter_column(
        "task_configs",
        "agent_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # Revert task_configs.agent_id to NOT NULL (fails if NULL rows exist)
    op.alter_column(
        "task_configs",
        "agent_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    # Drop FK then column on crew_configs
    op.drop_constraint("fk_crew_manager_agent", "crew_configs", type_="foreignkey")
    op.drop_column("crew_configs", "manager_agent_id")
