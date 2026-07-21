"""Tool 配置 CRUD 路由"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.crews.tool_registry import TOOL_OPTIONS
from app.db.session import get_db
from app.schemas.tool import ToolCreate, ToolRead, ToolUpdate
from app.services import tool_service

router = APIRouter()


@router.get("", response_model=list[ToolRead])
async def list_tools(session: AsyncSession = Depends(get_db)):
    return await tool_service.list_tools(session)


@router.get("/options")
async def tool_options():
    """返回可选的 tool_key（供前端下拉）。"""
    return {"options": TOOL_OPTIONS}


@router.get("/{tool_id}", response_model=ToolRead)
async def get_tool(tool_id: int, session: AsyncSession = Depends(get_db)):
    tool = await tool_service.get_tool(session, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    return tool


@router.post("", response_model=ToolRead, status_code=201)
async def create_tool(payload: ToolCreate, session: AsyncSession = Depends(get_db)):
    return await tool_service.create_tool(session, payload)


@router.put("/{tool_id}", response_model=ToolRead)
async def update_tool(tool_id: int, payload: ToolUpdate, session: AsyncSession = Depends(get_db)):
    tool = await tool_service.update_tool(session, tool_id, payload)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    return tool


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(tool_id: int, session: AsyncSession = Depends(get_db)):
    ok = await tool_service.delete_tool(session, tool_id)
    if not ok:
        raise HTTPException(404, "Tool not found")
