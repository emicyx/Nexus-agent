"""S2 定时任务与推送集成测试（真实 app + Mock LLM + 真实 DB/Redis）。

覆盖（§5.8）：
- jobs CRUD：创建/校验（422 非法 cron）/重名 409/PATCH 启停/seed 默认 job 在列
- 手动 run（eval:true）：job_runs 记 status=eval + pushed_to=suppressed(eval)，
  且 onebot send 调用数为 0（零外发硬断言，安全不变量 2）
- 手动 run（非 eval，渠道在线）：mock 出口收到推送（runner 兜底路径）
- 熔断：max_consecutive_failures=1 的 job 失败一次即自动停用，
  run 状态 disabled_by_circuit，job.enabled=False
- kb_delta：真实 DB 增量查询 + 渲染
"""
import pytest
from fastapi.testclient import TestClient

from app.channels import onebot_adapter as ob
from app.config import settings


@pytest.fixture()
def no_send(monkeypatch):
    """mock 唯一出口：记录调用且不真实发送。"""
    calls = []

    async def fake_send(content, *, user_id=None, group_id=None):
        calls.append({"content": content, "user_id": user_id, "group_id": group_id})
        return ob.SendResult(ok=True, segments=1)

    monkeypatch.setattr(ob, "send_qq_message", fake_send)
    return calls


def _patrol_crew_id(client: TestClient) -> int:
    crews = client.get("/v1/crews").json()
    for c in crews:
        if c["name"] == "ops_patrol":
            return c["id"]
    raise AssertionError("seed 未创建 ops_patrol crew（SEED_DEMO_DATA 未生效？）")


@pytest.fixture()
def cleanup_jobs(client: TestClient):
    """测试自清理：登记创建的 job id，结束后 DELETE（防遗留行污染开发库）。"""
    created: list[int] = []
    yield created
    for jid in created:
        client.delete(f"/v1/jobs/{jid}")


def _make_job(client: TestClient, cleanup: list[int], **overrides) -> dict:
    """建测试 job（默认 enabled=False：即使遗留也不会被调度器注册）。"""
    payload = {
        "name": "it-job",
        "trigger_type": "interval",
        "trigger_config": {"seconds": 3600},
        "crew_id": _patrol_crew_id(client),
        "input_template": "{{date}} 巡检",
        "output_config": {"push": {"on": "always"}},
        "enabled": False,
    }
    payload.update(overrides)
    resp = client.post("/v1/jobs", json=payload)
    assert resp.status_code == 201, resp.text
    cleanup.append(resp.json()["id"])
    return resp.json()


def test_jobs_seed_defaults_present(client: TestClient):
    jobs = client.get("/v1/jobs").json()
    names = {j["name"] for j in jobs}
    assert {"ops_patrol", "ops_daily_report", "kb_daily_digest"} <= names
    patrol = next(j for j in jobs if j["name"] == "ops_patrol")
    assert patrol["trigger_type"] == "interval"
    assert patrol["trigger_config"] == {"seconds": 1800}
    assert patrol["output_config"]["push"]["on"] == "state_change"
    digest = next(j for j in jobs if j["name"] == "kb_daily_digest")
    assert digest["trigger_config"] == {"expr": "0 21 * * *"}  # 用户定的晚间回顾时间
    assert digest["output_config"]["push"]["on"] == "always"


def test_job_crud_validation(client: TestClient, cleanup_jobs: list[int]):
    crew_id = _patrol_crew_id(client)
    # 非法 cron / interval / push 策略 / 未知占位符
    for payload in (
        {"name": "bad1", "trigger_type": "cron", "trigger_config": {"expr": "0 8 * *"},
         "crew_id": crew_id},
        {"name": "bad2", "trigger_type": "interval", "trigger_config": {"seconds": 0},
         "crew_id": crew_id},
        {"name": "bad3", "trigger_type": "interval", "trigger_config": {"seconds": 60},
         "crew_id": crew_id, "output_config": {"push": {"on": "whenever"}}},
        {"name": "bad4", "trigger_type": "interval", "trigger_config": {"seconds": 60},
         "crew_id": crew_id, "input_template": "{{what_is_this}}"},
    ):
        assert client.post("/v1/jobs", json=payload).status_code == 422

    ok = {
        "name": "it-job-crud",
        "trigger_type": "cron",
        "trigger_config": {"expr": "5 4 * * *"},
        "crew_id": crew_id,
        "input_template": "{{date}} 例行任务",
        "output_config": {"push": {"on": "always"}},
        "max_consecutive_failures": 3,
        "enabled": False,
    }
    resp = client.post("/v1/jobs", json=ok)
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    cleanup_jobs.append(job_id)
    # 重名 409；不存在的 crew 404
    assert client.post("/v1/jobs", json=ok).status_code == 409
    bad = dict(ok, name="it-job-crew404", crew_id=999999)
    assert client.post("/v1/jobs", json=bad).status_code == 404
    # PATCH 停用/启用
    resp = client.patch(f"/v1/jobs/{job_id}", json={"enabled": False})
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    # runs 列表 404 on 不存在 job
    assert client.get("/v1/jobs/999999/runs").status_code == 404
    # 手动触发已停用 job 允许（manual 跳过 enabled 检查）
    assert client.post(f"/v1/jobs/{job_id}/run", json={"eval": True}).status_code == 200


