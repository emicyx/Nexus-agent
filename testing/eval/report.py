"""Markdown 报告渲染 + 两次运行 diff。

设计目标：报告的读者体验就是评测系统的生命力——
每次运行产出"一眼看清"的摘要表 + 失败证据明细 + flaky 清单；
diff 输出三张清单：新增失败 / 恢复 / 判定迁移。
"""
from __future__ import annotations

from testing.eval.aggregate import LEVELS, LEVEL_VALUE

_ORDER = list(LEVELS) + ["JUDGE_ERROR"]


def render_summary_line(summary: dict) -> str:
    by = summary["by_level"]
    return (f"TSR {summary['tsr']:.1%} | 完全 {by.get('完全', 0)} / 部分 {by.get('部分', 0)}"
            f" / 错误 {by.get('错误', 0)} / 未完成 {by.get('未完成', 0)}")


def render_report(result: dict) -> str:
    fp = result.get("fingerprint", {})
    lines: list[str] = []
    lines.append(f"# 评测运行报告 {fp.get('run_id', '?')}")
    lines.append("")
    lines.append(f"- git: `{fp.get('git_sha', '?')}`　数据集: "
                 + "、".join(f"`{k}`" for k in (fp.get("datasets") or {})))
    cfg = fp.get("config_snapshot")
    lines.append(f"- Crew 配置快照: `{cfg if cfg else '未采集（对比可信度降级）'}`")
    for note in fp.get("notes", []):
        lines.append(f"- ⚠ {note}")
    lines.append("")

    for dname, d in result.get("datasets", {}).items():
        s = d.get("summary", {})
        lines.append(f"## {dname}（{d.get('version', '?')}，{s.get('total', 0)} 条）")
        lines.append("")
        lines.append(f"> {render_summary_line(s)}　flaky: {len(s.get('flaky', []))}　评分器故障: {s.get('judge_errors', 0)}")
        lines.append("")
        lines.append("| 用例 | 判定 | 分布 | 评分器故障 | 耗时(s) |")
        lines.append("|---|---|---|---|---|")
        for c in result.get("cases", []):
            if c.get("dataset") != dname:
                continue
            dist = "/".join(f"{lv}×{n}" for lv in _ORDER if (n := c.get("distribution", {}).get(lv)))
            elapsed = [t.get("elapsed", 0) for t in c.get("trials", [])]
            lines.append(f"| {c['case_id']} | {c['level']}{' 🔁' if c.get('flaky') else ''} "
                         f"| {dist} | {c.get('judge_errors', 0)} "
                         f"| {max(elapsed) if elapsed else 0:.1f} |")
        lines.append("")

    fails = [c for c in result.get("cases", []) if c.get("level") != "完全"]
    if fails:
        lines.append("## 失败证据")
        lines.append("")
        for c in fails:
            lines.append(f"### {c['case_id']} → {c['level']}")
            for t in c.get("trials", []):
                bad = [s for s in t.get("scores", []) if s.get("verdict") != "pass"]
                for s in bad:
                    lines.append(f"- [{t.get('index')}] `{s['scorer']}` {s.get('verdict')}: {s.get('evidence', '')}")
            if c.get("judge_errors"):
                lines.append(f"- ⚠ 该用例存在 {c['judge_errors']} 次评分器故障（与 agent 判定独立，需修评分器）")
            lines.append("")

    flaky = [c for c in result.get("cases", []) if c.get("flaky")]
    if flaky:
        lines.append("## flaky 用例（判定不一致，优先怀疑 prompt 边界）")
        lines.append("")
        lines.append("、".join(c["case_id"] for c in flaky))
        lines.append("")
    return "\n".join(lines)


def diff_runs(prev: dict, curr: dict) -> str:
    """两次运行对比：指纹差异说明 + 新增失败 / 恢复 / 判定迁移。"""
    lines: list[str] = []
    lines.append(f"## 对比 {prev.get('fingerprint', {}).get('run_id', '?')} → "
                 f"{curr.get('fingerprint', {}).get('run_id', '?')}")

    from testing.eval.fingerprint import RunFingerprint

    def _fp(d):
        f = d.get("fingerprint", {})
        return RunFingerprint(run_id=f.get("run_id", "?"), git_sha=f.get("git_sha", "?"),
                              datasets=f.get("datasets") or {},
                              config_snapshot=f.get("config_snapshot"))
    for line in _fp(curr).compare(_fp(prev)):
        lines.append(f"- {line}")

    prev_cases = {c["case_id"]: c for c in prev.get("cases", [])}
    curr_cases = {c["case_id"]: c for c in curr.get("cases", [])}
    severity = {lv: -LEVEL_VALUE.get(lv, 0.0) for lv in LEVELS}  # 完全最好 → 数值最小

    new_fail = [cid for cid, c in curr_cases.items()
                if cid not in prev_cases and c["level"] != "完全"]
    recovered = [cid for cid, c in curr_cases.items()
                 if cid in prev_cases and prev_cases[cid]["level"] != "完全" and c["level"] == "完全"]
    moved = [(cid, prev_cases[cid]["level"], c["level"]) for cid, c in curr_cases.items()
             if cid in prev_cases and prev_cases[cid]["level"] != c["level"]
             and not (prev_cases[cid]["level"] != "完全" and c["level"] == "完全")]
    gone = [cid for cid in prev_cases if cid not in curr_cases]

    lines.append("")
    lines.append(f"- **新增失败 {len(new_fail)}**: {new_fail or '无'}")
    lines.append(f"- **恢复 {len(recovered)}**: {recovered or '无'}")
    for cid, a, b in moved:
        mark = "↓退化" if severity.get(b, 0) > severity.get(a, 0) else "↑改善"
        lines.append(f"- 判定迁移 {cid}: {a} → {b}（{mark}）")
    if gone:
        lines.append(f"- 上次存在本次缺失的用例（数据集变更）: {gone}")
    for dname, d in curr.get("datasets", {}).items():
        pd = prev.get("datasets", {}).get(dname)
        if pd:
            delta = d["summary"]["tsr"] - pd["summary"]["tsr"]
            lines.append(f"- {dname} TSR: {pd['summary']['tsr']:.1%} → {d['summary']['tsr']:.1%}（{delta:+.1%}）")
    return "\n".join(lines)
