"""关联表：AgentTool、CrewAgent、AgentSkill"""
from sqlalchemy import Column, ForeignKey, Integer, Table

from app.models.base import Base

# Agent ↔ Tool 多对多
AgentTool = Table(
    "agent_tools",
    Base.metadata,
    Column("agent_id", Integer, ForeignKey("agent_configs.id", ondelete="CASCADE"), primary_key=True),
    Column("tool_id", Integer, ForeignKey("tool_configs.id", ondelete="CASCADE"), primary_key=True),
)

# Crew ↔ Agent 多对多（带 position 排序）
CrewAgent = Table(
    "crew_agents",
    Base.metadata,
    Column("crew_id", Integer, ForeignKey("crew_configs.id", ondelete="CASCADE"), primary_key=True),
    Column("agent_id", Integer, ForeignKey("agent_configs.id", ondelete="CASCADE"), primary_key=True),
    Column("position", Integer, nullable=False, default=0),  # 在 Crew 中的顺序
)

# Agent ↔ Skill 多对多
AgentSkill = Table(
    "agent_skills",
    Base.metadata,
    Column("agent_id", Integer, ForeignKey("agent_configs.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", Integer, ForeignKey("skill_configs.id", ondelete="CASCADE"), primary_key=True),
)
