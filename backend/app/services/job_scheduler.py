"""APScheduler 调度器（v2 S2 §5.2）：AsyncIOScheduler + memory jobstore。

- lifespan 启动（参考 sandbox_cleanup 模式），从 jobs 表加载 enabled 任务注册；
- job CRUD 后由 API 层调 reschedule_job 同步增删；
- 参数纪律（§5.2）：max_instances=1、coalesce=True、misfire_grace_time=300、
  timezone=JOB_TIMEZONE（cron 一律显式时区，安全不变量 9）；
- 回滚：JOBS_ENABLED=false 时 start 是空操作（§3.3）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.services import job_service

logger = logging.getLogger("services.job_scheduler")

_scheduler = None  # AsyncIOScheduler（lazy 创建，apscheduler 为 S2 新依赖）
_started = False


def _resolve_timezone():
    """JOB_TIMEZONE 解析（zoneinfo 不可用/名称非法时降级 UTC 并告警）。"""
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(settings.JOB_TIMEZONE)
    except Exception as e:  # noqa: BLE001 - tz 数据缺失/名称非法
        logger.warning("job_scheduler: 时区 %s 不可用（%s），降级 UTC",
                       settings.JOB_TIMEZONE, e)
        return timezone.utc


def build_trigger(trigger_type: str, trigger_config: dict):
    """trigger 配置 → APScheduler trigger（配置非法抛 ValueError，调用方落日志）。"""
    tz = _resolve_timezone()
    if trigger_type == "cron":
        from apscheduler.triggers.cron import CronTrigger

        return CronTrigger.from_crontab(str(trigger_config["expr"]).strip(), timezone=tz)
    if trigger_type == "interval":
        from apscheduler.triggers.interval import IntervalTrigger

        return IntervalTrigger(seconds=float(trigger_config["seconds"]), timezone=tz)
    raise ValueError(f"未知 trigger_type: {trigger_type}")


async def _fire(job_id: int) -> None:
    """调度回调：异常全部落日志（APScheduler 吞异常会静默失败）。"""
    from app.services import job_runner

    try:
        await job_runner.execute_job(job_id, manual=False)
    except Exception:  # noqa: BLE001
        logger.exception("job_scheduler: job %s 执行异常", job_id)


def _register(sched, job_row) -> bool:
    """把一条 jobs 行注册进调度器（已校验 enabled）。返回是否成功。"""
    try:
        trigger = build_trigger(job_row.trigger_type, job_row.trigger_config or {})
    except Exception as e:  # noqa: BLE001
        logger.error("job_scheduler: job %s trigger 配置非法，跳过注册: %s", job_row.name, e)
        return False
    sched.add_job(
        _fire,
        trigger=trigger,
        args=(job_row.id,),
        id=str(job_row.id),
        name=job_row.name,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    return True


def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        _scheduler = AsyncIOScheduler(timezone=_resolve_timezone())
    return _scheduler


async def start_scheduler() -> None:
    """lifespan 启动入口：加载 enabled 任务注册。"""
    global _started
    if _started:
        return
    if not settings.JOBS_ENABLED:
        logger.info("job_scheduler: JOBS_ENABLED=false，调度器不启动（回滚开关）")
        _started = True
        return
    sched = _get_scheduler()
    sched.start()
    _started = True
    registered = 0
    async with AsyncSessionLocal() as db:
        jobs = await job_service.list_jobs(db)
        for row in jobs:
            if row.enabled and _register(sched, row):
                registered += 1
        # next_run_at 回写（展示用；memory jobstore 重启后由触发器重算）
        await _sync_next_run_times(db, sched)
        await db.commit()
    if registered and not _alert_target_configured():
        logger.warning(
            "job_scheduler: 已注册 %d 个任务但 QQ_ALERT_TARGET 未配置/非法——"
            "推送将落账 failed(no_target)，请检查 .env", registered,
        )
    logger.info("job_scheduler: started（注册 %d 个任务，时区 %s）",
                registered, settings.JOB_TIMEZONE)


def _alert_target_configured() -> bool:
    from app.services.egress import alert_target_ok

    return alert_target_ok()


async def stop_scheduler() -> None:
    """lifespan 停机：不等待在跑 job（run_control 优雅停机统一接管收尾）。"""
    global _scheduler, _started
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        _scheduler = None
    _started = False
    logger.info("job_scheduler: stopped")


async def reschedule_job(job_id: int) -> None:
    """job CRUD 后同步：enabled → 注册/替换；停用/删除 → 移除。"""
    if not _started or _scheduler is None:
        return
    sched = _scheduler
    async with AsyncSessionLocal() as db:
        job = await job_service.get_job(db, job_id)
        if job is None or not job.enabled:
            _remove(sched, job_id)
            if job is not None:
                job.next_run_at = None
                await db.commit()
            return
        ok = _register(sched, job)
        await _sync_next_run_times(db, sched, only_job_id=job_id)
        await db.commit()
        if ok:
            logger.info("job_scheduler: rescheduled job %s (%s)", job.id, job.name)


async def sync_after_run(job_id: int) -> None:
    """run 结束后的调度侧刷新：被熔断停用的移除注册 + next_run_at 回写。"""
    if not _started or _scheduler is None:
        return
    sched = _scheduler
    async with AsyncSessionLocal() as db:
        job = await job_service.get_job(db, job_id)
        if job is None:
            _remove(sched, job_id)
            return
        if not job.enabled:
            _remove(sched, job_id)
            job.next_run_at = None
        await _sync_next_run_times(db, sched, only_job_id=job_id)
        await db.commit()


def _remove(sched, job_id: int) -> None:
    try:
        sched.remove_job(str(job_id))
        logger.info("job_scheduler: removed job %s", job_id)
    except Exception:  # noqa: BLE001 - JobLookupError（未注册）静默
        pass


async def _sync_next_run_times(db, sched, only_job_id: int | None = None) -> None:
    """把 APScheduler 的 next_run_time 回写 jobs 表（展示用）。"""
    jobs = await job_service.list_jobs(db)
    for row in jobs:
        if only_job_id is not None and row.id != only_job_id:
            continue
        if not row.enabled:
            continue
        try:
            apjob = sched.get_job(str(row.id))
        except Exception:  # noqa: BLE001
            apjob = None
        if apjob is not None and apjob.next_run_time is not None:
            nxt = apjob.next_run_time
            row.next_run_at = nxt if isinstance(nxt, datetime) else nxt.astimezone()
