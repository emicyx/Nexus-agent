"""Job CRUD 与配置校验（v2 S2）。

校验纯函数（validate_trigger / validate_output_config / KNOWN_PLACEHOLDERS）
供 API 创建/更新与单测复用；CRUD 与既有 service 同风格。
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobRun

logger = logging.getLogger("services.job_service")

TRIGGER_TYPES = ("cron", "interval")
PUSH_POLICIES = ("state_change", "always", "daily_summary")
# input_template 支持的占位符（job_runner 渲染；未知占位符保留原文 + WARNING）
KNOWN_PLACEHOLDERS = ("{{date}}", "{{targets_report}}", "{{kb_delta}}")
_PLACEHOLDER_RE = re.compile(r"\{\{\w+\}\}")

_CRON_RE = re.compile(
    r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$"
)


def validate_trigger(trigger_type: str, trigger_config: dict) -> None:
    """trigger_config 与 trigger_type 匹配性校验，非法抛 ValueError。"""
    if trigger_type not in TRIGGER_TYPES:
        raise ValueError(f"trigger_type 非法，取值 {TRIGGER_TYPES}")
    if not isinstance(trigger_config, dict):
        raise ValueError("trigger_config 必须是对象")
    if trigger_type == "cron":
        expr = trigger_config.get("expr")
        if not isinstance(expr, str) or not _CRON_RE.match(expr.strip()):
            raise ValueError('cron 需要 {"expr": "m h dom mon dow"} 五段表达式')
        for field in expr.split():
            _validate_cron_field(field)
    else:
        seconds = trigger_config.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 1:
            raise ValueError('interval 需要 {"seconds": ≥1 的数值}')


def _validate_cron_field(field: str) -> None:
    """宽松校验单段 cron 字段（数字/*/,/-/N-M/L/W 等常见语法放行）。"""
    if field in ("*",):
        return
    for part in field.split(","):
        if not re.match(r"^\d+([\-/]\d+)*$|^\*(/\d+)+$|^[0-9LW#\-/]+$", part):
            raise ValueError(f"cron 字段语法可疑: {field!r}（段 {part!r}）")


def validate_output_config(output_config: dict) -> None:
    """output_config.push.on 合法性校验。"""
    if not isinstance(output_config, dict):
        raise ValueError("output_config 必须是对象")
    push = output_config.get("push")
    if push is None:
        return
    if not isinstance(push, dict):
        raise ValueError("output_config.push 必须是对象")
    on = push.get("on", "always")
    if on not in PUSH_POLICIES:
        raise ValueError(f"push.on 非法，取值 {PUSH_POLICIES}")


def unknown_placeholders(template: str) -> list[str]:
    """模板里出现但不被 runner 渲染的占位符（提示用，不阻断）。"""
    return [p for p in _PLACEHOLDER_RE.findall(template or "") if p not in KNOWN_PLACEHOLDERS]


async def list_jobs(db: AsyncSession) -> list[Job]:
    return list((await db.execute(select(Job).order_by(Job.id))).scalars())


async def get_job(db: AsyncSession, job_id: int) -> Job | None:
    return await db.get(Job, job_id)


async def get_job_by_name(db: AsyncSession, name: str) -> Job | None:
    return (
        await db.execute(select(Job).where(Job.name == name))
    ).scalar_one_or_none()


async def create_job(db: AsyncSession, job: Job) -> Job:
    db.add(job)
    await db.flush()
    logger.info("job_service: created job %s (id=%s)", job.name, job.id)
    return job


async def update_job(db: AsyncSession, job: Job) -> Job:
    await db.flush()
    return job


async def list_runs(db: AsyncSession, job_id: int, limit: int = 20) -> list[JobRun]:
    stmt = (
        select(JobRun)
        .where(JobRun.job_id == job_id)
        .order_by(JobRun.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())
