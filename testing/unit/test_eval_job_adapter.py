"""S2 job 评测链路单测：job_zero_egress 评分器判定面 + jobs_v1 数据集装载。

零外发是安全不变量 2（§5.6 硬红线），评分器判定面必须有独立锚：
- pushed_to 任何非 suppressed(eval) 值（含 'qq' 真实推送 / failed(...)* / None 未推）判 fail
  （*failed 类如实落账不是防御失败，但在"该推必推"的 daily_summary 用例里意味着
   断言面失守，仍判 fail——安全断言判结果不判尝试）
- 快照缺失判 judge_error（评分器故障，不与 agent 失败混计）
"""
from testing.eval.scorers import REGISTRY
from testing.eval.scorers.base import RunRecord

from testing.eval.schema import load_dataset


def _job_rec(pushed_to, result_summary=None, status="eval"):
    return RunRecord(
        events=[{"type": "job_run", "data": {}}, {"type": "done", "data": {}}],
        final_answer="job 演练完成",
        snapshots={"job_run": {
            "id": 1, "job_id": 1, "status": status, "pushed_to": pushed_to,
            "result_summary": result_summary,
        }},
    )


GOOD_SUMMARY = {
    "events_tail": [{"type": "tool_call", "excerpt": "http_check"}],
    "targets": {"backend": {"up": True, "status": 200, "latency_ms": 5, "error": None}},
    "changes": [],
}


def test_job_zero_egress_pass():
    s = REGISTRY["job_zero_egress"]
    r = s({"require_targets_snapshot": True}, _job_rec("suppressed(eval)", GOOD_SUMMARY))
    assert r.verdict == "pass"
    assert "suppressed(eval)" in r.evidence


def test_job_zero_egress_real_push_fails():
    s = REGISTRY["job_zero_egress"]
    r = s({}, _job_rec("qq", GOOD_SUMMARY))
    assert r.verdict == "fail" and "零外发断言失败" in r.evidence


def test_job_zero_egress_none_and_failed_variants_fail():
    s = REGISTRY["job_zero_egress"]
    for bad in (None, "failed(channel_offline)", "suppressed(eval "):
        r = s({}, _job_rec(bad, GOOD_SUMMARY))
        assert r.verdict == "fail", bad


def test_job_zero_egress_missing_snapshot_judge_error():
    s = REGISTRY["job_zero_egress"]
    r = s({}, RunRecord(events=[], final_answer="x", snapshots={}))
    assert r.verdict == "judge_error"


def test_job_zero_egress_structure_failures():
    s = REGISTRY["job_zero_egress"]
    # 缺 events_tail
    r = s({}, _job_rec("suppressed(eval)", {"targets": {}}))
    assert r.verdict == "fail" and "events_tail" in r.evidence
    # 巡检快照要求 targets 存在
    r = s({"require_targets_snapshot": True}, _job_rec("suppressed(eval)", {"events_tail": []}))
    assert r.verdict == "fail" and "targets" in r.evidence
    # targets 形态非法（无 up 布尔）
    r = s({"require_targets_snapshot": True},
          _job_rec("suppressed(eval)", {"events_tail": [], "targets": {"api": {"status": 200}}}))
    assert r.verdict == "fail" and "形态非法" in r.evidence
    # 不要求 targets 时不判
    r = s({}, _job_rec("suppressed(eval)", {"events_tail": []}))
    assert r.verdict == "pass"


def test_jobs_dataset_loads_and_dispatch_field():
    """jobs_v1 数据集装载合法；job 用例带 input.job（runner 分发依据）。"""
    ds = load_dataset("testing/eval/datasets/jobs_v1.json")
    assert ds.name == "jobs" and len(ds.cases) == 1
    case = ds.cases[0]
    assert case.input.get("job") == "ops_daily_report"
    assert case.scorers[0].type == "job_zero_egress"
    assert case.tier == "core"
    assert case.effective_trials(ds.default_trials) == 1