def test_job_manual_run_eval_zero_egress(client: TestClient, no_send, monkeypatch, cleanup_jobs: list[int]):
    """零外发硬断言：eval run 落 suppressed(eval)，唯一出口调用数为 0。"""
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)
    job = _make_job(client, cleanup_jobs, name="it-job-eval",
                    input_template="{{date}} 巡检演练")

    resp = client.post(f"/v1/jobs/{job['id']}/run", json={"eval": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "eval"
    assert body["pushed_to"] == "suppressed(eval)"
    assert no_send == []  # 唯一出口零调用

    runs = client.get(f"/v1/jobs/{job['id']}/runs").json()
    assert runs and runs[0]["status"] == "eval"
    assert runs[0]["pushed_to"] == "suppressed(eval)"
    assert runs[0]["result_summary"].get("events_tail") is not None
    # eval run 不计入熔断
    jobs = {j["id"]: j for j in client.get("/v1/jobs").json()}
    assert jobs[job["id"]]["consecutive_failures"] == 0


def test_job_manual_run_real_push_online(client: TestClient, no_send, monkeypatch, cleanup_jobs: list[int]):
    """非 eval + 渠道在线：策略 always → mock LLM 未调工具，runner 兜底推送 final answer。"""
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", '{"type":"private","user_id":42}')
    monkeypatch.setattr(ob, "get_status",
                        lambda: {"connected": True, "connected_since": 1.0})
    job = _make_job(client, cleanup_jobs, name="it-job-push")

    resp = client.post(f"/v1/jobs/{job['id']}/run", json={"eval": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "succeeded"
    assert resp.json()["pushed_to"] == "qq"
    assert len(no_send) == 1
    assert no_send[0]["user_id"] == 42 and no_send[0]["group_id"] is None
    # 连续失败计数被成功清零
    jobs = {j["id"]: j for j in client.get("/v1/jobs").json()}
    assert jobs[job["id"]]["consecutive_failures"] == 0


def test_job_manual_run_offline_falls_back_honestly(client: TestClient, no_send, monkeypatch, cleanup_jobs: list[int]):
    """渠道离线：pushed_to 如实记 failed(channel_offline)，不静默丢。"""
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", '{"type":"private","user_id":42}')
    monkeypatch.setattr(ob, "get_status",
                        lambda: {"connected": False, "connected_since": None})
    from app.services import egress as egress_mod

    monkeypatch.setattr(egress_mod, "_OFFLINE_RETRY_WAIT_S", 0)
    job = _make_job(client, cleanup_jobs, name="it-job-offline")
    resp = client.post(f"/v1/jobs/{job['id']}/run", json={"eval": False})
    assert resp.status_code == 200
    assert resp.json()["pushed_to"] == "failed(channel_offline)"
    assert no_send == []


def test_job_circuit_breaker_trips(client: TestClient, no_send, monkeypatch, cleanup_jobs: list[int]):
    """max_consecutive_failures=1：失败一次即自动停用并记 disabled_by_circuit。"""
    import app.crews.factory as factory_mod
    from app.crews.factory import JobOutcome

    async def failing_job(crew_id, message, *, token_session=None):
        oc = JobOutcome()
        oc.error = "模拟执行失败"
        return oc

    monkeypatch.setattr(factory_mod, "run_crew_job", failing_job)
    monkeypatch.setattr(settings, "NEXUS_EVAL_MODE", False)
    monkeypatch.setattr(settings, "QQ_ALERT_TARGET", '{"type":"private","user_id":42}')
    monkeypatch.setattr(ob, "get_status",
                        lambda: {"connected": True, "connected_since": 1.0})
    job = _make_job(client, cleanup_jobs, name="it-job-circuit",
                    enabled=True,  # 熔断只对启用中的 job 生效；teardown DELETE 兜底
                    output_config={"push": {"on": "state_change"}},  # 无变化不推送告警本体
                    max_consecutive_failures=1)

    resp = client.post(f"/v1/jobs/{job['id']}/run", json={"eval": False})
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled_by_circuit"

    jobs = {j["id"]: j for j in client.get("/v1/jobs").json()}
    assert jobs[job["id"]]["enabled"] is False
    assert jobs[job["id"]]["consecutive_failures"] == 1
    # 熔断告警经唯一出口真实下发（一条）
    assert len(no_send) == 1 and "自动停用" in no_send[0]["content"]
    # 再跑一次（manual）仍允许；调度侧已移除注册不影响手动验证
    resp2 = client.post(f"/v1/jobs/{job['id']}/run", json={"eval": False})
    assert resp2.status_code == 200


def test_kb_delta_through_digest_job(client: TestClient, cleanup_jobs: list[int]):
    """{{kb_delta}} 真实链路：建文档 → daily_digest job eval 运行成功
    （kb_delta 在应用事件循环内对真实 DB 查询；渲染细节单测覆盖）。"""
    resp = client.post("/v1/documents", json={
        "name": "S2 集成测试增量文档", "content": "这是头部预览内容，用于日报渲染断言。"
    })
    assert resp.status_code in (200, 201), resp.text

    crews = client.get("/v1/crews").json()
    digest_crew = next(c for c in crews if c["name"] == "daily_digest")
    job = _make_job(client, cleanup_jobs, name="it-job-digest",
                    crew_id=digest_crew["id"],
                    trigger_type="cron", trigger_config={"expr": "30 21 * * *"},
                    input_template="{{date}}\n{{kb_delta}}")
    resp = client.post(f"/v1/jobs/{job['id']}/run", json={"eval": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "eval"
    runs = client.get(f"/v1/jobs/{job['id']}/runs").json()
    assert runs[0]["status"] == "eval" and runs[0]["error"] is None
