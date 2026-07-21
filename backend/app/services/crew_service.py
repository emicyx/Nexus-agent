"""Crew 配置 CRUD 服务（含 agents/tasks 关联）"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crews.factory import invalidate_default_crew_id_cache
from app.models import AgentConfig, CrewConfig, TaskConfig
from app.models.association import CrewAgent
from app.schemas.crew import CrewCreate, CrewUpdate
from app.schemas.task import TaskCreate, TaskUpdate


async def list_crews(session: AsyncSession) -> list[CrewConfig]:
    stmt = select(CrewConfig).order_by(CrewConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_crew(session: AsyncSession, crew_id: int) -> CrewConfig | None:
    stmt = select(CrewConfig).where(CrewConfig.id == crew_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _resolve_agents(session: AsyncSession, agent_ids: list[int]) -> list[AgentConfig]:
    if not agent_ids:
        return []
    stmt = select(AgentConfig).where(AgentConfig.id.in_(agent_ids))
    return list((await session.execute(stmt)).scalars().all())


async def create_crew(session: AsyncSession, payload: CrewCreate) -> CrewConfig:
    agents = await _resolve_agents(session, payload.agent_ids)
    crew = CrewConfig(
        name=payload.name,
        description=payload.description,
        process_type=payload.process_type,
        manager_agent_id=payload.manager_agent_id,
    )
    session.add(crew)
    await session.flush()
    # 显式设置 position（不用 agents= 关系，避免 M2M 双重插入）
    for idx, agent in enumerate(agents):
        await session.execute(
            CrewAgent.insert().values(crew_id=crew.id, agent_id=agent.id, position=idx)
        )
    # 创建 tasks
    for t in payload.tasks:
        task = TaskConfig(
            crew_id=crew.id,
            agent_id=t.agent_id,
            name=t.name,
            description=t.description,
            expected_output=t.expected_output,
            position=t.position,
            context_task_ids=t.context_task_ids,
            output_schema_id=t.output_schema_id,
        )
        session.add(task)
    await session.commit()
    # 用 select 重新加载（带 selectin 关系）
    stmt = select(CrewConfig).where(CrewConfig.id == crew.id)
    result = (await session.execute(stmt)).scalar_one_or_none()
    invalidate_default_crew_id_cache()
    return result


async def update_crew(session: AsyncSession, crew_id: int, payload: CrewUpdate) -> CrewConfig | None:
    crew = await session.get(CrewConfig, crew_id)
    if crew is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    agent_ids = data.pop("agent_ids", None)
    for k, v in data.items():
        setattr(crew, k, v)
    if agent_ids is not None:
        # 替换 agent 关联 + 重置 position
        await session.execute(CrewAgent.delete().where(CrewAgent.c.crew_id == crew.id))
        agents = await _resolve_agents(session, agent_ids)
        for idx, agent in enumerate(agents):
            await session.execute(
                CrewAgent.insert().values(crew_id=crew.id, agent_id=agent.id, position=idx)
            )
    await session.commit()
    # 用 select 重新加载（带 selectin 关系），避免 async lazy-load 报错
    stmt = select(CrewConfig).where(CrewConfig.id == crew_id)
    result = (await session.execute(stmt)).scalar_one_or_none()
    invalidate_default_crew_id_cache()
    return result


async def delete_crew(session: AsyncSession, crew_id: int) -> bool:
    crew = await session.get(CrewConfig, crew_id)
    if crew is None:
        return False
    await session.delete(crew)
    await session.commit()
    invalidate_default_crew_id_cache()
    return True


# ---------- Task 子资源 ----------


async def list_tasks(session: AsyncSession, crew_id: int) -> list[TaskConfig]:
    stmt = select(TaskConfig).where(TaskConfig.crew_id == crew_id).order_by(TaskConfig.position)
    return list((await session.execute(stmt)).scalars().all())


async def create_task(session: AsyncSession, crew_id: int, payload: TaskCreate) -> TaskConfig | None:
    crew = await session.get(CrewConfig, crew_id)
    if crew is None:
        return None
    task = TaskConfig(
        crew_id=crew_id,
        agent_id=payload.agent_id,
        name=payload.name,
        description=payload.description,
        expected_output=payload.expected_output,
        position=payload.position,
        context_task_ids=payload.context_task_ids,
        output_schema_id=payload.output_schema_id,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def update_task(session: AsyncSession, task_id: int, payload: TaskUpdate) -> TaskConfig | None:
    task = await session.get(TaskConfig, task_id)
    if task is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(task, k, v)
    await session.commit()
    await session.refresh(task)
    return task


async def delete_task(session: AsyncSession, task_id: int) -> bool:
    task = await session.get(TaskConfig, task_id)
    if task is None:
        return False
    await session.delete(task)
    await session.commit()
    return True
