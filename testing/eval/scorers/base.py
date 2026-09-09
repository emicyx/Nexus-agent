"""评分器基座：RunRecord / Score 与注册表。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

VERDICTS = ("pass", "partial", "fail", "judge_error")


@dataclass
class Score:
    scorer: str
    verdict: str  # pass | partial | fail | judge_error
    value: float | None = None  # 部分得分（0~1），无意义时为 None
    evidence: str = ""  # 断言证据/失败原因，直接进报告


@dataclass
class RunRecord:
    """一次试验的全部证据（runner 采集，评分器只读）。

    events: SSE 事件列表 [{type, data}]
    final_answer: final_answer 事件内容（无则空串）
    elapsed: 端到端耗时秒
    snapshots: {"before": {...}, "after": {...}}，键约定：
        db      -> {"documents": [{id, name, chunk_count?}]}
        sandbox -> {"files": [{path, size}]}
    error: 环境/网络层错误（非 agent 失败），有值时该试验视为不可判
    """

    events: list[dict] = field(default_factory=list)
    final_answer: str = ""
    elapsed: float = 0.0
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None

    def event_types(self) -> list[str]:
        return [e.get("type") or "" for e in self.events]

    def find_event(self, evt_type: str) -> dict | None:
        return next((e for e in self.events if e.get("type") == evt_type), None)

    def tool_calls(self, tool: str | None = None) -> list[dict]:
        out = []
        for e in self.events:
            if e.get("type") == "tool_call" and (tool is None or e.get("data", {}).get("tool") == tool):
                out.append(e.get("data", {}))
        return out


REGISTRY: dict[str, Callable[[dict, RunRecord], Score]] = {}


def register(scorer_type: str):
    """装饰器：把评分函数登记进 REGISTRY。"""

    def deco(fn):
        if scorer_type in REGISTRY:
            raise ValueError(f"评分器类型重复注册: {scorer_type}")
        fn.scorer_type = scorer_type
        REGISTRY[scorer_type] = fn
        return fn

    return deco
