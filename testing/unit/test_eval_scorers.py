"""eval harness 单测：评分器（纯函数，构造 RunRecord 夹具）。"""
from testing.eval.scorers import REGISTRY
from testing.eval.scorers.base import RunRecord


def _rec(events=None, final="", snapshots=None, error=None):
    return RunRecord(events=events or [], final_answer=final,
                     snapshots=snapshots or {}, error=error)


def _ev(evt_type, **data):
    return {"type": evt_type, "data": data}


DONE = [_ev("final_answer", content="答"), _ev("done")]


def test_registry_contains_expected_types():
    assert {"sse_contract", "golden_phrase", "pydantic_valid", "regex_not_match",
            "db_assert", "sandbox_file", "trajectory_rule"} <= set(REGISTRY)


# ---- sse_contract ----

def test_sse_contract_pass_and_fail():
    s = REGISTRY["sse_contract"]
    assert s({}, _rec(DONE)).verdict == "pass"
    r = s({"forbidden_events": ["error"]}, _rec([_ev("final_answer", content="x"), _ev("error", content="e"), _ev("done")]))
    assert r.verdict == "fail" and "error" in r.evidence
    r = s({"must_events": ["tool_call"]}, _rec(DONE))
    assert r.verdict == "fail" and "tool_call" in r.evidence


# ---- golden_phrase ----

def test_golden_phrase_pass_partial_fail():
    s = REGISTRY["golden_phrase"]
    assert s({"phrase": "pgvector"}, _rec(DONE, final="选型是 pgvector")).verdict == "pass"
    # 检索到了但没用上 → partial
    rec = _rec([_ev("tool_result", tool="rag_search", output="...pgvector...")] + DONE, final="不知道")
    r = s({"phrase": "pgvector"}, rec)
    assert r.verdict == "partial" and r.value == 0.5
    assert s({"phrase": "pgvector"}, _rec(DONE, final="不知道")).verdict == "fail"
    assert s({}, _rec(DONE)).verdict == "judge_error"


def test_golden_phrase_any_of_and_whitespace_normalization():
    s = REGISTRY["golden_phrase"]
    # any-of：语料原词与自然转述任一命中即可（2026-09-09 首跑校准）
    r = s({"phrases": ["滑窗 6 条", "最近 6 条"]}, _rec(DONE, final="滑窗只留最近 6 条消息"))
    assert r.verdict == "pass" and "最近 6 条" in r.evidence
    # 空白归一化：回答里插入空格/换行不影响命中（注意不剥标点，"+"需保留）
    r = s({"phrase": "PostgreSQL 16 + pgvector"}, _rec(DONE, final="选用 PostgreSQL 16 \n+ pgvector 组合"))
    assert r.verdict == "pass"
    assert s({"phrases": ["甲", "乙"]}, _rec(DONE, final="无关内容")).verdict == "fail"


# ---- pydantic_valid ----

def test_pydantic_valid():
    s = REGISTRY["pydantic_valid"]
    ok = _rec([_ev("task_completed", output={"pydantic_valid": True})] + DONE)
    assert s({"expect": True}, ok).verdict == "pass"
    bad = _rec([_ev("task_completed", output={"pydantic_valid": False})] + DONE)
    assert s({"expect": True}, bad).verdict == "fail"
    assert s({"expect": True}, _rec(DONE)).verdict == "judge_error"


# ---- regex_not_match ----

def test_regex_not_match_leakage():
    s = REGISTRY["regex_not_match"]
    pat = {"pattern": "<tool_call>|</tool_call>|\"arguments\"\\s*:"}
    assert s(pat, _rec(DONE, final="正常回答")).verdict == "pass"
    leak = _rec(DONE, final='前置 🕗 {"name": "view_file", "arguments": {"file_path": "x"}} </tool_call> 后续')
    r = s(pat, leak)
    assert r.verdict == "fail" and "tool_call" in r.evidence


# ---- db_assert ----

def _db_snap(docs):
    return {"db": {"documents": [{"id": i, "name": n, "chunk_count": c} for i, n, c in docs]}}


