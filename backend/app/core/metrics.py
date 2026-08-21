"""/metrics 轻量指标（上线欠账 B3）。

不引入 prometheus-client：手写 Prometheus 文本 exposition 格式，
采集器（Prometheus / VictoriaMetrics / 云监控）直接抓 /metrics 即可。

覆盖：
- SSE 并发连接数 / 每会话队列深度（来自 run_control 活跃运行注册表）
- 队列满丢弃事件总数（events.try_put 的背压压力观测）
- 每工具调用次数与 P95 延迟（tool_events 包装器记录）
- 当日 token 消耗 / 预算上限 / 每会话消耗（token_budget）
"""
from __future__ import annotations

import math
import threading
from collections import defaultdict, deque

from app.core import run_control
from app.core.events import dropped_event_count
from app.core.token_budget import get_budget, get_session_totals, get_today_total

_lock = threading.Lock()
_tool_calls: dict[str, int] = defaultdict(int)
_tool_errors: dict[str, int] = defaultdict(int)
# 最近 512 次延迟的滑动窗口，P95 抗抖动够用
_tool_latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=512))


def record_tool_call(tool: str, duration_seconds: float, ok: bool = True) -> None:
    """tool_events 包装器在每次工具调用结束时调用（worker 线程安全）。"""
    with _lock:
        _tool_calls[tool] += 1
        if not ok:
            _tool_errors[tool] += 1
        _tool_latencies[tool].append(max(0.0, duration_seconds))


def _p95(values: deque[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[idx]


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics() -> str:
    """渲染 Prometheus 文本格式（GET /metrics 直接返回）。"""
    lines: list[str] = []

    def gauge(name: str, help_text: str, value: float, labels: dict[str, str] | None = None) -> None:
        label_str = ""
        if labels:
            inner = ",".join(f'{k}="{_escape_label(v)}"' for k, v in labels.items())
            label_str = "{" + inner + "}"
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{label_str} {value}")

    def counter(name: str, help_text: str, value: float, labels: dict[str, str] | None = None) -> None:
        gauge(name, help_text, value, labels)  # 文本格式里 counter 同 gauge 写法

    gauge(
        "sse_active_connections",
        "当前活跃的 SSE 聊天运行数（含正在收尾的）",
        run_control.active_run_count(),
    )
    for session, depth in run_control.snapshot_queue_depths():
        gauge("sse_queue_depth", "每会话 SSE 事件队列当前深度", depth, {"session": session})
    counter("sse_dropped_events_total", "队列满被丢弃的 SSE 事件总数", dropped_event_count())

    with _lock:
        tools = sorted(_tool_calls.keys())
        calls_snapshot = {t: _tool_calls[t] for t in tools}
        errors_snapshot = {t: _tool_errors.get(t, 0) for t in tools}
        p95_snapshot = {t: _p95(_tool_latencies[t]) for t in tools}
    for tool, calls in calls_snapshot.items():
        counter("tool_calls_total", "工具调用次数", calls, {"tool": tool})
    for tool, errors in errors_snapshot.items():
        counter("tool_errors_total", "工具调用失败次数", errors, {"tool": tool})
    for tool, p95 in p95_snapshot.items():
        gauge("tool_latency_p95_seconds", "工具调用 P95 延迟（秒）", round(p95, 6), {"tool": tool})

    counter("token_usage_today_total", "当日 LLM token 消耗（prompt+completion）", get_today_total())
    counter("token_budget_limit", "当日 token 预算（0=不限）", get_budget())
    for session, total in get_session_totals().items():
        counter("token_usage_session_total", "每会话 LLM token 消耗", total, {"session": session})

    return "\n".join(lines) + "\n"


def reset_for_tests() -> None:
    with _lock:
        _tool_calls.clear()
        _tool_errors.clear()
        _tool_latencies.clear()
