"""eval harness 单测：报告渲染 + 两次运行 diff。"""
from testing.eval.report import diff_runs, render_report


def _case(cid, level, dataset="ds", flaky=False, dist=None):
    return {"case_id": cid, "dataset": dataset, "level": level, "flaky": flaky,
            "distribution": dist or {level: 1}, "judge_errors": 0, "score_value": 1.0 if level == "完全" else 0.0,
            "trials": [{"index": 0, "level": level, "elapsed": 3.2, "error": None,
                        "scores": [{"scorer": "sse_contract", "verdict": "pass" if level == "完全" else "fail",
                                    "value": None, "evidence": "demo"}]}]}


def _result(run_id, cases, git="abc123", cfg="cfg999"):
    return {
        "fingerprint": {"run_id": run_id, "git_sha": git,
                        "datasets": {"ds@v1": "hash1"}, "config_snapshot": cfg, "notes": []},
        "datasets": {"ds": {"version": "v1", "summary": {
            "total": len(cases),
            "by_level": {lv: sum(1 for c in cases if c["level"] == lv) for lv in ("完全", "部分", "错误", "未完成")},
            "tsr": sum(c["score_value"] for c in cases) / len(cases) if cases else 0,
            "flaky": [c["case_id"] for c in cases if c["flaky"]],
            "judge_errors": 0}}},
        "cases": cases,
    }


def test_render_report_contains_key_sections():
    md = render_report(_result("r1", [_case("a", "完全"), _case("b", "错误", flaky=True)]))
    assert "# 评测运行报告 r1" in md
    assert "TSR" in md and "b" in md and "失败证据" in md and "flaky" in md


def test_render_report_flags_missing_config_snapshot():
    r = _result("r1", [_case("a", "完全")])
    r["fingerprint"]["config_snapshot"] = None
    md = render_report(r)
    assert "未采集" in md


def test_diff_runs_categories():
    prev = _result("r1", [_case("a", "完全"), _case("b", "错误"), _case("c", "错误")], git="old")
    curr = _result("r2", [_case("a", "错误"), _case("b", "完全"), _case("c", "错误"), _case("d", "错误")], git="new")
    md = diff_runs(prev, curr)
    assert "代码: old → new" in md
    assert "新增失败 1" in md and "d" in md            # d 是新用例且失败
    assert "恢复 1" in md and "b" in md                 # b 错误→完全
    assert "a: 完全 → 错误" in md and "↓退化" in md     # 判定迁移
    assert "TSR" in md                                  # 数据集级 TSR 对比


def test_diff_runs_identical():
    r = _result("r1", [_case("a", "完全")])
    md = diff_runs(r, _result("r2", [_case("a", "完全")]))
    assert "指纹完全一致" in md
