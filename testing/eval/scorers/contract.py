"""契约类评分器：SSE 契约 / 黄金短语 / pydantic 校验 / 泄漏正则 / DB 对账 / 沙箱文件。

全部为纯函数，只读 RunRecord。快照缺失时返回 judge_error（评分器故障，
不是 agent 失败）——这是"校验评分器本身"的机械落点。
"""
from __future__ import annotations

import re

from testing.eval.scorers.base import REGISTRY, RunRecord, Score, register


def _final_or_empty(run: RunRecord) -> str:
    # runner 会填充 final_answer 字段；直接构造 RunRecord 的调用方（单测/回放）可只带事件
    if run.final_answer:
        return run.final_answer
    evt = run.find_event("final_answer")
    return (evt or {}).get("data", {}).get("content", "") or ""


@register("sse_contract")
def sse_contract(spec: dict, run: RunRecord) -> Score:
    """SSE 执行契约：必含事件、末条哨兵、禁发事件。

    spec: {must_events?: [type], last_event?: "done", forbidden_events?: [type]}
    """
    name = "sse_contract"
    types = run.event_types()
    must = spec.get("must_events", ["final_answer"])
    missing = [t for t in must if t not in types]
    if missing:
        return Score(name, "fail", evidence=f"缺必含事件 {missing}，实际序列 {types[:20]}")
    last = spec.get("last_event", "done")
    if types and types[-1] != last:
        return Score(name, "fail", evidence=f"末条事件为 {types[-1]!r} 而非 {last!r}")
    forbidden = [t for t in spec.get("forbidden_events", []) if t in types]
    if forbidden:
        return Score(name, "fail", evidence=f"出现禁发事件 {forbidden}")
    return Score(name, "pass", evidence=f"事件序列契约满足（{len(types)} 事件）")


@register("golden_phrase")
def golden_phrase(spec: dict, run: RunRecord) -> Score:
    """黄金短语命中：any-of 列表 + 空白归一化（回答转述鲁棒）。

    spec: {phrase: str} 或 {phrases: [str, ...]}（任一命中即 pass）
    判定优先级：final_answer 命中 → pass；仅 tool_result 命中 → partial
    （知识已检索到手、生成层丢失）；均未命中 → fail。

    校准教训（2026-09-09 首跑）：逐字匹配对生成层过于严苛——正确转述
    （如"滑窗只留最近 6 条" vs 语料"滑窗 6 条"）会被误判。故：
    ① 比较前剥离全部空白；② 语义等价的自然说法进 phrases 列表。
    """
    name = "golden_phrase"
    phrases = spec.get("phrases") or ([spec["phrase"]] if spec.get("phrase") else [])
    if not phrases:
        return Score(name, "judge_error", evidence="spec 缺 phrase/phrases")

    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    answer_n, phrases_n = norm(_final_or_empty(run)), [norm(p) for p in phrases]
    for p_raw, p_n in zip(phrases, phrases_n):
        if p_n and p_n in answer_n:
            return Score(name, "pass", value=1.0, evidence=f"回答命中短语 {p_raw!r}（空白归一化后匹配）")
    # 检索命中但回答没用上：知识已到手，生成层丢失 → 部分
    for e in run.events:
        if e.get("type") == "tool_result":
            out_n = norm(str((e.get("data") or {}).get("output", "")))
            if any(p_n and p_n in out_n for p_n in phrases_n):
                return Score(name, "partial", value=0.5,
                             evidence="短语出现在 tool_result 但 final_answer 未使用（生成层丢失）")
    return Score(name, "fail", value=0.0, evidence=f"回答与工具结果均未命中 {phrases!r}")


@register("pydantic_valid")
def pydantic_valid(spec: dict, run: RunRecord) -> Score:
    """结构化输出校验：task_completed 事件的 pydantic_valid 字段是后端现成判定。

    spec: {expect?: true}
    """
    name = "pydantic_valid"
    expect = spec.get("expect", True)
    completed = [e for e in run.events if e.get("type") == "task_completed"]
    if not completed:
        # hierarchical 场景才可靠发该事件；没有事件时判 judge_error 而非 fail，
        # 避免把"事件未启用"误判为本体失败
        return Score(name, "judge_error", evidence="无 task_completed 事件（sequential 流程或事件未启用）")
    results = [(e.get("data") or {}).get("output", {}).get("pydantic_valid") for e in completed]
    if all(r == expect for r in results):
        return Score(name, "pass", evidence=f"{len(results)} 个 task_completed 均为 pydantic_valid={expect}")
    bad = [r for r in results if r != expect]
    return Score(name, "fail", evidence=f"pydantic_valid 期望 {expect}，实际存在 {bad}")


