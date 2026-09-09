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
    for name in ("rag_v3", "capability_incidents"):
        ds = load_dataset(_DATASETS / f"{name}.json")
        assert ds.cases, name
        assert all(c.scorers for c in ds.cases)
        assert all(c.input["message"] for c in ds.cases)
    rag = load_dataset(_DATASETS / "rag_v3.json")
    assert len(rag.cases) == 15
    inc = load_dataset(_DATASETS / "capability_incidents.json")
    assert len(inc.cases) == 13
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
