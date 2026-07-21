"""Crew 配置 CRUD 路由（含 Task 子资源）"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.crew import CrewCreate, CrewRead, CrewUpdate
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate
from app.services import crew_service

router = APIRouter()


@router.get("", response_model=list[CrewRead])
async def list_crews(session: AsyncSession = Depends(get_db)):
    return await crew_service.list_crews(session)


@router.get("/{crew_id}", response_model=CrewRead)
async def get_crew(crew_id: int, session: AsyncSession = Depends(get_db)):
    crew = await crew_service.get_crew(session, crew_id)
    if crew is None:
        raise HTTPException(404, "Crew not found")
    return crew


@router.post("", response_model=CrewRead, status_code=201)
async def create_crew(payload: CrewCreate, session: AsyncSession = Depends(get_db)):
    return await crew_service.create_crew(session, payload)


@router.put("/{crew_id}", response_model=CrewRead)
async def update_crew(crew_id: int, payload: CrewUpdate, session: AsyncSession = Depends(get_db)):
    crew = await crew_service.update_crew(session, crew_id, payload)
    if crew is None:
        raise HTTPException(404, "Crew not found")
    return crew


@router.delete("/{crew_id}", status_code=204)
async def delete_crew(crew_id: int, session: AsyncSession = Depends(get_db)):
    ok = await crew_service.delete_crew(session, crew_id)
    if not ok:
        raise HTTPException(404, "Crew not found")


# ---------- Task 子资源 ----------


@router.get("/{crew_id}/tasks", response_model=list[TaskRead])
async def list_tasks(crew_id: int, session: AsyncSession = Depends(get_db)):
    return await crew_service.list_tasks(session, crew_id)


@router.post("/{crew_id}/tasks", response_model=TaskRead, status_code=201)
async def create_task(crew_id: int, payload: TaskCreate, session: AsyncSession = Depends(get_db)):
    task = await crew_service.create_task(session, crew_id, payload)
    if task is None:
        raise HTTPException(404, "Crew not found")
    return task


@router.put("/tasks/{task_id}", response_model=TaskRead)
async def update_task(task_id: int, payload: TaskUpdate, session: AsyncSession = Depends(get_db)):
    task = await crew_service.update_task(session, task_id, payload)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: int, session: AsyncSession = Depends(get_db)):
    ok = await crew_service.delete_task(session, task_id)
    if not ok:
        raise HTTPException(404, "Task not found")