@register("regex_not_match")
def regex_not_match(spec: dict, run: RunRecord) -> Score:
    """泄漏/禁词断言：final_answer 不得匹配给定正则。

    用途：tool-call JSON 泄漏（2026-08-25 事故变体）、敏感串泄漏。
    spec: {pattern: str, where?: "final_answer"}
    """
    name = "regex_not_match"
    pattern = spec.get("pattern")
    if not pattern:
        return Score(name, "judge_error", evidence="spec 缺 pattern")
    text = _final_or_empty(run)
    m = re.search(pattern, text)
    if m:
        i = max(0, m.start() - 40)
        return Score(name, "fail", evidence=f"命中禁用模式 {pattern!r}: ...{text[i:m.end() + 40]}...")
    return Score(name, "pass", evidence=f"未命中禁用模式 {pattern!r}")


@register("db_assert")
def db_assert(spec: dict, run: RunRecord) -> Score:
    """环境对账：以 before/after 快照 diff 判定（防自我报告）。

    spec 两种形态：
      单指标: {metric: "document_count"|"chunk_count", op?: ">=", value: int}
      多环节: {stages: [{name, metric, op, value}, ...]} → 按环节计部分分
    快照键: snapshots["before"|"after"]["db"] = {"documents": [{id, name, chunk_count}]}
    """
    name = "db_assert"
    before = run.snapshots.get("before", {}).get("db")
    after = run.snapshots.get("after", {}).get("db")
    if not before or not after:
        return Score(name, "judge_error", evidence="db 快照缺失（EnvAdapter 未采集或 API 不可用）")

    def metric_value(snap: dict, metric: str) -> int | None:
        docs = snap.get("documents", [])
        if metric == "document_count":
            return len(docs)
        if metric == "chunk_count":
            counts = [d.get("chunk_count") for d in docs if d.get("chunk_count") is not None]
            return sum(counts) if counts else None
        return None

    def check_one(metric: str, op: str, value: int) -> tuple[bool, str]:
        b, a = metric_value(before, metric), metric_value(after, metric)
        if b is None or a is None:
            return False, f"指标 {metric} 快照不可得（chunk_count 需 EnvAdapter 支持明细）"
        delta = a - b
        ok = {"<=": delta <= value, ">=": delta >= value, "==": delta == value}.get(op, delta >= value)
        return ok, f"{metric} 增量 {delta}（{b}→{a}），断言 {op} {value}"

    stages = spec.get("stages")
    if stages:
        results = []
        for st in stages:
            ok, ev = check_one(st.get("metric", "document_count"), st.get("op", ">="), st.get("value", 1))
            results.append((st.get("name", st.get("metric", "?")), ok, ev))
        passed = sum(1 for _, ok, _ in results if ok)
        verdict = "pass" if passed == len(results) else ("partial" if passed else "fail")
        evidence = "; ".join(f"{n}:{'✓' if ok else '✗'}({ev})" for n, ok, ev in results)
        return Score(name, verdict, value=passed / len(results) if results else None, evidence=evidence)
    metric = spec.get("metric", "document_count")
    ok, ev = check_one(metric, spec.get("op", ">="), spec.get("value", 1))
    return Score(name, "pass" if ok else "fail", value=1.0 if ok else 0.0, evidence=ev)


@register("sandbox_file")
def sandbox_file(spec: dict, run: RunRecord) -> Score:
    """沙箱产物核验：outputs/** 下文件存在性/大小（以文件系统为准）。

    spec: {path: "report.md"（相对 outputs/）, exists?: true, min_bytes?: int}
    """
    name = "sandbox_file"
    rel = spec.get("path")
    if not rel:
        return Score(name, "judge_error", evidence="spec 缺 path")
    after = run.snapshots.get("after", {}).get("sandbox")
    if after is None:
        return Score(name, "judge_error", evidence="sandbox 快照缺失（EnvAdapter 未采集）")
    want_exists = spec.get("exists", True)
    files = {f.get("path", "").replace("\\", "/").lstrip("/"): f for f in after.get("files", [])}
    hit = files.get(rel.replace("\\", "/").lstrip("/"))
    if want_exists:
        if hit is None:
            all_paths = sorted(files)[:10]
            return Score(name, "fail", evidence=f"outputs/{rel} 不存在；现有: {all_paths}")
        min_b = spec.get("min_bytes")
        size = hit.get("size", 0) or 0
        if min_b is not None and size < min_b:
            return Score(name, "fail", evidence=f"outputs/{rel} 仅 {size}B < {min_b}B")
        return Score(name, "pass", evidence=f"outputs/{rel} 存在（{size}B）")
    if hit is not None:
        return Score(name, "fail", evidence=f"outputs/{rel} 不应存在却存在")
    return Score(name, "pass", evidence=f"outputs/{rel} 确不存在")


def available_types() -> list[str]:
    return sorted(REGISTRY)
