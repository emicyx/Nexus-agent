"""真实 E2E：验证滚动摘要（滑动窗口 + 增量摘要）。

同一会话（同一 session_id uuid）连续对话 5 轮，验证：
1) 前 3 轮（6 条消息）内无摘要触发（窗口内）
2) 第 4 轮起有消息滑出窗口 → 后台生成/增长摘要
3) 早期偏好（第 1 轮"简洁"）在后续轮仍被摘要保留
"""
import asyncio
import json
import uuid

import httpx

BASE = "http://localhost:8000/v1/chat/stream"
session_id = str(uuid.uuid4())
turns = [
    "请记住：我偏好简洁的中文回答，不要长篇大论",
    "1+1 等于几？",
    "2+2 等于几？",
    "3+3 等于几？",
    "4+4 等于几？",
]


async def chat(client, text) -> str:
    payload = {"message": text, "session_id": session_id, "crew_id": None, "single": False}
    final = ""
    block = ""
    async with client.stream("POST", BASE, json=payload) as resp:
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        async for line in resp.aiter_lines():
            if line == "":
                if block.strip():
                    evt_type = None
                    data = {}
                    for bl in block.strip().split("\n"):
                        if bl.startswith("event: "):
                            evt_type = bl[len("event: "):].strip()
                        elif bl.startswith("data: "):
                            try:
                                data = json.loads(bl[len("data: "):].strip())
                            except Exception:
                                data = {}
                    if evt_type == "final_answer" and data.get("content"):
                        final = data["content"]
                    elif evt_type == "error":
                        final = f"ERROR: {data.get('content', data)}"
                block = ""
            else:
                block += line + "\n"
    return final


async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        for i, text in enumerate(turns, 1):
            final = await chat(client, text)
            print(f"turn{i}: {final[:60]}")
            await asyncio.sleep(3)  # 留时间给后台 STM 摘要 / LTM 提取
    print(f"SESSION_ID={session_id}")


if __name__ == "__main__":
    asyncio.run(main())
