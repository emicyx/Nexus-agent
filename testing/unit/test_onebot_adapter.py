"""S1 OneBot adapter 单测：原生解析纯函数 + WS 鉴权/单连接 + adapter→管线衔接。

S4 阶段 A 起，入站语义（白名单/路由/会话/串行）在渠道无关的 im_pipeline
（test_im_pipeline.py 覆盖）；本文件只测 adapter 封闭的渠道差异：
- 纯函数：群 @ 判定与剥离（message 数组 + CQ 码回退）、owner env 解析；
- WS 层：token 鉴权拒绝、单连接顶替、离线发送降级（TestClient fake NapCat）；
- 衔接：handle_message_event 把原生事件解析成 InboundMessage（channel=qq、
  群/私聊判定、sender 归一）交给管线，回复闭包回事件来源。

LLM 执行链通过 monkeypatch im_pipeline._run_crew_and_collect 隔离（不打真实 LLM）。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.channels import im_pipeline as ip
from app.channels import onebot_adapter as ob
from app.config import settings


# ── 纯函数（原生事件解析，渠道差异封闭点） ──────────────────────────────

def test_extract_at_segments_array():
    msg = [
        {"type": "at", "data": {"qq": "10001"}},
        {"type": "text", "data": {"text": " 帮我查一下 "}},
    ]
    hit, text = ob.extract_at_and_text(msg, "", self_id="10001")
    assert hit and text == "帮我查一下"
    # @ 了别人不算
    hit2, text2 = ob.extract_at_and_text(msg, "", self_id="99999")
    assert not hit2


def test_extract_at_cq_code_fallback():
    hit, text = ob.extract_at_and_text(None, "[CQ:at,qq=10001] 你好", self_id="10001")
    assert hit and text == "你好"
    hit2, _ = ob.extract_at_and_text(None, "[CQ:at,qq=888] 你好", self_id="10001")
    assert not hit2


def test_extract_at_self_id_empty_never_hits():
    # QQ_BOT_SELF_ID 未配置：一律不处理群消息（fail-closed）
    assert ob.extract_at_and_text(None, "[CQ:at,qq=1] x", self_id="") == (False, "")


def test_owner_ids_parse():
    orig = settings.QQ_OWNER_IDS
    try:
        settings.QQ_OWNER_IDS = "10001,10002，10003"
        assert ob.owner_ids() == {"10001", "10002", "10003"}
        settings.QQ_OWNER_IDS = ""
        assert ob.owner_ids() == set()
    finally:
        settings.QQ_OWNER_IDS = orig


# ── WS 层（fake NapCat 客户端） ──────────────────────────────────────

@pytest.fixture()
def ws_app(monkeypatch):
    """最小 app：只挂 onebot router；固定 token。"""
    from fastapi import FastAPI

    monkeypatch.setattr(settings, "ONEBOT_WS_TOKEN", "test-token", raising=False)
    app = FastAPI()
    app.include_router(ob.router)
    return app


def _msg_event(user_id=10001, text="你好", message_type="private", group_id=None, at_qq=None):
    segs = []
    if at_qq is not None:
        segs.append({"type": "at", "data": {"qq": str(at_qq)}})
    segs.append({"type": "text", "data": {"text": text}})
    ev = {
        "post_type": "message",
        "message_type": message_type,
        "raw_message": text,
        "user_id": user_id,
        "sender": {"user_id": user_id, "nickname": "owner"},
        "message": segs,
    }
    if group_id is not None:
        ev["group_id"] = group_id
    return ev


def test_ws_rejects_bad_token(ws_app):
    with TestClient(ws_app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/v1/channels/onebot/ws?token=wrong"):
                pass


def test_ws_rejects_when_token_unconfigured(monkeypatch):
    # 安全不变量 5：未配置 token = 拒绝一切连接
    from fastapi import FastAPI

    monkeypatch.setattr(settings, "ONEBOT_WS_TOKEN", "", raising=False)
    app = FastAPI()
    app.include_router(ob.router)
    with TestClient(app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/v1/channels/onebot/ws?token="):
                pass


def test_ws_single_connection_replaces_old(ws_app):
    with TestClient(ws_app) as c:
        with c.websocket_connect("/v1/channels/onebot/ws?token=test-token") as ws1:
            assert ob.get_status()["connected"] is True
            with c.websocket_connect("/v1/channels/onebot/ws?token=test-token") as ws2:
                assert ob.get_status()["connected"] is True
                # 旧连接被服务端关闭；TestClient 对 close 帧的呈现是抛
                # WebSocketDisconnect 或返回关闭表示，两种形态都证明顶替已发生
                # （若顶替未发生，receive 将永远阻塞——测试卡死即失败信号）
                closed = False
                try:
                    ws1.receive()
                    closed = True
                except Exception:
                    closed = True
                assert closed
                # 新连接仍然活着（心跳可收发）
                ws2.send_json({"post_type": "meta_event", "meta_event_type": "heartbeat"})
            assert ob.get_status()["connected"] is False


def test_send_qq_message_offline_returns_failed():
    # 无连接时发送降级为 SendResult(ok=False, channel_offline)
    asyncio.run(_offline_send())


async def _offline_send():
    ob._conn.ws = None
    result = await ob.send_qq_message("hello", user_id=1)
    assert result.ok is False
    assert "channel_offline" in result.reason


# ── adapter → im_pipeline 衔接（原生解析 + 回复闭包回事件来源） ──────────────

def test_handle_message_rejects_non_owner(ws_app, monkeypatch):
    """白名单外：静默不回（无 send 调用、无执行）。"""
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)
    called = []
    monkeypatch.setattr(ip, "_run_crew_and_collect", _fake_collect(called))
    monkeypatch.setattr(ob, "send_qq_message", _record_send(called))

    async def run():
        await ob.handle_message_event(_msg_event(user_id=99999))

    asyncio.run(run())
    assert called == []  # 既不执行也不回复


def test_handle_message_routes_kb_command(ws_app, monkeypatch):
    """/kb 命令路由：channel=qq 会话键、执行 knowledge_qa 链路、回复带转交前缀。"""
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)
    monkeypatch.setattr(settings, "QQ_BOT_SELF_ID", "20000", raising=False)
    called = []
    monkeypatch.setattr(
        ip, "_run_crew_and_collect",
        _fake_collect(called, answer="知识库答案"),
    )
    sends = []
    monkeypatch.setattr(ob, "send_qq_message", _record_send(sends, capture=True))
    monkeypatch.setattr(ip, "get_crew_id_by_name", _fake_crew_id(77))
    monkeypatch.setattr(ip, "_ensure_session", _fake_ensure_session(called))

    async def run():
        await ob.handle_message_event(_msg_event(text="/kb 项目用什么框架"))

    asyncio.run(run())
    # 会话键保持 qq: 前缀（前端来源徽标判别依据）；crew 解析为 knowledge_qa 的 id=77
    sessions = [x for x in called if x[0] == "session"]
    assert sessions and sessions[0][1] == "qq:10001:/cmd" and sessions[0][2] == 77
    runs = [x for x in called if x[0] == "run"]
    assert runs and runs[0][1] == "qq" and "<im_content>" in runs[0][2]
    # 回复闭包回事件来源（私聊 user_id）
    assert sends and sends[0]["user_id"] == 10001 and sends[0]["group_id"] is None
    assert sends[0]["text"].startswith("[已转交 知识库问答]")
    assert "知识库答案" in sends[0]["text"]


def test_handle_message_hitl_command_guided_to_web(ws_app, monkeypatch):
    """HITL 定案 A：/write 引导回 Web，不执行任何 crew。"""
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)
    called = []
    monkeypatch.setattr(ip, "_run_crew_and_collect", _fake_collect(called))
    sends = []
    monkeypatch.setattr(ob, "send_qq_message", _record_send(sends, capture=True))

    async def run():
        await ob.handle_message_event(_msg_event(text="/write 帮我写周报"))

    asyncio.run(run())
    assert called == []  # 未执行
    assert "Web 端" in sends[0]["text"]


def test_handle_message_unknown_command_replies_error(ws_app, monkeypatch):
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)
    sends = []
    monkeypatch.setattr(ob, "send_qq_message", _record_send(sends, capture=True))
    called = []
    monkeypatch.setattr(ip, "_run_crew_and_collect", _fake_collect(called))

    async def run():
        await ob.handle_message_event(_msg_event(text="/foo bar"))

    asyncio.run(run())
    assert called == []
    assert "未知命令" in sends[0]["text"]


def test_handle_message_group_requires_at_self(ws_app, monkeypatch):
    """群消息：未 @ 本机器人静默；@ 了才处理（正文剥离 at，回复回群）。"""
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)
    monkeypatch.setattr(settings, "QQ_BOT_SELF_ID", "20000", raising=False)
    called = []
    monkeypatch.setattr(ip, "_run_crew_and_collect", _fake_collect(called, answer="ok"))
    monkeypatch.setattr(ob, "send_qq_message", _record_send(called))
    monkeypatch.setattr(ip, "get_default_crew_id", _fake_default_crew(1))

    async def run_no_at():
        ev = _msg_event(message_type="group", group_id=555, at_qq=888, text="大家好")
        await ob.handle_message_event(ev)

    asyncio.run(run_no_at())
    assert called == []

    async def run_with_at():
        ev = _msg_event(message_type="group", group_id=555, at_qq=20000, text="今天天气")
        await ob.handle_message_event(ev)

    asyncio.run(run_with_at())
    runs = [x for x in called if x[0] == "run"]
    assert runs and "今天天气" in runs[0][2] and "<im_content>" in runs[0][2]
    # 群消息回复走事件来源 group_id（reply-to-source）
    sends = [x for x in called if x[0] == "send"]
    assert sends and sends[0][2] is None and sends[0][3] == 555


def test_session_queue_limit_serializes_and_caps(ws_app, monkeypatch):
    """§4.4（经 adapter 入口）：同一发送者串行，超限（默认 5）回'正在处理中'。"""
    monkeypatch.setattr(settings, "QQ_OWNER_IDS", "10001", raising=False)
    monkeypatch.setattr(ip, "_ensure_session", _fake_ensure_session([]))
    monkeypatch.setattr(ip, "get_default_crew_id", _fake_default_crew(1))
    sends: list[dict] = []
    monkeypatch.setattr(ob, "send_qq_message", _record_send(sends, capture=True))

    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_collect(channel, crew_id, message, session_key):
        started.set()
        await release.wait()
        return "done"

    monkeypatch.setattr(ip, "_run_crew_and_collect", slow_collect)

    async def run():
        try:
            t1 = asyncio.create_task(ob.handle_message_event(_msg_event(text="第一条")))
            await asyncio.wait_for(started.wait(), timeout=5)
            # 处理中再发 6 条：前 5 条排队，第 6 条超限
            tasks = [
                asyncio.create_task(ob.handle_message_event(_msg_event(text=f"排队{i}")))
                for i in range(1, 7)
            ]
            await asyncio.sleep(0.1)  # 让全部排队判定跑完
            release.set()
            await asyncio.gather(t1, *tasks)
        finally:
            ip._session_locks.clear()
            ip._session_pending.clear()

    asyncio.run(run())
    busy = [s for s in sends if "正在处理中" in s["text"]]
    done = [s for s in sends if s["text"] == "done"]
    assert len(busy) == 1, f"超限提示应恰好 1 条，实际 {len(busy)}"
    assert len(done) == 6, f"第一条 + 排队 5 条都应执行，实际 {len(done)}"


# ── fakes ──────────────────────────────────────────────

def _fake_collect(record, answer="ok"):
    async def _collect(channel, crew_id, message, session_key):
        record.append(("run", channel, message, session_key))
        return answer
    return _collect


def _record_send(record, capture=False):
    async def _send(content, *, user_id=None, group_id=None):
        if capture:
            record.append({"text": content, "user_id": user_id, "group_id": group_id})
        else:
            record.append(("send", content, user_id, group_id))
        return ob.SendResult(ok=True, segments=1)
    return _send


def _fake_crew_id(crew_id):
    async def _get(name):
        return crew_id
    return _get


def _fake_default_crew(crew_id):
    async def _get():
        return crew_id
    return _get


def _fake_ensure_session(record):
    async def _ensure(session_uuid, crew_id, first_message, channel):
        record.append(("session", session_uuid, crew_id, first_message, channel))
    return _ensure
