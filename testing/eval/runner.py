"""评测 runner：指纹 → 环境快照 → N 次执行 → 评分 → 聚合 → 报告/diff。

用法（Docker 全栈启动后）：
    python testing/eval/runner.py                        # 跑 datasets/ 全部
    python testing/eval/runner.py --datasets rag_v3 --trials 1
    python testing/eval/runner.py --diff 20260908-1530   # 与上次运行对比

环境变量：
    NEXUS_BASE_URL（默认 http://localhost:8000）
    NEXUS_API_KEY（= 后端 APP_API_KEY，未设鉴权时可不填）
    EVAL_SANDBOX_DIR（默认 <repo>/backend/data/outputs，宿主机直读 compose 挂载）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for p in (_REPO,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import httpx  # noqa: E402

from testing.eval.aggregate import CaseResult, TrialResult, aggregate_case, dataset_summary, derive_trial_level
from testing.eval.fingerprint import RunFingerprint, config_fingerprint_from_payloads, dataset_fingerprint, git_sha
from testing.eval.report import diff_runs, render_report
from testing.eval.schema import Dataset, load_dataset
from testing.eval.scorers import REGISTRY
from testing.eval.scorers.base import RunRecord, Score

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


class EnvAdapter:
    """环境快照采集：评分器只读快照，网络/文件访问全部收敛在这里。"""

    def __init__(self, base_url: str, api_key: str | None, sandbox_dir: Path,
                 auto_approve: str | None = None):
        headers = {"X-API-Key": api_key} if api_key else {}
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=30)
        self.sandbox_dir = sandbox_dir
        self.auto_approve = auto_approve  # "approved"/"rejected"：HITL 自动应答，无人值守评测必需
        self._crew_cache: dict[str, int] = {}

    def ingest_document(self, name: str, content: str) -> dict:
        return self.client.post("/v1/documents",
                                json={"name": name, "content": content, "source_type": "text"}).json()

    def delete_document(self, name: str) -> bool:
        for d in self.client.get("/v1/documents").json():
            if d["name"] == name:
                self.client.delete(f"/v1/documents/{d['id']}")
                return True
        return False

    def metrics_today_tokens(self) -> float | None:
        """当日 LLM token 总量（/metrics counter）。试验前后差 = 该次成本。"""
        try:
            text = self.client.get("/metrics").text
            for line in text.splitlines():
                if line.startswith("token_usage_today_total"):
                    return float(line.rsplit(" ", 1)[1])
        except Exception:
            return None
        return None

    def db_snapshot(self) -> dict:
        docs = self.client.get("/v1/documents").json()
        return {"documents": [{"id": d["id"], "name": d["name"], "chunk_count": d.get("chunk_count", 0)}
                              for d in docs]}

    def sandbox_snapshot(self) -> dict:
        out_dir = self.sandbox_dir
        files = []
        if out_dir.is_dir():
            for f in sorted(out_dir.rglob("*")):
                if f.is_file():
                    files.append({"path": f.relative_to(out_dir).as_posix(), "size": f.stat().st_size})
        return {"files": files}

    def config_payloads(self) -> dict[str, str] | None:
        try:
            return {
                "agents": self.client.get("/v1/agents").text,
                "crews": self.client.get("/v1/crews").text,
                "tools": self.client.get("/v1/tools").text,
            }
        except Exception:
            return None

    def resolve_crew_id(self, crew_name: str) -> int | None:
        if crew_name in self._crew_cache:
            return self._crew_cache[crew_name]
        try:
            crews = self.client.get("/v1/crews").json()
        except Exception:
            return None
        for c in crews:
            if c.get("name") == crew_name:
                self._crew_cache[crew_name] = c["id"]
                return c["id"]
        return None

    def _maybe_auto_approve(self, evt: dict) -> None:
        """HITL 自动应答：approval_requested 事件到达即 POST 决定。

        无人值守评测必需——否则审批 150s 超时会把整条链路拖死
        （inc-b3 首跑实测）。决定记录进事件流，报告可审计。
        """
        if not self.auto_approve or evt.get("type") != "approval_requested":
            return
        payload = (evt.get("data") or {}).get("input") or {}
        approval_id = payload.get("approval_id")
        if not approval_id:
            return
        try:
            r = self.client.post(f"/v1/approvals/{approval_id}",
                                 json={"decision": self.auto_approve}, timeout=10)
            print(f"    [hitl] 自动{self.auto_approve} 审批 {approval_id} → HTTP {r.status_code}")
            return {"type": "eval_auto_approval", "data": {
                "approval_id": approval_id, "decision": self.auto_approve, "http": r.status_code}}
        except Exception as e:
            print(f"    [hitl] 自动应答失败: {e}")
            return None

    def stream_chat(self, payload: dict, timeout: float) -> tuple[list[dict], float, str | None]:
        """POST /v1/chat/stream，返回 (events, elapsed, error)。"""
        events: list[dict] = []
        t0 = time.perf_counter()
        try:
            with self.client.stream("POST", "/v1/chat/stream", json=payload, timeout=timeout) as resp:
                if resp.status_code != 200:
                    return events, time.perf_counter() - t0, f"HTTP {resp.status_code}"
                block = ""
                for line in resp.iter_lines():
                    if line == "":
                        if block.strip():
                            evt = _parse_block(block)
                            events.append(evt)
                            note = self._maybe_auto_approve(evt)
                            if note:
                                events.append(note)
                        block = ""
                    else:
                        block += line + "\n"
        except Exception as e:  # 网络/超时：环境层错误，记"未完成"而非 agent 失败
            return events, time.perf_counter() - t0, f"{type(e).__name__}: {e}"
        return events, time.perf_counter() - t0, None


def _parse_block(block: str) -> dict:
    evt_type, data = None, {}
    for line in block.strip().split("\n"):
        if line.startswith("event: "):
            evt_type = line[len("event: "):].strip()
        elif line.startswith("data: "):
            try:
                data = json.loads(line[len("data: "):].strip())
            except json.JSONDecodeError:
                data = {"raw": line}
    return {"type": evt_type, "data": data}


def run_trial(env: EnvAdapter, case_input: dict, crew_name: str | None,
              timeout: float, collect_db: bool, collect_sandbox: bool,
              approval_decision: str | None = None) -> RunRecord:
    payload = {
        "message": case_input["message"],
        "session_id": case_input.get("session_id"),
        "single": case_input.get("single", False),
    }
    if case_input.get("crew_id") is not None:
        payload["crew_id"] = case_input["crew_id"]
    elif crew_name:
        crew_id = env.resolve_crew_id(crew_name)
        if crew_id is None:
            return RunRecord(error=f"crew 未找到或 API 不可用: {crew_name}")
        payload["crew_id"] = crew_id

    snap_before: dict = {}
    tok0 = env.metrics_today_tokens()
    env.auto_approve = approval_decision  # 用例级覆盖（HITL 拒绝后执行类攻击）
    if collect_db:
        try:
            snap_before["db"] = env.db_snapshot()
        except Exception as e:
            payload_note = f"db 快照失败: {e}"
            (snap_before.setdefault("_notes", [])).append(payload_note)
    if collect_sandbox:
        snap_before["sandbox"] = env.sandbox_snapshot()

    events, elapsed, err = env.stream_chat(payload, timeout)

    snap_after: dict = {}
    if collect_db:
        try:
            snap_after["db"] = env.db_snapshot()
        except Exception:
            pass
    if collect_sandbox:
        snap_after["sandbox"] = env.sandbox_snapshot()

    final = ""
    for e in events:
        if e.get("type") == "final_answer":
            final = (e.get("data") or {}).get("content", "") or ""
    tok1 = env.metrics_today_tokens()
    cost_tokens = round(tok1 - tok0, 1) if (tok0 is not None and tok1 is not None) else None
    return RunRecord(events=events, final_answer=final, question=str(case_input.get("message", "")),
                     elapsed=elapsed, cost_tokens=cost_tokens,
                     snapshots={"before": snap_before, "after": snap_after}, error=err)


def score_trial(case, record: RunRecord) -> tuple[str, list[Score]]:
    scores: list[Score] = []
    for ss in case.scorers:
        fn = REGISTRY[ss.type]
        try:
            scores.append(fn(ss.spec, record))
        except Exception as e:  # 评分器崩溃 = 评分器故障，绝不记 agent 失败
            scores.append(Score(scorer=ss.type, verdict="judge_error",
                                evidence=f"{type(e).__name__}: {e}"))

    # cost_gate：以 token 实测对账（成本是一等公民指标）
    max_tok = (case.cost_gate or {}).get("max_tokens")
    if max_tok:
        if record.cost_tokens is None:
            scores.append(Score("cost_gate", "judge_error",
                                evidence="token 指标不可得（/metrics 未采集）"))
        else:
            ok = record.cost_tokens <= max_tok
            scores.append(Score("cost_gate", "pass" if ok else "fail", value=1.0 if ok else 0.0,
                                evidence=f"token {record.cost_tokens:.0f} vs 上限 {max_tok}"))

    # 判定只看 weight>0 的"硬"评分器；weight=0 为软断言（记录进报告，不判败）
    hard = [s for ss, s in zip(case.scorers, scores) if ss.weight > 0]
    if max_tok:
        hard = hard + [scores[-1]]
    verdict_pool = hard or scores

    types = record.event_types()
    sse_ok = bool(types) and types[-1] == "done" and "error" not in types
    level, _ = derive_trial_level(verdict_pool, run_error=record.error,
                                  has_final_answer=bool(record.final_answer.strip()), sse_ok=sse_ok)
    if verdict_pool and all(s.verdict == "judge_error" for s in verdict_pool):
        level = "JUDGE_ERROR"  # 伪级别：该试验整体不可判
    return level, scores


def main() -> int:
    ap = argparse.ArgumentParser(description="Nexus 评测 runner")
    ap.add_argument("--datasets", nargs="*", help="数据集名或路径（缺省=datasets/ 全部 *.json）")
    ap.add_argument("--trials", type=int, default=None, help="覆盖数据集 default_trials")
    ap.add_argument("--base-url", default=os.environ.get("NEXUS_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("NEXUS_API_KEY", ""))
    ap.add_argument("--timeout", type=float, default=300, help="单次 SSE 超时秒")
    ap.add_argument("--diff", default=None, help="与指定 run_id 对比")
    ap.add_argument("--out", default=str(RESULTS_DIR))
    ap.add_argument("--case", default=None, help="只跑匹配的用例（逗号分隔 id 或子串），校准断言用")
    ap.add_argument("--judge-model", default=None,
                    help="LLM-judge 模型（缺省 qwen-plus；配 QWEN_API_KEY 即启用 rubric 评分器）")
    ap.add_argument("--auto-approve", choices=["approve", "reject"], default="approve",
                    help="HITL 审批自动应答（默认 approve；无人值守评测必需）")
    ap.add_argument("--dry-run", action="store_true", help="只校验数据集与评分器装配，不执行")
    args = ap.parse_args()

    # ---- 数据集选择 ----
    if args.datasets:
        paths = [Path(d) if str(d).endswith(".json") else DATASETS_DIR / f"{d}.json" for d in args.datasets]
    else:
        paths = sorted(DATASETS_DIR.glob("*.json"))
    if not paths:
        print("未找到数据集", file=sys.stderr)
        return 2
    datasets: list[Dataset] = [load_dataset(p) for p in paths]
    if args.case:
        keys = [k.strip() for k in args.case.split(",")]
        for d in datasets:
            d.cases = [c for c in d.cases if any(k in c.id for k in keys)]
        datasets = [d for d in datasets if d.cases]
        if not datasets:
            print(f"--case {args.case} 未匹配任何用例", file=sys.stderr)
            return 2
    print(f"[eval] 数据集: {[f'{d.name}@{d.version} ({len(d.cases)}条)' for d in datasets]}")

    env = EnvAdapter(args.base_url, args.api_key or None, _REPO / "backend" / "data" / "outputs",
                     auto_approve=args.auto_approve)

    from testing.eval import judge as judge_mod
    if args.judge_model:
        judge_mod.DEFAULT_MODEL = args.judge_model
    judge_tag = f"{judge_mod.DEFAULT_MODEL}@{judge_mod.PROMPT_VERSION}"

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.out) / run_id
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    cfg_payloads = env.config_payloads()
    fp = RunFingerprint(
        run_id=run_id, git_sha=git_sha(_REPO),
        datasets={f"{d.name}@{d.version}": dataset_fingerprint(d.path) for d in datasets},
        config_snapshot=config_fingerprint_from_payloads(cfg_payloads) if cfg_payloads else None,
        judge=judge_tag,
        notes=[] if cfg_payloads else ["配置快照未采集（API 不可达或服务未启动）——对比可信度降级"],
    )
    if args.dry_run:
        print(f"[eval] dry-run 通过：{sum(len(d.cases) for d in datasets)} 条用例装配合法")
        return 0

    case_results: list[CaseResult] = []
    for d in datasets:
        n_trials = args.trials or d.default_trials
        for case in d.cases:
            needs_db = any(s.type == "db_assert" for s in case.scorers)
            needs_sb = any(s.type == "sandbox_file" for s in case.scorers)
            # 红队前置：攻击载荷入库（毒文档），用例结束自动清理（封闭攻击环境）
            ingested: list[str] = []
            for doc in (case.pre_state or {}).get("ingest_documents", []):
                try:
                    env.ingest_document(doc["name"], doc["content"])
                    ingested.append(doc["name"])
                    print(f"    [pre] 攻击语料入库: {doc['name']}")
                except Exception as e:
                    print(f"    [pre] 攻击语料入库失败: {e}", file=sys.stderr)
            trials: list[TrialResult] = []
            for i in range(case.effective_trials(n_trials)):
                record = run_trial(env, case.input, case.crew, args.timeout, needs_db, needs_sb,
                                   approval_decision=case.auto_approve or args.auto_approve)
                # 原始事件落盘：失败校准的证据链（没有它，断言措辞问题无法诊断）
                (run_dir / "raw" / f"{case.id}_t{i}.json").write_text(
                    json.dumps(record.events, ensure_ascii=False, indent=1), encoding="utf-8")
                level, scores = score_trial(case, record)
                trials.append(TrialResult(index=i, level=level, scores=scores,
                                          elapsed=record.elapsed, cost_tokens=record.cost_tokens,
                                          error=record.error))
                print(f"  [{case.id}] trial#{i + 1}/{case.effective_trials(n_trials)} → {level}"
                      + (f"（{record.error}）" if record.error else ""))
            cr = aggregate_case(case.id, d.name, trials)
            case_results.append(cr)
            print(f"[eval] {case.id}: {cr.level}{' 🔁flaky' if cr.flaky else ''} dist={cr.distribution}")
            if ingested and (case.pre_state or {}).get("cleanup_ingested", True):
                for n in ingested:
                    env.delete_document(n)
                print(f"    [post] 攻击语料已清理: {ingested}")

    result = {
        "fingerprint": fp.as_dict(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "datasets": {d.name: {"version": d.version,
                              "summary": dataset_summary([r for r in case_results if r.dataset == d.name])}
                     for d in datasets},
        "cases": [{
            "case_id": r.case_id, "dataset": r.dataset, "level": r.level, "flaky": r.flaky,
            "distribution": r.distribution, "judge_errors": r.judge_errors, "score_value": r.score_value,
            "trials": [{"index": t.index, "level": t.level, "elapsed": round(t.elapsed, 2),
                        "cost_tokens": t.cost_tokens, "error": t.error,
                        "scores": [{"scorer": s.scorer, "verdict": s.verdict,
                                    "value": s.value, "evidence": s.evidence} for s in t.scores]}
                       for t in r.trials],
        } for r in case_results],
    }

    out_dir = Path(args.out)
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                         encoding="utf-8")

    md = render_report(result)
    if args.diff:
        prev_path = out_dir / args.diff / "result.json"
        if prev_path.exists():
            md += "\n\n" + diff_runs(json.loads(prev_path.read_text(encoding="utf-8")), result)
        else:
            md += f"\n\n（对比目标 {args.diff} 不存在，跳过 diff）"
    (run_dir / "report.md").write_text(md, encoding="utf-8")

    print(f"\n[eval] 完成 → {out_dir / run_id}")
    for dname, d in result["datasets"].items():
        from testing.eval.report import render_summary_line
        print(f"  {dname}: {render_summary_line(d['summary'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
