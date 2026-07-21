"""用户长期记忆管理端点（GET / DELETE）"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import UserMemory

router = APIRouter()


@router.get("")
async def list_memories(
    crew_id: int | None = Query(default=None, description="按 crew_id 过滤"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """列出用户记忆，按 crew_id 过滤，按 created_at 倒序。"""
    stmt = select(UserMemory).order_by(UserMemory.created_at.desc()).limit(limit)
    if crew_id is not None:
        stmt = stmt.where(UserMemory.crew_id == crew_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "crew_id": m.crew_id,
            "memory_type": m.memory_type,
            "content": m.content,
            "source_session_id": m.source_session_id,
            "use_count": m.use_count,
            "last_used_at": m.last_used_at,
            "created_at": m.created_at,
        }
        for m in rows
    ]


@router.delete("/{memory_id}")
async def delete_memory(memory_id: int, db: AsyncSession = Depends(get_db)):
    """删除单条用户记忆。"""
    m = await db.get(UserMemory, memory_id)
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    await db.delete(m)
    await db.commit()
    return None
