"""Jobs REST API（v2 S2 §5.5，最小集）：X-API-Key 鉴权由 main 挂载统一注入。

- POST/GET /v1/jobs、PATCH /v1/jobs/{id}
- GET /v1/jobs/{id}/runs（最近运行状态）
- POST /v1/jobs/{id}/run（手动触发，body 可带 "eval": true → 零外发 §5.6）

CRUD 变更后同步调度器（reschedule_job）；手动触发同步等待执行完成并返回
本次 run 概要（eval 演练/运维验证需要立即看到结果）。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Job
from app.schemas.job import (
    JobCreate,
    JobRead,
    JobRunRead,
    JobTriggerRequest,
    JobTriggerResponse,
    JobUpdate,
)
from app.services import job_service
from app.services.job_runner import JobBusyError, execute_job

import logging

logger = logging.getLogger("api.jobs")

router = APIRouter()


def _validate_or_400(payload: JobCreate | JobUpdate, current: Job | None = None) -> None:
    """创建/更新时的配置校验（trigger 与推送策略）。"""
    trigger_type = getattr(payload, "trigger_type", None) or (
        current.trigger_type if current else None
    )
    trigger_config = getattr(payload, "trigger_config", None) or (
        current.trigger_config if current else None
    )
    if trigger_type and trigger_config is not None:
        try:
            job_service.validate_trigger(trigger_type, trigger_config)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    output_config = getattr(payload, "output_config", None) or (
        current.output_config if current else None
    )
    if output_config is not None:
        try:
            job_service.validate_output_config(output_config)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    template = getattr(payload, "input_template", None)
    if template is not None:
        unknown = job_service.unknown_placeholders(template)
        if unknown:
            # 不阻断（保留扩展空间），但 422 提示比静默留 {{xxx}} 更诚实
            raise HTTPException(
                422,
                f"input_template 含未知占位符 {unknown}，"
                f"可用：{job_service.KNOWN_PLACEHOLDERS}",
            )


@router.get("", response_model=list[JobRead])
async def list_jobs(session: AsyncSession = Depends(get_db)):
    return await job_service.list_jobs(session)


@router.post("", response_model=JobRead, status_code=201)
async def create_job(payload: JobCreate, session: AsyncSession = Depends(get_db)):
    _validate_or_400(payload)
    if await job_service.get_job_by_name(session, payload.name):
        raise HTTPException(409, f"job 名已存在: {payload.name}")
    from app.models import CrewConfig

    if await session.get(CrewConfig, payload.crew_id) is None:
        raise HTTPException(404, f"crew 不存在: {payload.crew_id}")
    job = Job(
        name=payload.name,
        enabled=payload.enabled,
        trigger_type=payload.trigger_type,
        trigger_config=payload.trigger_config,
        crew_id=payload.crew_id,
        input_template=payload.input_template,
        output_config=payload.output_config,
        cost_cap_tokens=payload.cost_cap_tokens,
        max_consecutive_failures=payload.max_consecutive_failures,
    )
    job = await job_service.create_job(session, job)
    await session.commit()
    await session.refresh(job)
    from app.services import job_scheduler

    await job_scheduler.reschedule_job(job.id)
    return job


@router.patch("/{job_id}", response_model=JobRead)
async def update_job(job_id: int, payload: JobUpdate, session: AsyncSession = Depends(get_db)):
    job = await job_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    _validate_or_400(payload, current=job)
    data = payload.model_dump(exclude_unset=True)
    # 重名检查（在 setattr 之前，否则比较恒 False）
    if data.get("name") and data["name"] != job.name:
        other = await job_service.get_job_by_name(session, data["name"])
        if other is not None and other.id != job.id:
            raise HTTPException(409, f"job 名已存在: {data['name']}")
    if data.get("crew_id") is not None:
        from app.models import CrewConfig

        if await session.get(CrewConfig, data["crew_id"]) is None:
            raise HTTPException(404, f"crew 不存在: {data['crew_id']}")
    # trigger_type 与 trigger_config 成对语义：只改其一 = 与现值组合后必须仍合法
    for field in ("name", "enabled", "trigger_type", "trigger_config", "crew_id",
                  "input_template", "output_config", "cost_cap_tokens",
                  "max_consecutive_failures"):
        if field in data and data[field] is not None:
            setattr(job, field, data[field])
    job = await job_service.update_job(session, job)
    await session.commit()
    await session.refresh(job)
    from app.services import job_scheduler

    await job_scheduler.reschedule_job(job.id)
    return job


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: int, session: AsyncSession = Depends(get_db)):
    """删除 job（级联 job_runs）。计划 §1b.4 未列 DELETE，但代码库其余 CRUD
    资源均有 DELETE，且没有它误建 job 无法清理——按'遵循现有模式'补齐。"""
    job = await job_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    name = job.name
    await session.delete(job)
    await session.commit()
    from app.services import job_scheduler

    await job_scheduler.reschedule_job(job_id)  # job 已不存在 → 移除注册
    logger.info("jobs api: deleted job %s (id=%s)", name, job_id)


@router.get("/{job_id}/runs", response_model=list[JobRunRead])
async def list_job_runs(
    job_id: int, limit: int = 20, session: AsyncSession = Depends(get_db)
):
    if await job_service.get_job(session, job_id) is None:
        raise HTTPException(404, "Job not found")
    return await job_service.list_runs(session, job_id, limit=min(max(limit, 1), 100))


@router.post("/{job_id}/run", response_model=JobTriggerResponse)
async def run_job(job_id: int, payload: JobTriggerRequest | None = None,
                  session: AsyncSession = Depends(get_db)):
    """手动触发（同步执行）：eval=true 走零外发演练路径（§5.6）。"""
    if await job_service.get_job(session, job_id) is None:
        raise HTTPException(404, "Job not found")
    eval_mode = bool(payload and payload.eval)
    try:
        result = await execute_job(job_id, eval_mode=eval_mode, manual=True)
    except JobBusyError as e:
        raise HTTPException(409, str(e)) from e
    if result is None:
        raise HTTPException(409, "job 不可运行（停用中且非手动路径，或停机中）")
    return JobTriggerResponse(**result)
