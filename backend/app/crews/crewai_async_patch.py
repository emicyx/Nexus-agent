"""CrewAI 原生工具执行 offload patch。

背景：CrewAI 1.9.3 的原生 function-calling 异步循环
（CrewAgentExecutor._ainvoke_loop_native_tools）在事件循环内**同步内联**调用
_handle_native_tool_calls（内部直接 `tool.run(**args)`）。同步工具一旦阻塞
（HITL 忙等 60s / fetch_url 90s / playwright 渲染），整个 FastAPI 事件循环被冻结，
聊天期间其他 HTTP 请求超时；playwright_tools 在 loop 内调 sync_playwright() 会直接崩溃。

方案：把 `_handle_native_tool_calls` 整体丢进 worker 线程（asyncio.to_thread 会复制
contextvars，保证 delegation 子 agent 的同步 llm.call 仍能读到 _stream_ctx 流式输出）。
工具本身不改一行（CrewAI 原生路径加 _arun 无效且会因 BaseTool.run 内 asyncio.run 崩溃）。

幂等 + 版本守卫：crewai 版本漂移或源码变化时跳过并告警，不破坏行为。
与 factory.py 的 _apply_ltm_async_patch 同风格。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from crewai import __version__ as _crewai_version
from crewai.agents.parser import AgentFinish
from pydantic import BaseModel
from crewai.utilities.agent_utils import (
    aget_llm_response,
    convert_tools_to_openai_schema,
    enforce_rpm_limit,
    handle_context_length,
    handle_max_iterations_exceeded,
    handle_unknown_error,
    has_reached_max_iterations,
    is_context_length_exceeded,
)

logger = logging.getLogger("crews.crewai_async_patch")

_patch_applied = False
_EXPECTED_VERSION = "1.9.3"
_TARGET_METHOD = "_ainvoke_loop_native_tools"
# 用于 getsource 守卫的关键行（确定 patch 目标仍存在于源码中）
_SENTINEL = "tool_finish = self._handle_native_tool_calls("


async def _ainvoke_loop_native_tools_async(self: Any) -> AgentFinish:
    """近拷贝 crewai 1.9.3 CrewAgentExecutor._ainvoke_loop_native_tools。

    与上游源码逐行一致，唯一区别：工具处理由同步内联改为
    `await asyncio.to_thread(self._handle_native_tool_calls, ...)`，
    使全部工具在 worker 线程执行，事件循环不被阻塞；HITL 阻塞语义保持
    （Agent 仍等待 future，只是等待发生在 worker 线程）。
    """
    if not self.original_tools:
        return await self._ainvoke_loop_native_no_tools()

    openai_tools, available_functions = convert_tools_to_openai_schema(
        self.original_tools
    )

    while True:
        try:
            if has_reached_max_iterations(self.iterations, self.max_iter):
                formatted_answer = handle_max_iterations_exceeded(
                    None,
                    printer=self._printer,
                    i18n=self._i18n,
                    messages=self.messages,
                    llm=self.llm,
                    callbacks=self.callbacks,
                    verbose=self.agent.verbose,
                )
                self._show_logs(formatted_answer)
                return formatted_answer

            enforce_rpm_limit(self.request_within_rpm_limit)

            # 让 LLM 返回原始 tool_calls（available_functions=None），由 executor 执行
            answer = await aget_llm_response(
                llm=self.llm,
                messages=self.messages,
                callbacks=self.callbacks,
                printer=self._printer,
                tools=openai_tools,
                available_functions=None,
                from_task=self.task,
                from_agent=self.agent,
                response_model=self.response_model,
                executor_context=self,
                verbose=self.agent.verbose,
            )
            # Check if the response is a list of tool calls
            if (
                isinstance(answer, list)
                and answer
                and self._is_tool_call_list(answer)
            ):
                # ── CHANGED vs crewai 1.9.3：工具处理 offload 到 worker 线程 ──
                # asyncio.to_thread 复制 contextvars（含 _stream_ctx），
                # 保证 delegation 子 agent 的同步 llm.call 仍能流式输出。
                tool_finish = await asyncio.to_thread(
                    self._handle_native_tool_calls, answer, available_functions
                )
                # ────────────────────────────────────────────────────────────
                # If tool has result_as_answer=True, return immediately
                if tool_finish is not None:
                    return tool_finish
                # Continue loop to let LLM analyze results and decide next steps
                continue

            # Text or other response - handle as potential final answer
            if isinstance(answer, str):
                formatted_answer = AgentFinish(
                    thought="",
                    output=answer,
                    text=answer,
                )
                self._invoke_step_callback(formatted_answer)
                self._append_message(answer)  # Save final answer to messages
                self._show_logs(formatted_answer)
                return formatted_answer

            if isinstance(answer, BaseModel):
                output_json = answer.model_dump_json()
                formatted_answer = AgentFinish(
                    thought="",
                    output=answer,
                    text=output_json,
                )
                self._invoke_step_callback(formatted_answer)
                self._append_message(output_json)
                self._show_logs(formatted_answer)
                return formatted_answer

            # Unexpected response type, treat as final answer
            formatted_answer = AgentFinish(
                thought="",
                output=str(answer),
                text=str(answer),
            )
            self._invoke_step_callback(formatted_answer)
            self._append_message(str(answer))  # Save final answer to messages
            self._show_logs(formatted_answer)
            return formatted_answer

        except Exception as e:
            if e.__class__.__module__.startswith("litellm"):
                raise e
            if is_context_length_exceeded(e):
                handle_context_length(
                    respect_context_window=self.respect_context_window,
                    printer=self._printer,
                    messages=self.messages,
                    llm=self.llm,
                    callbacks=self.callbacks,
                    i18n=self._i18n,
                    verbose=self.agent.verbose,
                )
                continue
            handle_unknown_error(self._printer, e, verbose=self.agent.verbose)
            raise e
        finally:
            self.iterations += 1


def apply_async_tool_patch() -> bool:
    """幂等替换 CrewAgentExecutor._ainvoke_loop_native_tools。

    Returns:
        True 表示已应用或已应用过；False 表示因版本/源码漂移跳过。
    """
    global _patch_applied
    if _patch_applied:
        return True

    if _crewai_version != _EXPECTED_VERSION:
        logger.warning(
            "crewai version %s != expected %s；SKIP 原生工具异步 patch "
            "（工具仍可能阻塞事件循环）",
            _crewai_version,
            _EXPECTED_VERSION,
        )
        return False

    try:
        from crewai.agents.crew_agent_executor import CrewAgentExecutor

        src = inspect.getsource(CrewAgentExecutor._ainvoke_loop_native_tools)
        if _SENTINEL not in src:
            logger.warning(
                "crewai %s._ainvoke_loop_native_tools 源码已变化，缺少标记行 %r；SKIP patch",
                _EXPECTED_VERSION,
                _SENTINEL,
            )
            return False
        setattr(
            CrewAgentExecutor,
            _TARGET_METHOD,
            _ainvoke_loop_native_tools_async,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("apply native-tool async patch 失败: %s", e)
        return False

    _patch_applied = True
    logger.info("crewai 原生工具异步 patch 已应用（工具在 worker 线程执行，事件循环不被阻塞）")
    return True


def is_applied() -> bool:
    """供回归测试断言 patch 是否生效。"""
    return _patch_applied


# 模块加载时自动应用（幂等）
apply_async_tool_patch()
