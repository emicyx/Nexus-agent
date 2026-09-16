"""主动推送出口（v2 S2：egress 服务）。

职责（执行计划 §5.4 + §5.6 + 安全不变量 1/2）：
- 一切主动推送（巡检告警/日报/熔断告警/runner 兜底）都经 push_to_qq()，
  最终走 S1 的 onebot_adapter.send_qq_message——全系统唯一 QQ 出口；
  目标只来自 env QQ_ALERT_TARGET，任何调用方都不带目标参数。
- eval 抑制（硬红线）：EgressContext.eval_mode 或全局 NEXUS_EVAL_MODE 时，
  一律不发送、记 'suppressed(eval)'。
- 断连降级：推送前查渠道状态，离线时等 5s 重查一次（NapCat 可能刚重连），
  仍离线记 'failed(channel_offline)'，不静默丢。

线程模型：push_message 工具在 worker 线程执行（crewai_async_patch 的
asyncio.to_thread 复制 contextvars），EgressContext 经 ContextVar 传入工具
线程；工具侧用 run_coroutine_threadsafe 回到主循环执行真正的 WS 发送。
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger("services.egress")

# 离线重试：等待秒数（NapCat 反连通常秒级；过短无意义，过长拖慢 job）
_OFFLINE_RETRY_WAIT_S = 5.0
# 工具侧等待推送完成的超时（分段多条时 send 本身要几秒）
_PUSH_RPC_TIMEOUT_S = 60.0


@dataclass
class PushOutcome:
    """一次推送的落账结果（job_runs.pushed_to 的取值来源）。"""

    pushed_to: str  # 'qq' | 'suppressed(eval)' | 'failed(...)' | 'failed(no_target)'
    detail: str = ""
    segments: int = 0


@dataclass
class EgressContext:
    """一次 job 运行的 egress 上下文（contextvar 贯穿 agent 工具线程）。

    outcome 由 agent 侧 push_message 调用写入（对象引用共享，主循环可读），
    job_runner 据此决定是否需要兜底推送与 pushed_to 落账。
    """

    eval_mode: bool
    loop: asyncio.AbstractEventLoop
    run_label: str = ""
    outcome: PushOutcome | None = field(default=None, repr=False)
    pushed_chars: int = 0


_egress_ctx: contextvars.ContextVar[EgressContext | None] = contextvars.ContextVar(
    "egress_ctx", default=None
)


def set_egress_context(ctx: EgressContext | None) -> contextvars.Token:
    return _egress_ctx.set(ctx)


def reset_egress_context(token: contextvars.Token) -> None:
    _egress_ctx.reset(token)


def current_egress_context() -> EgressContext | None:
    """当前任务的 egress 上下文（无 job 上下文时为 None，如普通 Web 会话）。"""
    return _egress_ctx.get()


def parse_alert_target(raw: str | None = None) -> dict | None:
    """解析 QQ_ALERT_TARGET（安全不变量 1：推送目标唯一来源）。

    合法形态：{"type":"private","user_id":int} 或 {"type":"group","group_id":int}。
    非法/未配置返回 None（调用方记 failed(no_target)，不猜测默认目标）。
    """
    raw = raw if raw is not None else settings.QQ_ALERT_TARGET
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("egress: QQ_ALERT_TARGET 不是合法 JSON: %r", raw[:80])
        return None
    if not isinstance(obj, dict):
        logger.error("egress: QQ_ALERT_TARGET 必须是对象")
        return None
    t = obj.get("type")
    if t == "private":
        uid = obj.get("user_id")
        if isinstance(uid, int) and uid > 0:
            return {"type": "private", "user_id": uid}
    elif t == "group":
        gid = obj.get("group_id")
        if isinstance(gid, int) and gid > 0:
            return {"type": "group", "group_id": gid}
    logger.error("egress: QQ_ALERT_TARGET 形态非法（private/user_id 或 group/group_id）: %s", obj)
    return None


def _eval_mode_active(ctx: EgressContext | None) -> bool:
    """eval 判定：显式 ctx 或全局开关，任一命中即抑制（双闸，§5.6）。"""
    return settings.NEXUS_EVAL_MODE or (ctx is not None and ctx.eval_mode)


async def push_to_qq(
    content: str,
    *,
    ctx: EgressContext | None = None,
    eval_mode: bool | None = None,
) -> PushOutcome:
    """按 QQ_ALERT_TARGET 推送纯文本（超长由 send_qq_message 分段）。

    - ctx 缺省取当前 contextvar（agent 工具路径）；runner 兜底路径可显式传
      eval_mode 覆盖。
    - 成功记 'qq'；eval 抑制记 'suppressed(eval)'；失败如实记 failed(...)。
    - 会话内多次推送时 outcome 覆盖为最近一次（最终落账以最后一次为准）。
    """
    ctx = ctx if ctx is not None else _egress_ctx.get()
    is_eval = _eval_mode_active(ctx) if eval_mode is None else (eval_mode or settings.NEXUS_EVAL_MODE)

    if is_eval:
        outcome = PushOutcome(pushed_to="suppressed(eval)", detail="eval 模式：跳过发送")
        _record(ctx, outcome, content)
        return outcome

    target = parse_alert_target()
    if target is None:
        outcome = PushOutcome(pushed_to="failed(no_target)", detail="QQ_ALERT_TARGET 未配置或非法")
        _record(ctx, outcome, content)
        return outcome

    from app.channels import onebot_adapter

    # 推送前查连接（§5.4）：离线 → 等 5s 重查一次（NapCat 重连窗口）
    if not onebot_adapter.get_status()["connected"]:
        await asyncio.sleep(_OFFLINE_RETRY_WAIT_S)
        if not onebot_adapter.get_status()["connected"]:
            logger.warning("egress: 渠道离线，推送降级 failed(channel_offline) label=%s",
                           getattr(ctx, "run_label", ""))
            outcome = PushOutcome(pushed_to="failed(channel_offline)",
                                  detail="onebot 渠道离线（重查一次仍离线）")
            _record(ctx, outcome, content)
            return outcome

    try:
        if target["type"] == "private":
            result = await onebot_adapter.send_qq_message(content, user_id=target["user_id"])
        else:
            result = await onebot_adapter.send_qq_message(content, group_id=target["group_id"])
    except Exception as e:  # noqa: BLE001 - 发送异常如实落账，不静默丢
        logger.exception("egress: send_qq_message 异常")
        outcome = PushOutcome(pushed_to=f"failed({type(e).__name__})", detail=str(e)[:200])
        _record(ctx, outcome, content)
        return outcome

    if result.ok:
        outcome = PushOutcome(pushed_to="qq", segments=result.segments)
    else:
        outcome = PushOutcome(pushed_to=f"failed({result.reason or 'unknown'})",
                              segments=result.segments)
    _record(ctx, outcome, content)
    return outcome


def _record(ctx: EgressContext | None, outcome: PushOutcome, content: str) -> None:
    """把结果记进运行上下文（agent 侧推送与 runner 兜底共用一套落账）。"""
    if ctx is not None:
        ctx.outcome = outcome
        ctx.pushed_chars = len(content)


def push_from_tool(content: str) -> str:
    """push_message 工具的同步入口（worker 线程内调用）。

    经 run_coroutine_threadsafe 回到主循环执行推送；无 job 上下文时拒绝
    （推送是 job 专属能力，Web 会话里不开放自由推送面）。
    """
    ctx = _egress_ctx.get()
    if ctx is None:
        return (
            "错误：push_message 仅在定时任务运行内可用（当前无任务上下文），"
            "本次未发送任何内容。"
        )
    future = asyncio.run_coroutine_threadsafe(push_to_qq(content, ctx=ctx), ctx.loop)
    try:
        outcome: PushOutcome = future.result(timeout=_PUSH_RPC_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        logger.exception("egress: push_from_tool 失败")
        outcome = PushOutcome(pushed_to=f"failed({type(e).__name__})", detail=str(e)[:200])
        ctx.outcome = outcome
    if outcome.pushed_to == "qq":
        return (
            f"✅ 推送成功（{outcome.segments} 段，目标来自系统配置）。"
            "请基于推送结果给出简短结论，不要重复推送。"
        )
    if outcome.pushed_to == "suppressed(eval)":
        return "⚠️ 本次为 eval 演练运行：推送被系统抑制（未真实发送），任务流程视同已推送继续。"
    return (
        f"❌ 推送失败：{outcome.pushed_to}（{outcome.detail}）。"
        "请如实向用户报告推送失败与原因，不要反复重试推送。"
    )


def alert_target_ok() -> bool:
    """启动/调度前的自检：推送目标已配置且形态合法。"""
    return parse_alert_target() is not None


@contextlib.asynccontextmanager
async def egress_scope(eval_mode: bool, run_label: str = ""):
    """job_runner 用：进入时建上下文并绑 contextvar，退出时解绑。"""
    ctx = EgressContext(
        eval_mode=eval_mode,
        loop=asyncio.get_running_loop(),
        run_label=run_label,
    )
    token = set_egress_context(ctx)
    try:
        yield ctx
    finally:
        reset_egress_context(token)
