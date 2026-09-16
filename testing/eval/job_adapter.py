"""S2 job 评测适配器（执行计划 §5.6：eval 零外发红线落地）。

职责：把"触发一个 job 的 eval 演练 → 断言零外发"接进评测 runner：
- run_job_trial：POST /v1/jobs/{id}/run {"eval": true}（同步等待执行完成）→
  拉取 job_run 终态，装进 RunRecord（snapshots.job_run）供评分器只读断言；
- 也可独立执行：python -m testing.eval.job_adapter --job ops_daily_report

零外发硬断言（调研文档 9.1 红线）：pushed_to 必须 == 'suppressed(eval)'，
由 job_zero_egress 评分器判定（testing/eval/datasets/jobs_v1.json 用例挂载）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from testing.eval.scorers.base import RunRecord


class JobAdapterError(RuntimeError):
    pass


def _client(env) -> httpx.Client:
    """复用 EnvAdapter 的 httpx client；独立 CLI 时就地建一个。"""
    client = getattr(env, "client", None)
    if client is not None:
        return client
    return httpx.Client(
        base_url=getattr(env, "base_url", "http://localhost:8000"),
        headers=({"X-API-Key": env.api_key} if getattr(env, "api_key", None) else {}),
        timeout=300.0,
    )


def resolve_job_id(env, job_name: str) -> int:
    resp = _client(env).get("/v1/jobs")
    if resp.status_code != 200:
        raise JobAdapterError(f"GET /v1/jobs 失败: HTTP {resp.status_code}")
    for job in resp.json():
        if job.get("name") == job_name:
            return int(job["id"])
    raise JobAdapterError(f"job 未找到: {job_name}（seed 未同步或名称不符）")


def fetch_run(env, job_id: int, run_id: int) -> dict:
    resp = _client(env).get(f"/v1/jobs/{job_id}/runs", params={"limit": 50})
    if resp.status_code != 200:
        raise JobAdapterError(f"GET /v1/jobs/{job_id}/runs 失败: HTTP {resp.status_code}")
    for run in resp.json():
        if int(run["id"]) == run_id:
            return run
    raise JobAdapterError(f"run {run_id} 不在最近 50 条里")


def trigger_job_eval(env, job_name: str, timeout: float = 300.0) -> dict:
    """触发 eval 演练并返回终态 run dict（带 _elapsed 私有字段）。"""
    job_id = resolve_job_id(env, job_name)
    t0 = time.time()
    resp = _client(env).post(f"/v1/jobs/{job_id}/run", json={"eval": True}, timeout=timeout)
    if resp.status_code != 200:
        raise JobAdapterError(
            f"POST /v1/jobs/{job_id}/run 失败: HTTP {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    run = fetch_run(env, job_id, int(body["run_id"]))
    run["_elapsed"] = round(time.time() - t0, 2)
    return run


def run_job_trial(env, case_input: dict, timeout: float) -> RunRecord:
    """runner 的 job 用例入口：与 run_trial 同构产出 RunRecord。

    events 合成 job_run + done（终态完整语义）；snapshots.job_run 是
    job_zero_egress 评分器的数据源；cost_tokens 走 /metrics 差值（同 run_trial）。
    """
    job_name = case_input.get("job") or ""
    record = RunRecord(question=str(case_input.get("message", "")))
    tok0 = env.metrics_today_tokens()
    try:
        run = trigger_job_eval(env, job_name, timeout=timeout)
    except (JobAdapterError, httpx.HTTPError) as e:
        record.error = f"job 触发失败: {e}"
        return record
    tok1 = env.metrics_today_tokens()
    record.cost_tokens = round(tok1 - tok0, 1) if (tok0 is not None and tok1 is not None) else None

    status = run.get("status", "")
    record.elapsed = run.get("_elapsed", 0.0)
    record.snapshots = {"job_run": {k: v for k, v in run.items() if not k.startswith("_")}}
    if status in ("failed", "disabled_by_circuit"):
        record.error = f"job run 终态 {status}: {run.get('error') or ''}"
        return record
    record.final_answer = (
        f"job {job_name} eval 演练完成（status={status}，pushed_to={run.get('pushed_to')}）"
    )
    record.events = [
        {"type": "job_run", "data": {"job": job_name, "status": status,
                                     "pushed_to": run.get("pushed_to")}},
        {"type": "done", "data": {}},
    ]
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="S2 job eval 演练（零外发断言，可独立执行）")
    ap.add_argument("--job", default="ops_daily_report", help="job 名（缺省 ops_daily_report）")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="")
    args = ap.parse_args()

    class _Env:
        base_url = args.base_url
        api_key = args.api_key or None

    try:
        run = trigger_job_eval(_Env(), args.job)
    except (JobAdapterError, httpx.HTTPError) as e:
        print(f"❌ 触发失败: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in run.items() if not k.startswith("_")},
                     ensure_ascii=False, indent=1))
    if run.get("pushed_to") != "suppressed(eval)":
        print(f"❌ 零外发断言失败: pushed_to={run.get('pushed_to')!r}（期望 'suppressed(eval)'）",
              file=sys.stderr)
        return 1
    print("✅ 零外发断言通过：pushed_to == 'suppressed(eval)'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
