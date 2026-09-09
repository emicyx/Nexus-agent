"""eval harness 单测：四级判定推导 + 多次试验聚合。"""
from testing.eval.aggregate import aggregate_case, dataset_summary, derive_trial_level
from testing.eval.scorers.base import Score


def _scores(*verdicts):
    return [Score(scorer=f"s{i}", verdict=v) for i, v in enumerate(verdicts)]


# ---- derive_trial_level：四级判定 ----

def test_level_complete_partial_error_incomplete():
    kw = dict(run_error=None, has_final_answer=True, sse_ok=True)
    assert derive_trial_level(_scores("pass", "pass"), **kw)[0] == "完全"
    assert derive_trial_level(_scores("pass", "partial"), **kw)[0] == "部分"
    assert derive_trial_level(_scores("partial", "fail"), **kw)[0] == "错误"
    assert derive_trial_level(_scores("pass"), run_error="HTTP 500", has_final_answer=True, sse_ok=True)[0] == "未完成"
    assert derive_trial_level(_scores("pass"), run_error=None, has_final_answer=False, sse_ok=True)[0] == "未完成"
    assert derive_trial_level(_scores("pass"), run_error=None, has_final_answer=True, sse_ok=False)[0] == "未完成"


# ---- aggregate_case：majority / flaky / judge_error ----

def _trial(i, level):
    from testing.eval.aggregate import TrialResult
    return TrialResult(index=i, level=level)


def test_majority_and_flaky():
    r = aggregate_case("c1", "ds", [_trial(0, "完全"), _trial(1, "完全"), _trial(2, "错误")])
    assert r.level == "完全" and r.flaky and r.distribution == {"完全": 2, "错误": 1}
    r = aggregate_case("c1", "ds", [_trial(0, "错误"), _trial(1, "错误")])
    assert not r.flaky and r.score_value == 0.0


def test_majority_tie_breaks_to_worse_level():
    # 2:2 并列时保守取向：判"错误"而非"完全"，防指标虚高
    r = aggregate_case("c1", "ds", [_trial(0, "完全"), _trial(1, "完全"), _trial(2, "错误"), _trial(3, "错误")])
    assert r.level == "错误"


def test_judge_error_excluded_from_majority():
    trials = [_trial(0, "JUDGE_ERROR"), _trial(1, "完全"), _trial(2, "完全")]
    r = aggregate_case("c1", "ds", trials)
    assert r.level == "完全" and r.judge_errors == 1 and not r.flaky


def test_all_judge_error_is_incomplete_with_warning():
    r = aggregate_case("c1", "ds", [_trial(0, "JUDGE_ERROR"), _trial(1, "JUDGE_ERROR")])
    assert r.level == "未完成" and r.judge_errors == 2


# ---- dataset_summary ----

def test_dataset_summary_tsr():
    from testing.eval.aggregate import CaseResult
    rs = []
    for cid, level in [("a", "完全"), ("b", "部分"), ("c", "错误")]:
        cr = CaseResult(case_id=cid, dataset="ds", level=level, flaky=False,
                        distribution={level: 1}, score_value={"完全": 1.0, "部分": 0.5, "错误": 0.0}[level])
        rs.append(cr)
    s = dataset_summary(rs)
    assert s["total"] == 3 and s["by_level"]["完全"] == 1
    assert abs(s["tsr"] - (1 + 0.5 + 0) / 3) < 1e-6
