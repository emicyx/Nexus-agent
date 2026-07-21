"""Skill 配置 CRUD 路由"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.skill import SkillCreate, SkillRead, SkillUpdate
from app.services import skill_service

router = APIRouter()


@router.get("", response_model=list[SkillRead])
async def list_skills(session: AsyncSession = Depends(get_db)):
    return await skill_service.list_skills(session)


@router.get("/{skill_id}", response_model=SkillRead)
async def get_skill(skill_id: int, session: AsyncSession = Depends(get_db)):
    skill = await skill_service.get_skill(session, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill not found")
    return skill


@router.post("", response_model=SkillRead, status_code=201)
async def create_skill(payload: SkillCreate, session: AsyncSession = Depends(get_db)):
    return await skill_service.create_skill(session, payload)


@router.put("/{skill_id}", response_model=SkillRead)
async def update_skill(skill_id: int, payload: SkillUpdate, session: AsyncSession = Depends(get_db)):
    skill = await skill_service.update_skill(session, skill_id, payload)
    if skill is None:
        raise HTTPException(404, "Skill not found")
    return skill


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: int, session: AsyncSession = Depends(get_db)):
    ok = await skill_service.delete_skill(session, skill_id)
    if not ok:
        raise HTTPException(404, "Skill not found")
