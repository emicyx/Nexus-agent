"""Hook 注册表 - hook_key → Hook 类的映射 + 实例化工厂

新增 hook 时：
1) 实现 Hook 类（继承 BaseToolHook）
2) 在 HOOK_REGISTRY 登记 hook_key
3) 在 instantiate_hook 加 if 分支处理特殊依赖注入（如 HumanApprovalTool 的事件队列）
4) 在 ToolConfig.config_json 声明 {"hooks": [{"key": "...", "config": {...}}]}

对比 tool_registry.py 的设计模式：tool_key → BaseTool 类，相同思路。
"""
import asyncio
import logging
from typing import Any, Type

from app.crews.tool_hooks import BaseToolHook, HitlPreApprovalHook
from app.tools.human_approval_tool import HumanApprovalTool

logger = logging.getLogger("hook_registry")

# hook_key → Hook 类
HOOK_REGISTRY: dict[str, Type[BaseToolHook]] = {
    "hitl_pre_approval": HitlPreApprovalHook,
    # 未来扩展点：
    # "audit_log": AuditLogHook,
    # "rate_limit": RateLimitHook,
    # "input_validation": InputValidationHook,
    # "output_sanitize": OutputSanitizeHook,
    # "cache": CacheHook,
    # "metrics": MetricsHook,
}


def instantiate_hook(
    hook_key: str,
    config: dict,
    queue: Any,
    loop: asyncio.AbstractEventLoop,
    agent_role: str = "Agent",
) -> BaseToolHook | None:
    """根据 hook_key 实例化 hook，注入共享依赖（queue/loop/agent_role）。

    特殊 hook（如 hitl_pre_approval 需要内部实例化 HumanApprovalTool 并绑定事件队列）
    走 if 分支显式构造；通用 hook 走末尾 cls(**config)。

    Args:
        hook_key: HOOK_REGISTRY 中的键
        config: hook 配置 dict（来自 ToolConfig.config_json.hooks[].config）
        queue: asyncio.Queue，用于 SSE 事件推送
        loop: asyncio.AbstractEventLoop，用于线程安全推送
        agent_role: 当前 agent 的 role 名，供 hook 标识来源

    Returns:
        BaseToolHook 实例；hook_key 未注册时返回 None 并打印警告
    """
    config = config or {}

    if hook_key == "hitl_pre_approval":
        # HITL hook 需要内部实例化 HumanApprovalTool 并绑定事件队列
        approval_tool = HumanApprovalTool()
        approval_tool.bind_event_emitter(queue, loop, agent_role=agent_role)
        return HitlPreApprovalHook(
            approval_tool=approval_tool,
            risk_level=config.get("risk_level", "medium"),
            action_template=config.get("action_template"),
            reason=config.get("reason", ""),
            args_preview_limit=config.get("args_preview_limit", 500),
        )

    # 通用构造：用 config dict 作为 kwargs 调用构造函数
    cls = HOOK_REGISTRY.get(hook_key)
    if cls is None:
        logger.warning(
            "hook_key '%s' 未注册，跳过。已注册 hooks: %s",
            hook_key, list(HOOK_REGISTRY.keys()),
        )
        return None
    try:
        return cls(**config)  # type: ignore[call-arg]
    except TypeError as e:
        logger.error(
            "hook_instantiate_failed key=%s config=%s err=%s",
            hook_key, config, e,
        )
        return None


# 可选项：供前端下拉选择 hook
HOOK_OPTIONS = [{"key": k, "label": k} for k in sorted(HOOK_REGISTRY.keys())]
