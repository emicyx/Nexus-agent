"""轨迹类评分器：在 SSE 事件流上做有限状态断言。

动机：三次线上事故（路径幻觉 / tool-call 泄漏 / 检索失败重试缺失）
全部是轨迹层失败——把事后补的机械护栏反向写成断言，护栏有回归、
行为有评分，一份投入两份产出。

规则原语（spec.rules 列表，每条带显式 kind，全部满足才 pass）：
  {kind: "expect_event", event, tool?, input_contains?: {k: 子串}, output_contains?: [子串]}
        至少出现一次；input 匹配 tool_call 的 input（dict 序列化后查子串），
        output 匹配 tool_result 的 output（如 path_guard 注入的目录清单）
  {kind: "forbidden_event", event, tool?, input_contains?}           不得出现
  {kind: "no_repeat_tool_input", tool, input_key}                    同工具同参数值不得重复
        （2026-08-26 事故：首轮检索失败必须换词/锁定 doc 再查）
  {kind: "min_calls", tool, count}                                   至少调用 count 次
        （多轮 agentic 检索行为）
"""
from __future__ import annotations

import json

from testing.eval.scorers.base import REGISTRY, RunRecord, Score, register


def _matches(data: dict, tool: str | None, input_contains: dict | None,
             output_contains: list | None) -> bool:
    if tool is not None and data.get("tool") != tool:
        return False
    if input_contains:
        inp = data.get("input")
        inp = json.dumps(inp, ensure_ascii=False) if isinstance(inp, dict) else str(inp)
        for sub in (input_contains or {}).values():
            if str(sub) not in inp:
                return False
    if output_contains:
        out = str(data.get("output", ""))
        for sub in output_contains:
            if str(sub) not in out:
                return False
    return True


@register("trajectory_rule")
def trajectory_rule(spec: dict, run: RunRecord) -> Score:
    name = "trajectory_rule"
    rules = spec.get("rules")
    if not rules:
        return Score(name, "judge_error", evidence="spec 缺 rules")

    violations: list[str] = []
    for i, r in enumerate(rules):
        kind = r.get("kind")
        if kind == "expect_event":
            evt, tool = r.get("event"), r.get("tool")
            ic, oc = r.get("input_contains"), r.get("output_contains")
            hit = any(
                e.get("type") == evt and _matches(e.get("data") or {}, tool, ic, oc)
                for e in run.events
            )
            if not hit:
                violations.append(f"rule[{i}] 期望事件未出现: {evt}" + (f" tool={tool}" if tool else "")
                                  + (f" input含{ic}" if ic else "") + (f" output含{oc}" if oc else ""))
        elif kind == "forbidden_event":
            evt, tool = r.get("event"), r.get("tool")
            ic, oc = r.get("input_contains"), r.get("output_contains")
            for e in run.events:
                if e.get("type") == evt and _matches(e.get("data") or {}, tool, ic, oc):
                    violations.append(f"rule[{i}] 禁止事件出现: {evt} tool={e.get('data', {}).get('tool')}")
                    break
        elif kind == "no_repeat_tool_input":
            tool, key = r.get("tool"), r.get("input_key")
            seen: dict[str, int] = {}
            for d in run.tool_calls(tool):
                inp = d.get("input") or {}
                val = str(inp.get(key)) if isinstance(inp, dict) else str(inp)
                seen[val] = seen.get(val, 0) + 1
            dupes = {v: c for v, c in seen.items() if c > 1}
            if dupes:
                violations.append(f"rule[{i}] 工具 {tool} 参数 {key} 重复: {list(dupes)[:3]}")
        elif kind == "min_calls":
            tool, count = r.get("tool"), int(r.get("count", 1))
            got = len(run.tool_calls(tool))
            if got < count:
                violations.append(f"rule[{i}] 工具 {tool} 调用 {got} 次 < {count} 次")
        else:
            violations.append(f"rule[{i}] 未知规则类型 {kind!r}")

    if violations:
        types = run.event_types()
        return Score(name, "fail", evidence="; ".join(violations) + f" | 实际序列 {types[:25]}")
    return Score(name, "pass", evidence=f"{len(rules)} 条轨迹规则全部满足")
