"""job 执行链（v2 S2 §5.3）：渲染输入 → run_crew_job → 推送策略 → 落账。

设计要点：
- 巡检数据源是**确定性扫测**（check_all_targets），不是 LLM 输出——
  result_summary.targets 是 state_change 检测的权威快照；
- 推送策略在 runner 判定（push_expected），经输入末尾的[推送指令]告知 agent；
  agent 侧 push_message 与 runner 兜底推送共享 egress 落账（agent 已推则
  runner 不重复推；agent 忘推且该推时 runner 兜底推 final answer，告警不丢）；
- eval 零外发（§5.6 硬红线）：eval run 状态记 eval、egress 双闸抑制、
  不计入连续失败熔断；
- 熔断（§3.2.8）：连续失败达 max_consecutive_failures 自动停用 + 推一条
  告警后静默（该次 run 状态记 disabled_by_circuit）；
- 每 job 并发 = 1（_job_locks 跨 scheduled/manual 两路入口）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models import Job, JobRun
from app.models.job import PUSHED_SUPPRESSED_EVAL
from app.services import egress, job_service
from app.services.egress import egress_scope

logger = logging.getLogger("services.job_runner")


class JobBusyError(RuntimeError):
    """该 job 已有运行中的实例（每 job 并发 = 1）。"""


# ── 纯函数（单测覆盖） ──────────────────────────────────────────────

def diff_states(prev_targets: dict | None, current: list[dict]) -> list[str]:
    """与上一次成功快照对比 up/down 变化。

    prev_targets: 上次 result_summary["targets"]（{name: {"up": bool}}）；
    无历史（首次基线）返回 []——基线不推送。
    """
    if not prev_targets:
        return []
    changes: list[str] = []
    for r in current:
        name = r.get("name", "")
        prev = prev_targets.get(name)
        if prev is None:
            continue  # 新增目标的基线化交给下一次对比
        was_up = bool(prev.get("up"))
        now_up = bool(r.get("up"))
        if was_up and not now_up:
            changes.append(f"{name}: up→down")
        elif not was_up and now_up:
            changes.append(f"{name}: down→up")
    return changes


def compute_push_expected(policy: str, changed: list[str]) -> bool:
    """推送策略判定：state_change 只在变化时推；always/daily_summary 每次推。"""
    if policy == "state_change":
        return bool(changed)
    return True  # always / daily_summary


def render_input_template(template: str, placeholders: dict[str, str]) -> str:
    """占位符替换（已知占位符全量替换；未知占位符保留原文并告警）。"""
    out = template or ""
    for key, value in placeholders.items():
        out = out.replace(key, value)
    unknown = job_service.unknown_placeholders(out)
    if unknown:
        logger.warning("job_runner: 模板存在未渲染占位符 %s（保留原文）", unknown)
    return out


def build_push_directive(push_expected: bool) -> str:
    if push_expected:
        return (
            "\n\n[推送指令] 本次任务需要推送：请把最终报告组织成一条适合 IM 阅读的"
            "纯文本消息（结构清晰、无 markdown 语法），并调用 push_message 工具发送；"
            "发送后基于推送结果给出简短结论。"
        )
    return (
        "\n\n[推送指令] 本次无需推送（策略判定无触发）：严禁调用 push_message，"
        "直接输出简短的任务摘要即可。"
    )


def render_targets_report(
    results: list[dict], changes: list[str], prev_targets: dict | None
) -> str:
    """确定性扫测结果 → {{targets_report}} 文本（含上次状态与变化说明）。"""
    from app.tools.http_check_tool import format_results

    lines = ["【本次巡检结果（系统确定性探测）】", format_results(results)]
    if prev_targets:
        lines.append("\n【上次成功巡检状态】")
        for name, st in sorted(prev_targets.items()):
            lines.append(f"- {name}: {'UP' if st.get('up') else 'DOWN'}")
    else:
        lines.append("\n（首次巡检：本次为基线快照，无历史对比）")
    lines.append("\n【状态变化】")
    lines.append("\n".join(changes) if changes else "（无 up/down 变化）")
    return "\n".join(lines)


# ── 执行链 ─────────────────────────────────────────────────────────

# 每 job 并发 = 1（§3.2.8）：scheduled 与 manual 共用
_job_locks: dict[int, asyncio.Lock] = {}

_TERMINAL_STATUSES = ("succeeded", "failed", "disabled_by_circuit", "eval")


async def _last_snapshot(job_id: int) -> dict | None:
    """上一次成功（非 eval）run 的 result_summary（state_change 数据源）。"""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(JobRun)
            .where(JobRun.job_id == job_id, JobRun.status == "succeeded")
            .order_by(JobRun.id.desc())
            .limit(1)
        )
        run = (await db.execute(stmt)).scalars().first()
        summary = run.result_summary if run else None
        return dict(summary) if isinstance(summary, dict) else None


async def _last_success_started_at(job_id: int) -> datetime | None:
    """上一次成功 run 的 started_at（kb_delta 增量起点）。"""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(JobRun)
            .where(JobRun.job_id == job_id, JobRun.status == "succeeded")
            .order_by(JobRun.id.desc())
            .limit(1)
        )
        run = (await db.execute(stmt)).scalars().first()
        return run.started_at if run else None


async def execute_job(job_id: int, *, eval_mode: bool = False, manual: bool = False) -> dict | None:
    """执行一次 job。返回 {"run_id", "status", "pushed_to"} 概要。

    - eval_mode：该次 run 记 status=eval、egress 抑制、不计熔断（§5.6）；
    - manual：跳过 enabled 检查（运维可手动跑已停用 job 验证）；运行中再触发
      抛 JobBusyError（scheduled 路径则记日志跳过）。
    - job 不存在/停用（非 manual）/停机中：返回 None。
    """
    from app.core import run_control
    from app.core.token_budget import get_session_totals
    from app.crews.factory import run_crew_job

    eval_mode = eval_mode or settings.NEXUS_EVAL_MODE

    lock = _job_locks.setdefault(job_id, asyncio.Lock())
    if lock.locked():
        if manual:
            raise JobBusyError(f"job {job_id} 已有运行中的实例")
        logger.info("job_runner: job %s 上次触发仍在运行，本次跳过（max_instances=1）", job_id)
        return None

    async with lock:
        async with AsyncSessionLocal() as db:
            job = await job_service.get_job(db, job_id)
            if job is None:
                return None
            if not job.enabled and not manual:
                return None
            if run_control.is_shutting_down():
                logger.info("job_runner: 停机中，跳过 job %s", job.name)
                return None

            started_at = datetime.now(timezone.utc)
            run = JobRun(job_id=job.id, status="running", started_at=started_at)
            db.add(run)
            await db.flush()
            run_id = run.id
            crew_id = job.crew_id
            template = job.input_template
            policy = ((job.output_config or {}).get("push") or {}).get("on", "always")
            cost_cap = job.cost_cap_tokens
            max_failures = job.max_consecutive_failures or 5
            job_name = job.name
            await db.commit()

        logger.info("job_runner: job=%s run=%s 开始（eval=%s manual=%s policy=%s）",
                    job_name, run_id, eval_mode, manual, policy)

        # ---- 确定性输入收集与模板渲染 ----
        placeholders: dict[str, str] = {}
        sweep: list[dict] | None = None
        changes: list[str] = []
        try:
            if "{{date}}" in template:
                tz_name = settings.JOB_TIMEZONE
                try:
                    from zoneinfo import ZoneInfo

                    tz = ZoneInfo(tz_name)
                except Exception:  # noqa: BLE001 - 时区库缺失降级 UTC
                    tz = timezone.utc
                placeholders["{{date}}"] = datetime.now(tz).strftime("%Y-%m-%d %A %H:%M")
            if "{{targets_report}}" in template:
                from app.tools.http_check_tool import check_all_targets

                sweep = await asyncio.to_thread(check_all_targets)
                prev_summary = await _last_snapshot(job_id)
                prev_targets = (
                    prev_summary.get("targets") if isinstance(prev_summary, dict) else None
                )
                changes = diff_states(prev_targets, sweep)
                placeholders["{{targets_report}}"] = render_targets_report(sweep, changes, prev_targets)
            if "{{kb_delta}}" in template:
                from app.services.kb_delta import kb_delta, render_kb_delta

                since = await _last_success_started_at(job_id)
                placeholders["{{kb_delta}}"] = render_kb_delta(await kb_delta(since))
        except Exception as e:  # noqa: BLE001 - 输入收集失败按失败落账
            logger.exception("job_runner: 输入收集失败 job=%s", job_name)
            await _finalize_failed_early(run_id, job_id, f"输入收集失败: {e}")
            return {"run_id": run_id, "status": "failed", "pushed_to": None}

        push_expected = compute_push_expected(policy, changes)
        message = render_input_template(template, placeholders) + build_push_directive(push_expected)

        # ---- crew 执行（egress 上下文贯穿 agent 工具线程） ----
        # token 归集：run_crew_job 内部按 token_session 绑定（worker 线程经
        # to_thread 复制 contextvar 可见），runner 只做前后差值
        token_session = f"jobrun:{run_id}"
        tokens_before = get_session_totals().get(token_session, 0)
        async with egress_scope(eval_mode, run_label=f"{job_name}#{run_id}") as ectx:
            outcome = await run_crew_job(crew_id, message, token_session=token_session)
        tokens_used = max(0, get_session_totals().get(token_session, 0) - tokens_before)

        # ---- 推送落账（agent 已推 → 沿用；该推未推 → runner 兜底） ----
        # pushed_to_backup（S3' §6.3）：与 pushed_to 同源（PushOutcome.backup）——
        # agent 侧推送与 runner 兜底推送共用一套备推落账
        pushed_to: str | None = None
        pushed_to_backup: str | None = None
        if push_expected:
            if ectx.outcome is not None:
                pushed_to = ectx.outcome.pushed_to
                pushed_to_backup = ectx.outcome.backup or None
            elif eval_mode:
                # 结构性抑制兜底：eval run 该推但 agent 未走到工具层，也必须记抑制
                # （eval 零外发红线：QQ 与钉钉备推都不会发送，backup 必为 None）
                pushed_to = PUSHED_SUPPRESSED_EVAL
            elif outcome.answer:
                logger.warning(
                    "job_runner: agent 未推送但策略要求推送，runner 兜底推送 final answer job=%s",
                    job_name,
                )
                fallback = await egress.push_to_qq(outcome.answer, eval_mode=eval_mode)
                pushed_to = fallback.pushed_to
                pushed_to_backup = fallback.backup or None

        # ---- 结果落账 ----
        result_summary: dict[str, Any] = {"events_tail": outcome.events_tail}
        if sweep is not None:
            result_summary["targets"] = {
                r["name"]: {k: r.get(k) for k in ("up", "status", "latency_ms", "error")}
                for r in sweep
            }
            result_summary["changes"] = changes

        status = "eval" if eval_mode else ("succeeded" if not outcome.error else "failed")
        error_text = outcome.error or None
        cost_note = "token_budget 会话差值（按本次运行会话归集，近似值）"

        # 成本上限（§3.2.8）：事后判定，超出按失败落账并计入熔断
        if status == "succeeded" and cost_cap and tokens_used > cost_cap:
            status = "failed"
            error_text = f"单次 token 超上限：{tokens_used} > cost_cap_tokens={cost_cap}"

        # 熔断（eval 不计数不触发）
        tripped = False
        consecutive = None
        if not eval_mode:
            async with AsyncSessionLocal() as db:
                job = await job_service.get_job(db, job_id)
                if job is None:
                    return None
                job.last_run_at = started_at
                if status == "succeeded":
                    job.consecutive_failures = 0
                else:
                    job.consecutive_failures = (job.consecutive_failures or 0) + 1
                consecutive = job.consecutive_failures
                if (
                    status == "failed"
                    and job.enabled
                    and job.consecutive_failures >= max_failures
                ):
                    job.enabled = False
                    tripped = True
                    status = "disabled_by_circuit"
                await db.commit()

        async with AsyncSessionLocal() as db:
            run = await db.get(JobRun, run_id)
            if run is not None:
                run.status = status
                run.finished_at = datetime.now(timezone.utc)
                run.error = error_text
                run.tokens_used = tokens_used
                run.cost_note = cost_note
                run.result_summary = result_summary
                run.pushed_to = pushed_to
                run.pushed_to_backup = pushed_to_backup
                await db.commit()

        if tripped:
            logger.error(
                "job_runner: job=%s 连续失败 %s 次触发熔断，自动停用", job_name, consecutive
            )
            # 熔断告警推送后静默（§3.2.8）；NEXUS_EVAL_MODE 下同样被抑制
            await egress.push_to_qq(
                f"[Nexus 告警] 定时任务「{job_name}」连续失败 {consecutive} 次，"
                f"已自动停用。最近错误：{(error_text or '')[:200]}\n"
                "请在 Web 端 config → Jobs 查看运行记录并修复后重新启用。",
                eval_mode=eval_mode,
            )

        # 调度器侧刷新（next_run_at 展示；停用任务移除注册）
        try:
            from app.services import job_scheduler

            await job_scheduler.sync_after_run(job_id)
        except Exception:  # noqa: BLE001 - 调度器未启动等场景
            pass

        logger.info("job_runner: job=%s run=%s 结束 status=%s pushed_to=%s backup=%s tokens=%s",
                    job_name, run_id, status, pushed_to, pushed_to_backup, tokens_used)
        return {
            "run_id": run_id, "status": status, "pushed_to": pushed_to,
            "pushed_to_backup": pushed_to_backup,
        }


async def _finalize_failed_early(run_id: int, job_id: int, error: str) -> None:
    """输入收集阶段的失败落账（并推进熔断计数）。"""
    async with AsyncSessionLocal() as db:
        run = await db.get(JobRun, run_id)
        if run is not None:
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error = error
            await db.commit()
        job = await job_service.get_job(db, job_id)
        if job is not None:
            job.last_run_at = datetime.now(timezone.utc)
            job.consecutive_failures = (job.consecutive_failures or 0) + 1
            if job.enabled and job.consecutive_failures >= (job.max_consecutive_failures or 5):
                job.enabled = False
            await db.commit()
