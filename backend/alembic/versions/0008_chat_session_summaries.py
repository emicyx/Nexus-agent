"""chat_session_summaries: per-session rolling summary for STM layer 1

补齐迁移：模型 backend/app/models/chat_session_summary.py 早已存在，
此前仅靠 create_all 建表，迁移链缺失（生产切 Alembic 时会断链）。

Revision ID: 0008_chat_session_summaries
Revises: 0007_user_memories
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_chat_session_summaries"
down_revision: Union[str, None] = "0007_user_memories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_session_summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "session_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_message_id", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 模型侧 session_id 同时 unique=True + index=True（SQLAlchemy 生成唯一索引 ix_*）
    op.create_index(
        "ix_chat_session_summaries_session_id",
        "chat_session_summaries",
        ["session_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_session_summaries_session_id", table_name="chat_session_summaries"
    )
    op.drop_table("chat_session_summaries")
