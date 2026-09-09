"""LLM-as-a-Judge 评分器（Phase 2）：开放式回答的 Rubric 打分。

与确定性评分器的分工：golden_phrase 抓"逐字可验证的事实锚"，
llm_rubric 收编转述型/行为型判定（正确但换措辞、诚实拒答、幻觉）——
首跑校准已证明逐字匹配对生成层有长尾误判，这是"确定性优先、LLM 补位"
分层在 Phase 2 的交接点。

judge 故障（无 KEY/解析失败）→ judge_error，不计 agent 失败。

spec: {
  reference: str,                 # 评分锚（参考答案或"正确行为"描述）
  dimensions: {名: 权重},          # 缺省 {"正确性": 1.0}
  pass_threshold: 0.7,
  question: str?,                 # 缺省取 RunRecord.question
  model: "qwen-plus"?
}
"""
from __future__ import annotations

from testing.eval.scorers.base import REGISTRY, RunRecord, Score, register


@register("llm_rubric")
def llm_rubric(spec: dict, run: RunRecord) -> Score:
    from testing.eval import judge as judge_mod

    name = "llm_rubric"
    reference = spec.get("reference")
    if not reference:
        return Score(name, "judge_error", evidence="spec 缺 reference（评分锚）")
    dimensions = spec.get("dimensions") or {"正确性": 1.0}
    threshold = float(spec.get("pass_threshold", 0.7))
    question = spec.get("question") or run.question
    if not question:
        return Score(name, "judge_error", evidence="无问题上下文（RunRecord.question 未填充）")
    answer = run.final_answer
    if not answer.strip():
        return Score(name, "fail", value=0.0, evidence="final_answer 为空，无从评分")

    parsed = judge_mod.rubric_judge(question, answer, reference, dimensions,
                                    model=spec.get("model", judge_mod.DEFAULT_MODEL))
    if parsed is None:
        return Score(name, "judge_error", evidence="judge 调用失败（见 stderr）")

    total = judge_mod.weighted_total(parsed, dimensions)
    reasons = "; ".join(f"{d}={parsed['dimensions'].get(d, '?')}"
                        f"({parsed.get('reasons', {}).get(d, '')[:60]})"
                        for d in dimensions)
    verdict = "pass" if total >= threshold else "fail"
    return Score(name, verdict, value=round(total, 3), evidence=f"加权 {total:.2f}（阈值 {threshold}）| {reasons}")
