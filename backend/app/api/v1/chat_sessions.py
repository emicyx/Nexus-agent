"""Chat sessions CRUD 路由"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.chat import (
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionRead,
    ChatSessionUpdate,
)
from app.services import chat_service

router = APIRouter()


@router.get("", response_model=list[ChatSessionRead])
async def list_sessions(
    crew_id: int | None = Query(default=None, description="按 crew_id 过滤"),
    session: AsyncSession = Depends(get_db),
):
    """列出 sessions，按 updated_at 倒序。crew_id 为空时列出全部。"""
    return await chat_service.list_sessions(session, crew_id=crew_id)


@router.get("/{session_id}", response_model=ChatSessionDetail)
async def get_session(session_id: int, session: AsyncSession = Depends(get_db)):
    """获取 session 详情含完整 messages。"""
    detail = await chat_service.get_session_detail(session, session_id)
    if detail is None:
        raise HTTPException(404, "Session not found")
    return detail


@router.post("", response_model=ChatSessionRead, status_code=201)
async def create_session(payload: ChatSessionCreate, session: AsyncSession = Depends(get_db)):
    """创建新 session（前端生成 session_uuid 传入）。"""
    s = await chat_service.create_session(session, payload)
    return {
        "id": s.id,
        "crew_id": s.crew_id,
        "session_uuid": s.session_uuid,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "message_count": 0,
        "last_message_at": None,
    }


@router.patch("/{session_id}", response_model=ChatSessionRead)
async def update_session(
    session_id: int,
    payload: ChatSessionUpdate,
    session: AsyncSession = Depends(get_db),
):
    """更新 session 标题。"""
    s = await chat_service.update_session_title(session, session_id, payload)
    if s is None:
        raise HTTPException(404, "Session not found")
    return {
        "id": s.id,
        "crew_id": s.crew_id,
        "session_uuid": s.session_uuid,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "message_count": len(s.messages) if s.messages else 0,
        "last_message_at": s.messages[-1].created_at if s.messages else None,
    }


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: int, session: AsyncSession = Depends(get_db)):
    """删除 session（级联删 messages）。"""
    ok = await chat_service.delete_session(session, session_id)
    if not ok:
        raise HTTPException(404, "Session not found")
    return None
