"""聚合：单次试验四级判定 + 多次试验 majority/flaky。

四级判定（从评分器结果自动推导，不引入人工判断）：
  完全   —— 全部评分器 pass
  部分   —— 存在 partial 且无 fail（多环节任务部分达成）
  错误   —— 任一评分器 fail
  未完成 —— 环境错误 / 无 final_answer / SSE 未正常收尾（error 终止等）

judge_error 是评分器故障：该试验"不可判"，聚合时剔除并单独计数，
绝不计入 agent 失败（否则评分器 bug 会伪装成能力退化）。

多次试验（消 LLM 随机性）：majority 定案；结果不一致 → flaky 标记。
flaky 是最有价值的诊断信号——通常指向 prompt 边界而非模型随机。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from testing.eval.scorers.base import Score

LEVELS = ("完全", "部分", "错误", "未完成")
# TSR 折算权重：部分得分 0.5
LEVEL_VALUE = {"完全": 1.0, "部分": 0.5, "错误": 0.0, "未完成": 0.0}


def derive_trial_level(scores: list[Score], *, run_error: str | None,
                       has_final_answer: bool, sse_ok: bool) -> tuple[str, list[Score]]:
    """单次试验 → (四级判定, 参与判定的评分器列表)。

    sse_ok: 事件流以 done 收尾且无 error 事件（由 runner 判定后传入）。
    """
    if run_error:
        return "未完成", []
    if not has_final_answer or not sse_ok:
        return "未完成", []
    if any(s.verdict == "fail" for s in scores):
        return "错误", scores
    if any(s.verdict == "partial" for s in scores):
        return "部分", scores
    return "完全", scores


@dataclass
class TrialResult:
    index: int
    level: str                       # LEVELS 之一
    scores: list[Score] = field(default_factory=list)
    elapsed: float = 0.0
    error: str | None = None         # 环境层错误原文


@dataclass
class CaseResult:
    case_id: str
    dataset: str
    level: str                       # majority 定案
    flaky: bool
    distribution: dict[str, int]     # 各判定出现次数
    trials: list[TrialResult] = field(default_factory=list)
    judge_errors: int = 0            # 评分器故障次数（独立于 agent 判定）
    score_value: float = 0.0         # LEVEL_VALUE[level]

    @property
    def passed(self) -> bool:
        return self.level == "完全"


def aggregate_case(case_id: str, dataset: str, trials: list[TrialResult]) -> CaseResult:
    """多次试验 → majority 判定 + flaky 标记。

    judge_error 试验不参与 majority；全部不可判时整体记"未完成"并全量计数
    judge_errors（报告里应醒目提示评分器需要修，而非 agent 退化）。
    """
    judgeable = [t for t in trials if t.level != "JUDGE_ERROR"]
    n_judge_error = len(trials) - len(judgeable)
    if not judgeable:
        return CaseResult(case_id=case_id, dataset=dataset, level="未完成", flaky=False,
                          distribution={"JUDGE_ERROR": len(trials)}, trials=trials,
                          judge_errors=n_judge_error, score_value=0.0)
    counts = Counter(t.level for t in judgeable)
    # majority：并列时按严重度更差的优先（错误 > 未完成 > 部分 > 完全），
    # 保守取向——灰区判失败，防止指标虚高
    severity = {"错误": 0, "未完成": 1, "部分": 2, "完全": 3}
    level = min(counts, key=lambda lv: (-counts[lv], severity[lv]))
    flaky = len(counts) > 1
    return CaseResult(case_id=case_id, dataset=dataset, level=level, flaky=flaky,
                      distribution=dict(counts), trials=trials, judge_errors=n_judge_error,
                      score_value=LEVEL_VALUE[level])


def dataset_summary(case_results: list[CaseResult]) -> dict:
    total = len(case_results)
    by_level = {lv: sum(1 for r in case_results if r.level == lv) for lv in LEVELS}
    tsr = sum(r.score_value for r in case_results) / total if total else 0.0
    return {
        "total": total,
        "by_level": by_level,
        "tsr": round(tsr, 4),
        "flaky": [r.case_id for r in case_results if r.flaky],
        "judge_errors": sum(r.judge_errors for r in case_results),
    }
