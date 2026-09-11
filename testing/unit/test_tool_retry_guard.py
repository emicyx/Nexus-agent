"""重试风暴护栏单测（backend/app/crews/tool_events.py wrap_tool_with_events）。

零网络依赖：全部用可编程 FakeTool 驱动包装器。
"""
import asyncio

import pytest
from crewai.tools import BaseTool
from pydantic import BaseModel

from app.crews.tool_events import wrap_tool_with_events


class _EmptyInput(BaseModel):
    pass


class FakeTool(BaseTool):
    """可编程工具：按脚本依次返回结果或抛异常。"""

    name: str = "fake_tool"
    description: str = "测试用可编程工具"
    args_schema: type[BaseModel] = _EmptyInput

    def _run(self, **kwargs) -> str:  # 占位实现（实例化后再按脚本覆盖）
        return ""

    def make_run(self, script):
        """script: list[str | Exception]，每次调用弹出一项（空了重复最后一项）。"""
        calls = {"i": 0}

        def _run(**kwargs):
            item = script[min(calls["i"], len(script) - 1)]
            calls["i"] += 1
            if isinstance(item, Exception):
                raise item
            return item

        return _run


def _wrap(script):
    tool = FakeTool()
    tool._run = tool.make_run(script)
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    wrapped = wrap_tool_with_events(tool, queue, loop, agent_role="测试员")
    return wrapped


def test_exception_streak_escalates_on_third():
    """同一异常连续 3 次：第 3 次起异常消息带 [系统护栏] 停止重试指令。"""
    wrapped = _wrap(
        [PermissionError("[Errno 13] Permission denied: '/app/data/outputs/x.md'")]
    )
    msgs = []
    for _ in range(3):
        with pytest.raises(Exception) as ei:
            wrapped.run()
        msgs.append(str(ei.value))
    assert "[系统护栏]" not in msgs[0]
    assert "[系统护栏]" not in msgs[1]
    assert "[系统护栏]" in msgs[2]
    assert "不要再以相同或相近参数重试" in msgs[2]


def test_error_string_streak_escalates():
    """以返回值报告的失败（拒绝写入…）连续 3 次同样升级。"""
    wrapped = _wrap(
        ["拒绝写入：路径越出沙箱被拒绝：写入 仅允许在 /app/data/outputs 内"
         "（收到 /app/data/outputs/../escape.md）"]
    )
    results = [wrapped.run() for _ in range(3)]
    assert "[系统护栏]" not in results[0]
    assert "[系统护栏]" not in results[1]
    assert "[系统护栏]" in results[2]


def test_success_resets_streak():
    """成功调用复位计数：失败×3（第 3 次升级）→ 成功 → 失败×2 不再升级。"""
    wrapped = _wrap(
        ["错误：写入失败"] * 3 + ["ok"] + ["错误：写入失败"] * 2
    )
    results = [wrapped.run() for _ in range(6)]
    assert "[系统护栏]" not in results[0]
    assert "[系统护栏]" not in results[1]
    assert "[系统护栏]" in results[2]  # 连续第 3 次触发
    assert "[系统护栏]" not in results[3]  # 成功复位
    assert "[系统护栏]" not in results[4]  # 重新计数第 1 次
    assert "[系统护栏]" not in results[5]  # 重新计数第 2 次


def test_different_failure_shape_resets_streak():
    """不同失败形态（错误文本不同）不累计为风暴。"""
    wrapped = _wrap(
        ["错误：filename 不能为空", "错误：data 不能为空", "错误：路径不存在"]
    )
    results = [wrapped.run() for _ in range(3)]
    assert all("[系统护栏]" not in r for r in results)


def test_normal_results_never_escalate():
    wrapped = _wrap(["正常结果 A", "正常结果 B"])
    results = [wrapped.run() for _ in range(2)]
    assert all("[系统护栏]" not in r for r in results)
