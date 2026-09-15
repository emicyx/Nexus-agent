"""score_trial 的 judge_error 纪律：任一硬评分器故障 → 试验不可判（JUDGE_ERROR）。

2026-09-14 实录缺陷：llm_rubric 的 judge API 故障时，用例仅凭 sse_contract pass
就被判"完全"（静默通过），case 级 judge_errors 计数为 0。修复后混合场景必须
整体不可判——机械断言不能替 judge 类评分器兜底。
"""
import pytest

from testing.eval.aggregate import aggregate_case, TrialResult
from testing.eval.runner import score_trial
from testing.eval.schema import CaseSpec, ScorerSpec
from testing.eval.scorers.base import REGISTRY, RunRecord, Score, register


def _ok_record() -> RunRecord:
    """完整 SSE 闭环 + 非空 final_answer 的合法试验。"""
    return RunRecord(
        events=[
            {"type": "agent_thinking", "data": {"content": "思考"}},
            {"type": "final_answer", "data": {"content": "如实回答：无法读取该文件"}},
            {"type": "done", "data": {}},
        ],
        final_answer="如实回答：无法读取该文件",
    )


@pytest.fixture
def broken_rubric(monkeypatch):
    """注册一个必然抛异常的评分器，模拟 judge API 故障。"""

    @register("boom_scorer")
    def _boom(spec, run):
        raise RuntimeError("judge API down")

    yield
    REGISTRY.pop("boom_scorer", None)


def _case(scorer_types: list[str]) -> CaseSpec:
    return CaseSpec(
        id="t-case", dataset="test", input={"message": "m"},
        scorers=[ScorerSpec(type=t, spec={}) for t in scorer_types],
    )


class TestScoreTrialJudgeError:
    def test_mixed_pass_and_judge_error_is_not_adjudicable(self, broken_rubric):
        """机械断言 pass + rubric 故障 → JUDGE_ERROR（不许静默通过）。"""
        level, scores = score_trial(_case(["sse_contract", "boom_scorer"]), _ok_record())
        verdicts = {s.scorer: s.verdict for s in scores}
        assert verdicts["sse_contract"] == "pass"
        assert verdicts["boom_scorer"] == "judge_error"
        assert level == "JUDGE_ERROR"

    def test_all_judge_error_is_not_adjudicable(self, broken_rubric):
        level, _ = score_trial(_case(["boom_scorer"]), _ok_record())
        assert level == "JUDGE_ERROR"

    def test_all_healthy_scorers_score_normally(self):
        """无故障时照常判定（回归保护）。"""
        level, scores = score_trial(_case(["sse_contract"]), _ok_record())
        assert level == "完全"
        assert scores[0].verdict == "pass"

    def test_soft_scorer_judge_error_also_blocks(self, broken_rubric):
        """weight=0 软断言故障同样不可判：软断言也是断言，坏了要知道。"""
        case = CaseSpec(
            id="t-case", dataset="test", input={"message": "m"},
            scorers=[ScorerSpec(type="sse_contract"), ScorerSpec(type="boom_scorer", weight=0)],
        )
        # 软断言被过滤出 hard 后 verdict_pool=hard（全 pass）→ 不触发 JUDGE_ERROR，
        # 但 boom_scorer 的 judge_error 仍在 scores 里可见（记录进报告）
        level, scores = score_trial(case, _ok_record())
        verdicts = {s.scorer: s.verdict for s in scores}
        assert verdicts["boom_scorer"] == "judge_error"
        assert level == "完全"


class TestAggregateCountsJudgeErrors:
    def test_judge_error_trial_counted_and_excluded_from_majority(self):
        """JUDGE_ERROR 试验不参与 majority 且全量计数。"""
        trials = [
            TrialResult(index=0, level="JUDGE_ERROR", elapsed=1, cost_tokens=None,
                        error=None, scores=[]),
            TrialResult(index=1, level="完全", elapsed=1, cost_tokens=None,
                        error=None, scores=[]),
        ]
        r = aggregate_case("c", "d", trials)
        assert r.judge_errors == 1
        assert r.level == "完全"  # 剩余可判试验的 majority
