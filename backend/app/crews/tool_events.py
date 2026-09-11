"""工具事件包装器 - 为 CrewAI BaseTool 注入 tool_call / tool_result 事件推送

Week 2: 让前端能实时看到"正在检索..."、"工具调用完成"等状态。
通过包装 BaseTool._run 方法，在工具执行前后向 asyncio.Queue 推送事件。

用法:
    tool = wrap_tool_with_events(tool, queue, loop, agent_role="研究员")
"""
import asyncio
import logging
import time
from typing import Any

from crewai.tools import BaseTool

from app.core.events import AgentEvent, try_put
from app.core.metrics import record_tool_call
from app.core.run_control import RunCancelledError, raise_if_cancelled
from app.core.token_budget import check_budget_checkpoint

logger = logging.getLogger("tools")


def _truncate(text: Any, limit: int = 500) -> str:
    """截断过长的工具输入/输出，避免 SSE 消息过大。"""
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else s[:limit] + "...(truncated)"


# ── 重试风暴护栏（2026-09-09 评测发现 #6）─────────────────────────────
# rt-4 实录：agent 对同一被拒写入原样重试 7 次直到 max_iter——错误不可恢复时
# 反复重试只烧迭代与 token。同一工具同一失败形态连续 N 次后，在工具结果里
# 附加明确的停止重试指令，把"换个姿势再试一次"变成"改路或如实报告失败"。
# 状态在闭包内（每个 wrapped 工具实例独立 = 每 agent 每次运行独立），成功即复位。

_FAILURE_TEXT_PREFIXES = ("错误：", "拒绝", "工具执行出错", "失败")
_ESCALATE_AFTER = 3  # 连续第 3 次同形态失败开始附加指令
_SIG_CHARS = 60  # 失败签名长度：错误类别+开头细节，可合并微小参数变体
_ESCALATION_NOTE = (
    "\n\n[系统护栏] 该调用已连续 {n} 次以相同方式失败，大概率不可恢复。"
    "不要再以相同或相近参数重试：请改用其他可行方案，"
    "或如实向用户报告当前无法完成及原因，然后结束任务。"
)


def _is_failure_text(result: Any) -> bool:
    """按平台工具错误串约定识别"以返回值形式报告的失败"。"""
    if not isinstance(result, str):
        return False
    head = result.lstrip()[:10]
    return any(head.startswith(p) for p in _FAILURE_TEXT_PREFIXES)


def _safe_put(queue: "asyncio.Queue[AgentEvent | None]", evt: AgentEvent, loop: asyncio.AbstractEventLoop) -> None:
    """线程安全的 Queue 推送。

    Crew 执行在 thread pool 中运行，不在事件循环线程，必须用
    call_soon_threadsafe 投递到事件循环线程 push，避免数据竞争。
    队列有界（A3）：满时丢最旧事件，防止慢客户端拖爆内存。
    """
    loop.call_soon_threadsafe(try_put, queue, evt)


def wrap_tool_with_events(
    tool: BaseTool,
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
    agent_role: str = "Agent",
) -> BaseTool:
    """包装一个 CrewAI 工具，使其在执行前后推送 tool_call / tool_result 事件。

    通过 monkey-patch tool._run 实现，保留原逻辑不变。
    """
    original_run = tool._run
    tool_name = tool.name

    # 重试风暴护栏状态（闭包级：本 wrapped 实例生命周期内累计）
    _streak_sig: tuple | None = None
    _streak_count = 0

    def _escalation_note(sig: tuple) -> str | None:
        """记录一次失败并返回升级指令（未达阈值返回 None）。"""
        nonlocal _streak_sig, _streak_count
        if sig == _streak_sig:
            _streak_count += 1
        else:
            _streak_sig = sig
            _streak_count = 1
        if _streak_count >= _ESCALATE_AFTER:
            return _ESCALATION_NOTE.format(n=_streak_count)
        return None

    def _reset_streak() -> None:
        nonlocal _streak_sig, _streak_count
        _streak_sig = None
        _streak_count = 0

    def wrapped_run(*args: Any, **kwargs: Any) -> str:
        # A1 取消检查点：客户端断连后不再启动新的工具调用（RunCancelledError
        # 是 BaseException，不会被下方 except Exception 转成"工具报错"结果）
        raise_if_cancelled()
        # A2 预算检查点（30s 节流）：长跑 Crew 中途超预算时终止后续工具调用。
        # BudgetExceededRunError 是 BaseException（同取消语义），穿透 CrewAI
        # 工具异常兜底直达 producer，由其分类为 budget_exceeded 事件。
        check_budget_checkpoint()

        # 推送 tool_call 开始事件
        call_input = _truncate(kwargs if kwargs else (args[0] if args else ""))
        _safe_put(
            queue,
            AgentEvent(
                type="tool_call",
                agent=agent_role,
                tool=tool_name,
                input=call_input,
            ),
            loop,
        )

        _t0 = time.perf_counter()
        try:
            result = original_run(*args, **kwargs)
        except RunCancelledError:
            raise  # 取消向上传播，不计为工具失败
        except Exception as e:
            record_tool_call(tool_name, time.perf_counter() - _t0, ok=False)
            note = _escalation_note(("exc", type(e).__name__, str(e)[:_SIG_CHARS]))
            msg = f"工具执行出错: {e}"
            if note:
                msg += note
            # 工具异常也作为结果推送，便于前端展示
            _safe_put(
                queue,
                AgentEvent(
                    type="tool_result",
                    agent=agent_role,
                    tool=tool_name,
                    output=msg,
                ),
                loop,
            )
            if note:
                # 升级指令要送达 LLM：CrewAI 会把异常字符串化成工具结果，
                # 换成带指令的 RuntimeError（保留原始异常为 __cause__）
                raise RuntimeError(msg) from e
            raise

        record_tool_call(tool_name, time.perf_counter() - _t0, ok=True)

        # 重试风暴护栏：以返回值形式报告的失败（错误：/拒绝…）同样计数
        if isinstance(result, str) and _is_failure_text(result):
            note = _escalation_note(("ret", result[:_SIG_CHARS]))
            if note:
                result = result + note
        else:
            _reset_streak()

        # 推送 tool_result 完成事件
        _safe_put(
            queue,
            AgentEvent(
                type="tool_result",
                agent=agent_role,
                tool=tool_name,
                output=_truncate(result),
            ),
            loop,
        )
        return result

    tool._run = wrapped_run  # type: ignore[method-assign]
    return tool
