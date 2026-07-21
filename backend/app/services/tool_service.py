"""Tool 配置 CRUD 服务"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolConfig
from app.schemas.tool import ToolCreate, ToolUpdate


async def list_tools(session: AsyncSession) -> list[ToolConfig]:
    stmt = select(ToolConfig).order_by(ToolConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_tool(session: AsyncSession, tool_id: int) -> ToolConfig | None:
    return await session.get(ToolConfig, tool_id)


async def create_tool(session: AsyncSession, payload: ToolCreate) -> ToolConfig:
    tool = ToolConfig(
        name=payload.name,
        tool_key=payload.tool_key,
        description=payload.description,
        config_json=payload.config_json,
    )
    session.add(tool)
    await session.flush()
    await session.commit()
    await session.refresh(tool)
    return tool


async def update_tool(session: AsyncSession, tool_id: int, payload: ToolUpdate) -> ToolConfig | None:
    tool = await session.get(ToolConfig, tool_id)
    if tool is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(tool, k, v)
    await session.commit()
    await session.refresh(tool)
    return tool


async def delete_tool(session: AsyncSession, tool_id: int) -> bool:
    tool = await session.get(ToolConfig, tool_id)
    if tool is None:
        return False
    await session.delete(tool)
    await session.commit()
    return True
