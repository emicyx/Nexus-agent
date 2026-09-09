"""eval harness 单测：llm_rubric 评分器 + judge 数学（假 judge 注入，零网络）。"""
import pytest

from testing.eval import judge as judge_mod
from testing.eval.scorers import REGISTRY
from testing.eval.scorers.base import RunRecord


DONE = [{"type": "final_answer", "data": {"content": "答"}}, {"type": "done"}]


def _patch_rubric(monkeypatch, ret):
    monkeypatch.setattr(judge_mod, "rubric_judge", lambda *a, **kw: ret)


def test_rubric_registry_and_missing_reference():
    assert "llm_rubric" in REGISTRY
    r = REGISTRY["llm_rubric"]({}, RunRecord(events=DONE, final_answer="x"))
    assert r.verdict == "judge_error" and "reference" in r.evidence


def test_rubric_pass_fail_with_fake_judge(monkeypatch):
    ok = {"dimensions": {"正确性": 0.9}, "reasons": {"正确性": "覆盖要点"}}
    _patch_rubric(monkeypatch, ok)
    run = RunRecord(events=DONE, final_answer="正确的回答", question="问题？")
    r = REGISTRY["llm_rubric"]({"reference": "ref"}, run)
    assert r.verdict == "pass" and r.value == 0.9 and "正确性=0.9" in r.evidence

    low = {"dimensions": {"正确性": 0.3}, "reasons": {"正确性": "缺关键要点"}}
    _patch_rubric(monkeypatch, low)
    r = REGISTRY["llm_rubric"]({"reference": "ref"}, run)
    assert r.verdict == "fail"


def test_rubric_judge_error_paths(monkeypatch):
    run = RunRecord(events=DONE, final_answer="x", question="q")
    _patch_rubric(monkeypatch, None)
    assert REGISTRY["llm_rubric"]({"reference": "r"}, run).verdict == "judge_error"
    _patch_rubric(monkeypatch, {"dimensions": {}, "reasons": {}})
    assert REGISTRY["llm_rubric"]({"reference": "r"}, RunRecord(events=DONE, final_answer="", question="q")).verdict == "fail"
    # 无问题上下文 → judge_error（而非猜）
    _patch_rubric(monkeypatch, {"dimensions": {"正确性": 1}, "reasons": {}})
    assert REGISTRY["llm_rubric"]({"reference": "r"}, RunRecord(events=DONE, final_answer="x")).verdict == "judge_error"


def test_weighted_total():
    parsed = {"dimensions": {"a": 1.0, "b": 0.5}}
    dims = {"a": 0.75, "b": 0.25}
    assert judge_mod.weighted_total(parsed, dims) == pytest.approx(0.875)


def test_cohen_kappa():
    assert judge_mod._cohen_kappa([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0
    # po=0.5, pa=pb=0.5 → pe=0.5 → kappa=0
    assert judge_mod._cohen_kappa([1, 0, 1, 0], [1, 1, 0, 0]) == pytest.approx(0.0)
    assert judge_mod._cohen_kappa([], []) == 0.0


def test_cache_key_changes_with_prompt_version():
    a = judge_mod._cache_key("m", "s", "u")
    old = judge_mod.PROMPT_VERSION
    try:
        judge_mod.PROMPT_VERSION = "bumped"
        assert judge_mod._cache_key("m", "s", "u") != a  # prompt 版本变更必须击穿缓存
    finally:
        judge_mod.PROMPT_VERSION = old
