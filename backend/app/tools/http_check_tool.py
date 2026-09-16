"""http_check 巡检工具（v2 S2）。

安全不变量 3（执行计划 §3.2）：目标只来自 env MONITOR_TARGETS——工具参数
只有可选的 name 过滤，**没有 URL 参数**，模型无法自由指定目标（注入打不开
SSRF 面）。私网目标由运维在 env 白名单里显式声明，因此直连 requests、
不走 fetch_url/net_guard 路径，也不为巡检放松 net_guard 全局规则。

双重用途：
- agent 工具：巡检 agent 对可疑目标按 name 复查（初诊用）；
- job_runner 确定性扫测：check_all_targets() 在渲染输入模板前对全部目标
  扫一遍，结构化结果进 {{targets_report}} 与 job_runs.result_summary
  （state_change 检测的数据源是确定性扫测，不是 LLM 输出）。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger("tools.http_check")

_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_EXPECT_STATUS = 200


@dataclass(frozen=True)
class MonitorTarget:
    name: str
    url: str
    expect_status: int
    timeout_s: float


def parse_monitor_targets(raw: str | None = None) -> list[MonitorTarget]:
    """解析并校验 MONITOR_TARGETS。非法条目抛 ValueError（配置错误必须显性）。"""
    raw = raw if raw is not None else settings.MONITOR_TARGETS
    raw = (raw or "").strip() or "[]"
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"MONITOR_TARGETS 不是合法 JSON: {e}") from e
    if not isinstance(arr, list):
        raise ValueError("MONITOR_TARGETS 必须是 JSON 数组")
    targets: list[MonitorTarget] = []
    seen: set[str] = set()
    for i, item in enumerate(arr):
        if not isinstance(item, dict):
            raise ValueError(f"MONITOR_TARGETS[{i}] 必须是对象")
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name:
            raise ValueError(f"MONITOR_TARGETS[{i}] 缺 name")
        if name in seen:
            raise ValueError(f"MONITOR_TARGETS name 重复: {name}")
        seen.add(name)
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"MONITOR_TARGETS[{name}] url 必须以 http(s):// 开头，收到: {url}")
        try:
            expect_status = int(item.get("expect_status", _DEFAULT_EXPECT_STATUS))
        except (TypeError, ValueError) as e:
            raise ValueError(f"MONITOR_TARGETS[{name}] expect_status 非法") from e
        try:
            timeout_s = float(item.get("timeout_s", _DEFAULT_TIMEOUT_S))
        except (TypeError, ValueError) as e:
            raise ValueError(f"MONITOR_TARGETS[{name}] timeout_s 非法") from e
        if timeout_s <= 0 or timeout_s > 60:
            raise ValueError(f"MONITOR_TARGETS[{name}] timeout_s 需在 (0, 60] 秒")
        targets.append(MonitorTarget(name=name, url=url, expect_status=expect_status,
                                     timeout_s=timeout_s))
    return targets


def check_target(t: MonitorTarget) -> dict[str, Any]:
    """探测单个目标（stream=True 只取状态行，不拉正文）。阻塞 IO，调用方自行安排线程。"""
    started = time.perf_counter()
    status: int | None = None
    error: str | None = None
    try:
        with requests.get(t.url, timeout=t.timeout_s, stream=True,
                          headers={"User-Agent": "NexusOpsPatrol/1.0"}) as resp:
            status = resp.status_code
    except requests.RequestException as e:
        error = f"{type(e).__name__}: {e}"[:300]
    latency_ms = int((time.perf_counter() - started) * 1000)
    up = error is None and status == t.expect_status
    return {
        "name": t.name,
        "url": t.url,
        "status": status,
        "expect_status": t.expect_status,
        "latency_ms": latency_ms,
        "error": error,
        "up": up,
    }


def check_all_targets(name_filter: str | None = None) -> list[dict[str, Any]]:
    """顺序扫测全部（或指定 name 的）目标，返回结构化结果。"""
    try:
        targets = parse_monitor_targets()
    except ValueError as e:
        # 配置错误的确定性输出（不抛给调度循环；job_run 会以 failed 落账）
        return [{"name": "(config)", "url": "", "status": None, "expect_status": None,
                 "latency_ms": 0, "error": f"MONITOR_TARGETS 配置错误: {e}", "up": False}]
    if name_filter:
        name_filter = name_filter.strip()
        targets = [t for t in targets if t.name == name_filter]
        if not targets:
            return [{"name": name_filter, "url": "", "status": None, "expect_status": None,
                     "latency_ms": 0, "error": f"MONITOR_TARGETS 中不存在名为 {name_filter!r} 的目标",
                     "up": False}]
    return [check_target(t) for t in targets]


def format_results(results: list[dict[str, Any]]) -> str:
    """结构化结果 → LLM 可读文本（同一格式也用于 {{targets_report}} 渲染）。"""
    lines = []
    for r in results:
        if r["up"]:
            lines.append(
                f"- {r['name']}: UP（status={r['status']}，{r['latency_ms']}ms，{r['url']}）"
            )
        else:
            err = r["error"] or f"status={r['status']} != 期望 {r['expect_status']}"
            lines.append(f"- {r['name']}: DOWN（{err}，{r['latency_ms']}ms，{r['url']}）")
    return "\n".join(lines) if lines else "（无监控目标）"


class HttpCheckInput(BaseModel):
    """注意：无 url 参数——目标清单锁定在 env MONITOR_TARGETS（安全不变量 3）。"""

    name: str = Field(
        "",
        description=(
            "可选：只复查指定名称的目标（名称必须来自任务输入里列出的监控目标清单）。"
            "留空 = 检查全部目标。不接受 URL。"
        ),
    )


class HttpCheckTool(BaseTool):
    """巡检探测工具：按配置清单探测 HTTP 目标健康状态。"""

    name: str = "http_check"
    description: str = (
        "巡检探测工具：探测监控目标清单的 HTTP 健康状态，返回 name/状态码/延迟/错误。"
        "触发时机：巡检任务中对某目标复查、确认异常或恢复时使用。"
        "行为：目标清单来自系统配置（MONITOR_TARGETS），只能按 name 过滤，"
        "不能指定任意 URL。\n"
        "输出：每个目标一行 UP/DOWN 状态与状态码/延迟/错误信息。"
    )
    args_schema: type[BaseModel] = HttpCheckInput

    def _run(self, name: str = "", **kwargs: Any) -> str:
        results = check_all_targets(name or None)
        text = format_results(results)
        if name and results and not results[0]["up"] and results[0]["url"] == "":
            return f"错误：{results[0]['error']}"
        return text
