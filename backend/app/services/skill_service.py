"""Skill 配置 CRUD 服务"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SkillConfig
from app.schemas.skill import SkillCreate, SkillUpdate


async def list_skills(session: AsyncSession) -> list[SkillConfig]:
    stmt = select(SkillConfig).order_by(SkillConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_skill(session: AsyncSession, skill_id: int) -> SkillConfig | None:
    return await session.get(SkillConfig, skill_id)


async def create_skill(session: AsyncSession, payload: SkillCreate) -> SkillConfig:
    skill = SkillConfig(
        name=payload.name,
        description=payload.description,
        prompt_template=payload.prompt_template,
        skill_key=payload.skill_key,
        config_json=payload.config_json,
    )
    session.add(skill)
    await session.flush()
    await session.commit()
    await session.refresh(skill)
    return skill


async def update_skill(session: AsyncSession, skill_id: int, payload: SkillUpdate) -> SkillConfig | None:
    skill = await session.get(SkillConfig, skill_id)
    if skill is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(skill, k, v)
    await session.commit()
    await session.refresh(skill)
    return skill


async def delete_skill(session: AsyncSession, skill_id: int) -> bool:
    skill = await session.get(SkillConfig, skill_id)
    if skill is None:
        return False
    await session.delete(skill)
    await session.commit()
    return True
