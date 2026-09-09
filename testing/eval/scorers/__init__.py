"""评分器注册表。

约定：
- 每个评分器是纯函数 score(spec: dict, run: RunRecord) -> Score，
  只读 RunRecord（SSE 事件 + 环境快照），不发起任何网络/DB 操作；
  快照采集是 runner / EnvAdapter 的职责。
- verdict ∈ {pass, partial, fail, judge_error}；
  judge_error 表示"评分器自身故障"（快照缺失、spec 非法等），
  绝不与 agent 失败混计。
"""
from testing.eval.scorers.base import REGISTRY, RunRecord, Score, register
from testing.eval.scorers.contract import db_assert, golden_phrase, pydantic_valid, regex_not_match, sandbox_file
from testing.eval.scorers.trajectory import trajectory_rule

# 触发注册（import 副作用）
_ = (db_assert, golden_phrase, pydantic_valid, regex_not_match, sandbox_file, trajectory_rule)

__all__ = ["REGISTRY", "RunRecord", "Score", "register"]
