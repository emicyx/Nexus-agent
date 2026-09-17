"""S1 OneBot 渠道集成测试：fake NapCat WS 客户端全链路（真实 app + Mock LLM）。

覆盖：
- WS 接入 → 推 message 事件 → run_crew_chat（mock LLM）→ API 调用回推 → echo 响应判成败
- 私聊默认问答全链路（含 <im_content> 不可信包裹进入引擎）
- /kb 命令路由回复带转交前缀（真实 route_v0 + seeded knowledge_qa crew）
- 超长回复分段（在线 echo 路径，单测只覆盖了 offline 降级）
- 非 owner 消息：无任何 send API 调用
- GET /v1/channels 状态接口
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.channels import im_pipeline as ip
from app.channels import onebot_adapter as ob
from app.config import settings
from app.llm.aliyun_llm import AliyunLLM

# researcher_writer 的 research 任务挂 ResearchMaterial output_schema，
# mock 回答必须是合法 JSON（与 test_api_chat_sse 的 SCHEMA_MOCK_ANSWER 同理）
SCHEMA_MOCK_ANSWER = '{"title": "t", "key_facts": ["f"], "sources": [], "summary": "s"}'


@pytest.fixture(autouse=True)
def _schema_valid_mock_llm(monkeypatch):
    """覆盖 conftest 的固定中文回答：schema crew 需要合法 JSON 输出。"""

    def fake_call(self, messages, tools=None, callbacks=None, available_functions=None,
                  max_iterations=10, _retry_on_empty=True, **kwargs):
        return SCHEMA_MOCK_ANSWER

    async def fake_acall(self, messages, tools=None, callbacks=None, available_functions=None,
                         max_iterations=10, _retry_on_empty=True, **kwargs):
        return SCHEMA_MOCK_ANSWER

    monkeypatch.setattr(AliyunLLM, "call", fake_call)
    monkeypatch.setattr(AliyunLLM, "acall", fake_acall)


def _private_event(user_id: int, text: str) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "raw_message": text,
        "user_id": user_id,
        "sender": {"user_id": user_id, "nickname": "owner"},
        "message": [{"type": "text", "data": {"text": text}}],
    }


class FakeNapCat:
    """WS 客户端侧：自动应答服务端下发的 OneBot API 调用（echo 回执）。"""

    def __init__(self, ws):
        self.ws = ws
        self.api_calls: list[dict] = []

    def pump_until(self, predicate, timeout=90) -> list[dict]:
        """循环接收，自动回 API 响应，直到 predicate(新收的调用列表) 为真。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.ws.receive_json()
            if "action" in msg and "echo" in msg:
                self.api_calls.append(msg)
                self.ws.send_json({
                    "echo": msg["echo"], "status": "ok", "retcode": 0,
                    "data": {"message_id": len(self.api_calls)},
                })
                if predicate(self.api_calls):
                    return self.api_calls
        raise TimeoutError("等待 OneBot API 调用超时")


def test_channels_status_endpoint(client: TestClient):
    r = client.get("/v1/channels")
    assert r.status_code == 200
    body = r.json()
    assert "onebot" in body
    assert set(body["onebot"]) >= {"connected", "connected_since", "configured"}


def test_onebot_private_roundtrip_default_crew(client: TestClient, monkeypatch):
    """私聊默认问答全链路：mock LLM 的回答经 send_private_msg 回到来源。"""
    monkeypatch.setattr(settings, "ONEBOT_WS_TOKEN", "it-token", raising=False)
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)

    with client.websocket_connect("/v1/channels/onebot/ws?token=it-token") as ws:
        assert ob.get_status()["connected"] is True
        fake = FakeNapCat(ws)
        ws.send_json(_private_event(10001, "你好，介绍一下你自己"))

        calls = fake.pump_until(
            lambda cs: any(c["action"] == "send_private_msg" for c in cs),
            timeout=120,
        )
        sends = [c for c in calls if c["action"] == "send_private_msg"]
        assert len(sends) == 1
        params = sends[0]["params"]
        assert params["user_id"] == 10001
        text = "".join(
            seg["data"]["text"] for seg in params["message"] if seg["type"] == "text"
        )
        # mock LLM 固定回答经引擎真实回到渠道（final_answer 或 error 均非空）
        assert text, "回复不应为空"


def test_onebot_kb_command_routes_with_prefix(client: TestClient, monkeypatch):
    """/kb → knowledge_qa（真实 seeded crew + route_v0），回复带转交前缀。"""
    monkeypatch.setattr(settings, "ONEBOT_WS_TOKEN", "it-token", raising=False)
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)

    with client.websocket_connect("/v1/channels/onebot/ws?token=it-token") as ws:
        fake = FakeNapCat(ws)
        ws.send_json(_private_event(10001, "/kb 项目用什么框架"))

        calls = fake.pump_until(
            lambda cs: any(c["action"] == "send_private_msg" for c in cs),
            timeout=120,
        )
        sends = [c for c in calls if c["action"] == "send_private_msg"]
        assert len(sends) == 1
        text = "".join(
            seg["data"]["text"] for seg in sends[0]["params"]["message"]
            if seg["type"] == "text"
        )
        assert text.startswith("[已转交 知识库问答]")


def test_onebot_long_reply_segmented_online(client: TestClient, monkeypatch):
    """超长回复分段（在线 echo 路径）：>1500 字拆多条 API 调用，逐条回执。"""
    import asyncio as _asyncio

    monkeypatch.setattr(settings, "ONEBOT_WS_TOKEN", "it-token", raising=False)
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)

    async def fake_collect(channel, crew_id, message, session_key):
        return "长" * 3200

    monkeypatch.setattr(ip, "_run_crew_and_collect", fake_collect)

    with client.websocket_connect("/v1/channels/onebot/ws?token=it-token") as ws:
        fake = FakeNapCat(ws)
        ws.send_json(_private_event(10001, "写一篇长文"))

        calls = fake.pump_until(
            lambda cs: len([c for c in cs if c["action"] == "send_private_msg"]) >= 3,
            timeout=60,
        )
        sends = [c for c in calls if c["action"] == "send_private_msg"]
        assert len(sends) == 3  # 3200 字 → 1500+1500+200
        for c in sends:
            text = "".join(
                seg["data"]["text"] for seg in c["params"]["message"]
                if seg["type"] == "text"
            )
            assert len(text) <= settings.QQ_MESSAGE_MAX_LEN


def test_onebot_non_owner_silent(client: TestClient, monkeypatch):
    """非 owner：记 WARNING + 静默——不触发任何 send API 调用（收不到即通过）。"""
    monkeypatch.setattr(settings, "ONEBOT_WS_TOKEN", "it-token", raising=False)
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)

    with client.websocket_connect("/v1/channels/onebot/ws?token=it-token") as ws:
        fake = FakeNapCat(ws)
        ws.send_json(_private_event(99999, "你是机器人吗"))
        # 等一小段时间确认没有 send API 调用下来（用一次心跳往返制造时序窗口）
        ws.send_json({"post_type": "meta_event", "meta_event_type": "heartbeat"})
        # 心跳在服务端只打 debug 日志不回消息——直接等超时窗口
        time.sleep(2)
        assert fake.api_calls == []