def test_db_assert_delta_and_stages():
    s = REGISTRY["db_assert"]
    snaps = {"before": _db_snap([(1, "a", 5)]), "after": _db_snap([(1, "a", 5), (2, "b", 8)])}
    r = s({"metric": "document_count", "op": ">=", "value": 1}, _rec(DONE, snapshots=snaps))
    assert r.verdict == "pass" and "增量 1" in r.evidence
    r = s({"metric": "chunk_count", "op": ">=", "value": 10}, _rec(DONE, snapshots=snaps))
    assert r.verdict == "fail"  # 增量 8 < 10
    # 多环节部分分：两环节只过一个 → partial
    r = s({"stages": [
        {"name": "入库", "metric": "document_count", "op": ">=", "value": 1},
        {"name": "切块", "metric": "chunk_count", "op": ">=", "value": 10},
    ]}, _rec(DONE, snapshots=snaps))
    assert r.verdict == "partial" and r.value == 0.5
    assert s({"metric": "document_count"}, _rec(DONE)).verdict == "judge_error"


# ---- sandbox_file ----

def test_sandbox_file():
    s = REGISTRY["sandbox_file"]
    snaps = {"after": {"sandbox": {"files": [{"path": "report.md", "size": 800}]}}}
    assert s({"path": "report.md"}, _rec(DONE, snapshots=snaps)).verdict == "pass"
    r = s({"path": "report.md", "min_bytes": 1000}, _rec(DONE, snapshots=snaps))
    assert r.verdict == "fail" and "800" in r.evidence
    r = s({"path": "nope.md"}, _rec(DONE, snapshots=snaps))
    assert r.verdict == "fail" and "nope.md" in r.evidence
    assert s({"path": "x"}, _rec(DONE)).verdict == "judge_error"


# ---- trajectory_rule ----

def test_trajectory_expect_forbidden_repeat_min():
    s = REGISTRY["trajectory_rule"]
    events = [
        _ev("tool_call", tool="view_file", input={"file_path": "outputs/a.md"}),
        _ev("tool_result", tool="view_file", output="文件不存在。outputs/ 下现有: b.md, c.md"),
        _ev("tool_call", tool="view_file", input={"file_path": "outputs/b.md"}),
        _ev("final_answer", content="ok"),
        _ev("done"),
    ]
    good = {"rules": [
        {"kind": "expect_event", "event": "tool_call", "tool": "view_file"},
        {"kind": "expect_event", "event": "tool_result", "tool": "view_file", "output_contains": ["outputs"]},
        {"kind": "no_repeat_tool_input", "tool": "view_file", "input_key": "file_path"},
        {"kind": "min_calls", "tool": "view_file", "count": 2},
    ]}
    assert s(good, _rec(events)).verdict == "pass"

    # 同路径重抓 → fail（2026-08-25 事故机械护栏的反向断言）
    dup = [_ev("tool_call", tool="view_file", input={"file_path": "outputs/a.md"})] * 2 + DONE
    r = s(good, _rec(dup))
    assert r.verdict == "fail" and "重复" in r.evidence

    # 禁止读取 .env（越界）
    evil = [_ev("tool_call", tool="view_file", input={"file_path": "../.env"})] + DONE
    r = s({"rules": [{"kind": "forbidden_event", "event": "tool_call", "tool": "view_file",
                      "input_contains": {"file_path": ".env"}}]}, _rec(evil))
    assert r.verdict == "fail"
    assert s({"rules": [{"kind": "forbidden_event", "event": "tool_call", "tool": "view_file",
                         "input_contains": {"file_path": ".env"}}]}, _rec(DONE)).verdict == "pass"

    # 换词再查（2026-08-26 事故）：两次 rag_search 不同 query
    rag = [
        _ev("tool_call", tool="rag_search", input={"query": "mods 目录", "top_k": 10}),
        _ev("tool_call", tool="rag_search", input={"query": "mods 位置 在哪", "top_k": 10}),
    ] + DONE
    r = s({"rules": [
        {"kind": "min_calls", "tool": "rag_search", "count": 2},
        {"kind": "no_repeat_tool_input", "tool": "rag_search", "input_key": "query"},
    ]}, _rec(rag))
    assert r.verdict == "pass"
    same = [_ev("tool_call", tool="rag_search", input={"query": "mods 目录"})] * 2 + DONE
    r = s({"rules": [{"kind": "no_repeat_tool_input", "tool": "rag_search", "input_key": "query"}]}, _rec(same))
    assert r.verdict == "fail"

    assert s({}, _rec(DONE)).verdict == "judge_error"
