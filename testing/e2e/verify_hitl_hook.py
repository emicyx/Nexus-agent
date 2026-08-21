"""实时验证：hook(hitl_pre_approval) 触发后 HITL 是否阻塞 worker 线程而非事件循环。

触发 web_ingest_crew（kb_ingest 挂了 hitl_pre_approval hook），并发测 /health：
- 若 hook 阻塞发生在事件循环上 → 60s 审批期内 /health 全部超时/缓慢
- 若发生在 worker 线程 → /health 保持毫秒级响应
"""
import asyncio
import json
import statistics
import time

import httpx

BASE = "http://localhost:8000"
HOOK_WINDOW_SLOW_MS = 200  # hook 阻塞窗口内 /health 超过此值视为 loop 被冻结


async def health_checker(stop_evt, samples):
    """每 1s 测一次 /health，记录 (monotonic_ts, latency_ms)。"""
    async with httpx.AsyncClient(timeout=5) as c:
        while not stop_evt.is_set():
            t0 = time.perf_counter()
            try:
                r = await c.get(f"{BASE}/health")
                lat = (time.perf_counter() - t0) * 1000
            except Exception:
                lat = -1.0
            samples.append((time.monotonic(), lat))
            await asyncio.sleep(1)


async def main():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/v1/crews")
        crew = next(cc for cc in r.json() if cc["name"] == "web_ingest_crew")
    crew_id = crew["id"]

    stop_evt = asyncio.Event()
    samples = []
    checker = asyncio.create_task(health_checker(stop_evt, samples))

    t0 = time.perf_counter()
    hook_start_ts = None  # 首次 approval_requested 的 monotonic 时刻
    final = ""
    approval_seen = False
    async with httpx.AsyncClient(timeout=240) as c:
        payload = {
            "message": "请抓取并整理 https://example.com 的内容入库",
            "crew_id": crew_id, "session_id": None, "single": False,
        }
        block = ""
        async with c.stream("POST", f"{BASE}/v1/chat/stream", json=payload) as resp:
            async for line in resp.aiter_lines():
                if line == "":
                    if block.strip():
                        et = data = None
                        for bl in block.strip().split("\n"):
                            if bl.startswith("event: "):
                                et = bl[len("event: "):].strip()
                            elif bl.startswith("data: "):
                                try:
                                    data = json.loads(bl[len("data: "):].strip())
                                except Exception:
                                    data = {}
                        if et == "approval_requested":
                            approval_seen = True
                            hook_start_ts = time.monotonic()
                            print(
                                f"[{time.perf_counter()-t0:.1f}s] approval_requested: "
                                f"{str(data)[:100]}"
                            )
                        elif et == "final_answer":
                            final = data.get("content", "")
                    block = ""
                else:
                    block += line + "\n"
    stop_evt.set()
    await checker
    elapsed = time.perf_counter() - t0

    print(f"\n总耗时 {elapsed:.1f}s；approval_requested(hook 触发)={approval_seen}")

    ok = [lat for _, lat in samples if lat >= 0]
    if ok:
        print(
            f"/health 全局采样 {len(ok)} 次：min={min(ok):.1f}ms "
            f"max={max(ok):.1f}ms avg={statistics.mean(ok):.1f}ms"
        )

    if approval_seen and hook_start_ts is not None:
        window = [lat for ts, lat in samples if ts >= hook_start_ts and lat >= 0]
        if window:
            print(
                f"→ hook 阻塞窗口内采样 {len(window)} 次："
                f"min={min(window):.1f}ms max={max(window):.1f}ms "
                f"avg={statistics.mean(window):.1f}ms"
            )
            slow = [lat for lat in window if lat > HOOK_WINDOW_SLOW_MS]
            print(f"→ 窗口内 >{HOOK_WINDOW_SLOW_MS}ms 的请求数: {len(slow)}")
            assert not slow, f"事件循环被 hook 阻塞! 窗口内 max={max(window):.1f}ms"
            print("✅ 结论：hook 阻塞发生在 worker 线程，事件循环未被冻结")
        else:
            print("⚠️ 窗口内无 /health 采样（期间 /health 全部超时）→ 疑似 loop 被阻塞")
            assert False, "hook 窗口内 /health 全部失败"
    else:
        print("⚠️ 未观测到 approval_requested（LLM 未调用 kb_ingest），结论不成立")

    print(f"final_answer: {final[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
