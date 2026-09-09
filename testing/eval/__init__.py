"""Nexus 评测 harness（testing/eval）。

分层：
- schema.py      用例/数据集的加载与校验（声明式 JSON → dataclass）
- scorers/       评分器注册表：纯函数 (spec, RunRecord) -> Score
- aggregate.py   单次试验四级判定 + 多次试验 majority/flaky 聚合
- fingerprint.py 运行指纹（数据集哈希 + git SHA + 配置快照）
- report.py      Markdown 报告渲染 + 两次运行 diff
- runner.py      CLI 编排（环境快照 → N 次执行 → 评分 → 报告）

设计约束：除 runner 需 httpx 外，其余模块仅依赖标准库——
保证在无 Docker/无 DB 的 CI 环境可完整单测（见 testing/unit/test_eval_*.py）。
"""
