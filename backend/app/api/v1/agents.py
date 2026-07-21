"""Agent 配置 CRUD 路由"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.agent import AgentCreate, AgentRead, AgentUpdate
from app.services import agent_service

router = APIRouter()


@router.get("", response_model=list[AgentRead])
async def list_agents(session: AsyncSession = Depends(get_db)):
    return await agent_service.list_agents(session)


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(agent_id: int, session: AsyncSession = Depends(get_db)):
    agent = await agent_service.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.post("", response_model=AgentRead, status_code=201)
async def create_agent(payload: AgentCreate, session: AsyncSession = Depends(get_db)):
    return await agent_service.create_agent(session, payload)


@router.put("/{agent_id}", response_model=AgentRead)
async def update_agent(agent_id: int, payload: AgentUpdate, session: AsyncSession = Depends(get_db)):
    agent = await agent_service.update_agent(session, agent_id, payload)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: int, session: AsyncSession = Depends(get_db)):
    ok = await agent_service.delete_agent(session, agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")


@router.post("/{agent_id}/tools", response_model=AgentRead)
async def set_agent_tools(
    agent_id: int, tool_ids: list[int], session: AsyncSession = Depends(get_db)
):
    """整体替换 Agent 的工具集。请求体为 tool_id 列表。"""
    agent = await agent_service.set_agent_tools(session, agent_id, tool_ids)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


@router.post("/{agent_id}/skills", response_model=AgentRead)
async def set_agent_skills(
    agent_id: int, skill_ids: list[int], session: AsyncSession = Depends(get_db)
):
    """整体替换 Agent 的 skills 集。请求体为 skill_id 列表。"""
    agent = await agent_service.set_agent_skills(session, agent_id, skill_ids)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent
