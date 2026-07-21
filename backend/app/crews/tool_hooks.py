"""通用 Tool Hook 机制 - 为所有 CrewAI 工具提供可复用的 pre/post hook。

设计原则：
- 单一职责：Hook 与 Tool 解耦，Hook 之间互不依赖
- 声明式启用：通过 ToolConfig.config_json.hooks 列表声明，不挂 hook 的工具零影响
- 中间件链式语义：pre-hook 顺序执行，首个 short_circuit=True 即短路；post-hook 逆序执行
- 复用现有同步模式：HITL hook 内部直接调 HumanApprovalTool._run（同步 Redis + SSE），不重写

装配顺序（factory.py）：
    tool._run  ←  wrap_tool_with_hooks（业务 hooks，内层）
              ←  wrap_tool_with_events（SSE 事件，外层）
即使 hook 短路，外层事件包装器仍会推送 tool_call 与 tool_result（result=取消原因）。
"""
import asyncio
import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Optional

from crewai.tools import BaseTool

from app.tools.human_approval_tool import HumanApprovalTool

logger = logging.getLogger("tool_hooks")


@dataclass
class HookContext:
    """Hook 调用上下文，工厂装配时注入 queue/loop/agent_role，每次工具调用复用。

    call_args/call_kwargs 是本次 tool._run 的原始入参，供 hook 检查/构造审批文本。
    """
    queue: Any                  # asyncio.Queue[AgentEvent | None]
    loop: asyncio.AbstractEventLoop
    agent_role: str
    tool_name: str
    tool_key: str
    call_args: tuple
    call_kwargs: dict


@dataclass
class HookResult:
    """pre-hook 的返回值。

    short_circuit=True 时跳过 tool._run，直接把 return_value 作为工具结果。
    return_value 为 None 时返回空字符串。
    reason 仅供日志/调试，不返回给 agent。
    """
    short_circuit: bool
    return_value: Optional[str] = None
    reason: str = ""


class BaseToolHook(ABC):
    """所有 tool hook 的抽象基类。子类按需 override before_run / after_run。

    扩展点示例：
    - HitlPreApprovalHook：调用 HumanApprovalTool 请求人类审批
    - AuditLogHook：记录工具调用审计日志（未实现）
    - RateLimitHook：限流（未实现）
    - InputValidationHook：入参校验（未实现）
    - OutputSanitizeHook：脱敏输出（未实现）
    - CacheHook：缓存幂等工具结果（未实现）
    - MetricsHook：记录调用时长/成功率（未实现）
    """

    def before_run(self, ctx: HookContext) -> Optional[HookResult]:
        """返回 None 继续执行；返回 HookResult(short_circuit=True, ...) 短路。"""
        return None

    def after_run(self, ctx: HookContext, result: str) -> str:
        """可修改 result 后返回。默认原样返回。"""
        return result


def wrap_tool_with_hooks(
    tool: BaseTool,
    hooks: list[BaseToolHook],
    queue: Any,
    loop: asyncio.AbstractEventLoop,
    agent_role: str = "Agent",
) -> BaseTool:
    """monkey-patch tool._run，按序执行 pre-hooks，逆序执行 post-hooks。

    与 wrap_tool_with_events 的关系：
    - 本函数是内层包装，处理业务 hooks（HITL 等）
    - wrap_tool_with_events 是外层包装，处理 SSE 事件
    - 装配顺序：tool._run → wrap_tool_with_hooks → wrap_tool_with_events
    - 即使 hook 短路，外层 wrap_tool_with_events 仍会推送 tool_call 与 tool_result
    """
    if not hooks:
        # 无 hook 声明，零开销直接返回
        return tool

    original_run = tool._run
    tool_name = tool.name
    # tool_key 优先取属性，回落到 tool_name（供 hook 按 key 过滤）
    tool_key = getattr(tool, "tool_key", tool_name)

    def wrapped_run(*args: Any, **kwargs: Any) -> str:
        ctx = HookContext(
            queue=queue,
            loop=loop,
            agent_role=agent_role,
            tool_name=tool_name,
            tool_key=tool_key,
            call_args=args,
            call_kwargs=kwargs,
        )
        # pre-hooks 顺序执行，首个 short_circuit=True 即短路
        for hook in hooks:
            try:
                res = hook.before_run(ctx)
            except Exception as e:
                logger.exception(
                    "hook_before_run_failed tool=%s hook=%s err=%s",
                    tool_name, hook.__class__.__name__, e,
                )
                # hook 自身异常不应阻塞工具，继续执行后续 hook 或原 _run
                continue
            if res is not None and res.short_circuit:
                logger.info(
                    "hook_short_circuit tool=%s hook=%s reason=%s",
                    tool_name, hook.__class__.__name__, res.reason[:200],
                )
                return res.return_value or ""

        # 执行原工具
        result = original_run(*args, **kwargs)

        # post-hooks 逆序执行（中间件语义）
        for hook in reversed(hooks):
            try:
                result = hook.after_run(ctx, result)
            except Exception as e:
                logger.exception(
                    "hook_after_run_failed tool=%s hook=%s err=%s",
                    tool_name, hook.__class__.__name__, e,
                )
                # post-hook 异常不阻塞，继续用原 result
                continue
        return result

    tool._run = wrapped_run  # type: ignore[method-assign]
    return tool


class HitlPreApprovalHook(BaseToolHook):
    """HITL 前置审批 hook。

    复用 HumanApprovalTool._run（同步 Redis 轮询 + SSE 事件）。
    工具 _run 前先推送 approval_requested 事件到前端，等待人类决策。
    - 用户批准：继续执行原工具
    - 用户拒绝/超时：短路返回取消原因，不执行原工具

    config 项：
        risk_level: "low"|"medium"|"high"|"critical"（默认 medium）
        action_template: 自定义 action 文本模板，支持 {tool_name} {args_preview}
        reason: 审批理由（可选）
        args_preview_limit: 入参预览截断长度（默认 500，避免 SSE 消息过大）
    """

    DEFAULT_ACTION_TEMPLATE = (
        "即将执行工具 {tool_name}。入参预览：\n{args_preview}"
    )

    def __init__(
        self,
        approval_tool: HumanApprovalTool,
        risk_level: str = "medium",
        action_template: Optional[str] = None,
        reason: str = "",
        args_preview_limit: int = 500,
    ):
        self.approval_tool = approval_tool
        self.risk_level = risk_level
        self.action_template = action_template or self.DEFAULT_ACTION_TEMPLATE
        self.reason = reason
        self.args_preview_limit = args_preview_limit

    def _build_preview(self, ctx: HookContext) -> str:
        """从调用入参构造预览文本。优先取 kwargs（命名参数更可读），其次取首个位置参数。"""
        try:
            if ctx.call_kwargs:
                # kwargs 更可读，但可能很长，截断
                preview_src = str(ctx.call_kwargs)
            elif ctx.call_args:
                preview_src = str(ctx.call_args[0])
            else:
                preview_src = ""
        except Exception:
            preview_src = f"args={ctx.call_args} kwargs={ctx.call_kwargs}"
        return preview_src[: self.args_preview_limit]

    def before_run(self, ctx: HookContext) -> Optional[HookResult]:
        action = self.action_template.format(
            tool_name=ctx.tool_name,
            args_preview=self._build_preview(ctx),
        )
        try:
            approval_result = self.approval_tool._run(
                action=action,
                risk_level=self.risk_level,
                reason=self.reason,
            )
        except Exception as e:
            logger.exception(
                "hitl_hook_approval_failed tool=%s err=%s", ctx.tool_name, e,
            )
            return HookResult(
                short_circuit=True,
                return_value=f"工具 {ctx.tool_name} 已取消：审批工具异常 {e}",
                reason=f"approval_exception: {e}",
            )

        # HumanApprovalTool 批准时返回字符串以"已获人类批准"开头
        if approval_result.startswith("已获人类批准"):
            logger.info(
                "hitl_hook_approved tool=%s agent=%s",
                ctx.tool_name, ctx.agent_role,
            )
            return None  # 继续 tool._run

        # 拒绝/超时/Redis 失效：短路
        logger.info(
            "hitl_hook_short_circuit tool=%s result=%s",
            ctx.tool_name, approval_result[:200],
        )
        return HookResult(
            short_circuit=True,
            return_value=f"工具 {ctx.tool_name} 已取消：{approval_result}",
            reason=approval_result,
        )
