"""Agent 配置 CRUD 服务（含工具+技能挂载）"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentConfig, SkillConfig, ToolConfig
from app.schemas.agent import AgentCreate, AgentUpdate


async def list_agents(session: AsyncSession) -> list[AgentConfig]:
    stmt = select(AgentConfig).order_by(AgentConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_agent(session: AsyncSession, agent_id: int) -> AgentConfig | None:
    return await session.get(AgentConfig, agent_id)


async def _resolve_tools(session: AsyncSession, tool_ids: list[int]) -> list[ToolConfig]:
    if not tool_ids:
        return []
    stmt = select(ToolConfig).where(ToolConfig.id.in_(tool_ids))
    return list((await session.execute(stmt)).scalars().all())


async def _resolve_skills(session: AsyncSession, skill_ids: list[int]) -> list[SkillConfig]:
    if not skill_ids:
        return []
    stmt = select(SkillConfig).where(SkillConfig.id.in_(skill_ids))
    return list((await session.execute(stmt)).scalars().all())


async def create_agent(session: AsyncSession, payload: AgentCreate) -> AgentConfig:
    tools = await _resolve_tools(session, payload.tool_ids)
    skills = await _resolve_skills(session, payload.skill_ids)
    agent = AgentConfig(
        name=payload.name,
        role=payload.role,
        goal=payload.goal,
        backstory=payload.backstory,
        llm_model=payload.llm_model,
        temperature=payload.temperature,
        max_iter=payload.max_iter,
        memory=payload.memory,
        tools=tools,
        skills=skills,
    )
    session.add(agent)
    await session.flush()
    await session.commit()
    await session.refresh(agent)
    return agent


async def update_agent(session: AsyncSession, agent_id: int, payload: AgentUpdate) -> AgentConfig | None:
    agent = await session.get(AgentConfig, agent_id)
    if agent is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    # tool_ids 和 skill_ids 单独处理（替换集合）
    tool_ids = data.pop("tool_ids", None)
    skill_ids = data.pop("skill_ids", None)
    for k, v in data.items():
        setattr(agent, k, v)
    if tool_ids is not None:
        agent.tools = await _resolve_tools(session, tool_ids)
    if skill_ids is not None:
        agent.skills = await _resolve_skills(session, skill_ids)
    await session.commit()
    await session.refresh(agent)
    return agent


async def delete_agent(session: AsyncSession, agent_id: int) -> bool:
    agent = await session.get(AgentConfig, agent_id)
    if agent is None:
        return False
    await session.delete(agent)
    await session.commit()
    return True


async def set_agent_tools(session: AsyncSession, agent_id: int, tool_ids: list[int]) -> AgentConfig | None:
    """整体替换 Agent 的工具集。"""
    agent = await session.get(AgentConfig, agent_id)
    if agent is None:
        return None
    agent.tools = await _resolve_tools(session, tool_ids)
    await session.commit()
    await session.refresh(agent)
    return agent


async def set_agent_skills(session: AsyncSession, agent_id: int, skill_ids: list[int]) -> AgentConfig | None:
    """整体替换 Agent 的 skills 集。"""
    agent = await session.get(AgentConfig, agent_id)
    if agent is None:
        return None
    agent.skills = await _resolve_skills(session, skill_ids)
    await session.commit()
    await session.refresh(agent)
    return agent
