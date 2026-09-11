"""eval harness 单测：数据集 schema 加载与校验。"""
from pathlib import Path

import pytest

from testing.eval.schema import SchemaError, load_dataset

_DATASETS = Path(__file__).resolve().parents[1] / "eval" / "datasets"


def _write(tmp_path, payload):
    p = tmp_path / "ds.json"
    p.write_text(__import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_real_datasets():
    """仓库内真实数据集必须始终合法（CI 防数据集 JSON 腐化）。"""
    names = sorted(f.stem for f in _DATASETS.glob("*.json"))
    assert {"rag_v3", "capability_incidents", "redteam"} <= set(names)
    for name in names:
        ds = load_dataset(_DATASETS / f"{name}.json")
        assert ds.cases, name
        assert all(c.scorers for c in ds.cases)
        assert all(c.input["message"] for c in ds.cases)
    rag = load_dataset(_DATASETS / "rag_v3.json")
    assert len(rag.cases) == 15
    inc = load_dataset(_DATASETS / "capability_incidents.json")
    assert len(inc.cases) == 13
    rt = load_dataset(_DATASETS / "redteam.json")
    assert len(rt.cases) == 8
    assert all(c.scenario == "异常" for c in rt.cases)
    assert all(c.origin and c.origin.startswith("incident:") for c in inc.cases)


def test_scenario_coverage_of_incident_set():
    ds = load_dataset(_DATASETS / "capability_incidents.json")
    scenarios = {c.scenario for c in ds.cases}
    assert {"常规", "边界", "异常"} <= scenarios


def test_reject_unknown_scorer_type(tmp_path):
    p = _write(tmp_path, {"name": "x", "cases": [
        {"id": "c1", "input": {"message": "hi"}, "scorers": [{"type": "no_such", "spec": {}}]}
    ]})
    with pytest.raises(SchemaError, match="未知评分器类型"):
        load_dataset(p)


def test_reject_duplicate_case_id(tmp_path):
    case = {"id": "c1", "input": {"message": "hi"}, "scorers": [{"type": "sse_contract", "spec": {}}]}
    p = _write(tmp_path, {"name": "x", "cases": [case, case]})
    with pytest.raises(SchemaError, match="id 重复"):
        load_dataset(p)


def test_reject_missing_message(tmp_path):
    p = _write(tmp_path, {"name": "x", "cases": [
        {"id": "c1", "input": {}, "scorers": [{"type": "sse_contract", "spec": {}}]}
    ]})
    with pytest.raises(SchemaError, match="message"):
        load_dataset(p)


def test_reject_bad_scenario(tmp_path):
    p = _write(tmp_path, {"name": "x", "cases": [
        {"id": "c1", "scenario": "日常", "input": {"message": "hi"},
         "scorers": [{"type": "sse_contract", "spec": {}}]}
    ]})
    with pytest.raises(SchemaError, match="scenario"):
        load_dataset(p)


def test_effective_trials_fallback(tmp_path):
    p = _write(tmp_path, {"name": "x", "default_trials": 2, "cases": [
        {"id": "c1", "input": {"message": "hi"}, "scorers": [{"type": "sse_contract", "spec": {}}]},
        {"id": "c2", "trials": 5, "input": {"message": "hi"}, "scorers": [{"type": "sse_contract", "spec": {}}]},
    ]})
    ds = load_dataset(p)
    assert ds.cases[0].effective_trials(ds.default_trials) == 2
    assert ds.cases[1].effective_trials(ds.default_trials) == 5


def _case(cid, **kw):
    base = {"id": cid, "input": {"message": "hi"}, "scorers": [{"type": "sse_contract", "spec": {}}]}
    base.update(kw)
    return base


def test_dataset_level_pre_state_defaults_and_case_override(tmp_path):
    """#8：数据集级 pre_state 为默认值，用例级按键覆盖（redteam 全组封闭环境只声明一次）。"""
    p = _write(tmp_path, {"name": "x",
                          "pre_state": {"clean_sandbox": True, "cleanup_new_documents": True},
                          "cases": [
                              _case("c1"),
                              _case("c2", pre_state={"clean_sandbox": False,
                                                     "ingest_documents": [{"name": "n", "content": "c"}]}),
                          ]})
    ds = load_dataset(p)
    assert ds.cases[0].pre_state == {"clean_sandbox": True, "cleanup_new_documents": True}
    # 用例级按键覆盖：clean_sandbox 被关掉，数据集级 cleanup_new_documents 保留，私有键合入
    assert ds.cases[1].pre_state == {"clean_sandbox": False, "cleanup_new_documents": True,
                                     "ingest_documents": [{"name": "n", "content": "c"}]}


def test_pre_state_type_validation(tmp_path):
    p = _write(tmp_path, {"name": "x", "pre_state": ["not", "a", "dict"], "cases": [_case("c1")]})
    with pytest.raises(SchemaError, match="数据集级 pre_state"):
        load_dataset(p)
    p = _write(tmp_path, {"name": "x", "cases": [_case("c1", pre_state="oops")]})
    with pytest.raises(SchemaError, match="pre_state"):
        load_dataset(p)


def test_real_datasets_pre_state_wiring():
    """真实数据集：redteam 全组封闭环境默认 + inc-a3 用例级清理（#8 落地核验）。"""
    rt = load_dataset(_DATASETS / "redteam.json")
    assert all(c.pre_state and c.pre_state.get("clean_sandbox")
               and c.pre_state.get("cleanup_new_documents") for c in rt.cases)
    # rt-1/rt-2 用例级毒文档与数据集级默认合并共存
    rt1 = next(c for c in rt.cases if c.id.startswith("rt-1"))
    assert rt1.pre_state["ingest_documents"]
    # #9：rt-4 硬门禁锚沙箱外 ../escape.md，沙箱内合规位置降 weight 0
    rt4 = next(c for c in rt.cases if c.id.startswith("rt-4"))
    sb = [(s.spec.get("path"), s.weight) for s in rt4.scorers if s.type == "sandbox_file"]
    assert ("../escape.md", 1.0) in sb and ("escape.md", 0.0) in sb
    inc = load_dataset(_DATASETS / "capability_incidents.json")
    a3 = next(c for c in inc.cases if c.id == "inc-a3")
    assert a3.pre_state == {"clean_sandbox": True, "cleanup_new_documents": True}
    # 其余 capability 用例不受数据集级默认影响（无该键声明）
    assert all(c.pre_state is None for c in inc.cases if c.id != "inc-a3")
    rag = load_dataset(_DATASETS / "rag_v3.json")
    q15 = next(c for c in rag.cases if c.id == "q15")
    assert any(s.type == "llm_rubric" for s in q15.scorers)
    assert next(s for s in q15.scorers if s.type == "regex_not_match").weight == 0
