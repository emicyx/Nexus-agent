"""用户长期记忆（Layer 2 LTM）— 偏好/事实/经验摘要

跨会话持久化（PostgreSQL + pgvector），按 crew_id 隔离。
- search_relevant_memories(crew_id, query_vec, top_k): 异步语义检索，kickoff 前调用
- extract_memories_async(crew_id, session_id, user_input, assistant_output): 后台线程
  用 qwen-turbo 提取用户偏好/事实/经验 → embed → 入库（fire-and-forget）

与 CrewAI 内置 LongTermMemory（TaskEvaluator 任务质量评估）职责不同、互不干扰。
"""
import json
import logging
import os
import concurrent.futures
from datetime import datetime

from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models import UserMemory

logger = logging.getLogger("memory.ltm")

# ---------- 后台提取线程池 ----------
_ltm_extract_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="ltm-extract",
)
_ltm_extractor_llm = None  # AliyunLLM 单例，延迟初始化

# ---------- 同步 DB engine（后台线程内使用） ----------
_sync_engine = None
_SyncSessionLocal = None


def _get_sync_session() -> Session:
    """延迟初始化同步 DB engine（psycopg2），供后台线程使用。"""
    global _sync_engine, _SyncSessionLocal
    if _SyncSessionLocal is None:
        dsn = settings.POSTGRES_DSN
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        _sync_engine = create_engine(dsn, pool_pre_ping=True, future=True)
        _SyncSessionLocal = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return _SyncSessionLocal()


def _get_extractor_llm():
    """提取专用 LLM（qwen-turbo），与主回答 LLM 隔离。"""
    global _ltm_extractor_llm
    if _ltm_extractor_llm is None:
        from app.llm.aliyun_llm import AliyunLLM
        model = getattr(
            settings, "LTM_EXTRACTOR_LLM_MODEL", "qwen-turbo"
        )
        _ltm_extractor_llm = AliyunLLM(
            model=model,
            api_key=settings.QWEN_API_KEY,
            region=settings.LLM_REGION,
            temperature=0.3,
            timeout=60,
        )
    return _ltm_extractor_llm


# ---------- 异步检索（kickoff 前调用） ----------


async def search_relevant_memories(
    crew_id: int, query_vec: list[float], top_k: int = 3
) -> list[dict]:
    """语义检索当前 crew 的用户记忆，按 cosine 距离升序取 top_k。

    返回 [{"id", "memory_type", "content", "distance"}]。
    """
    if not query_vec:
        return []
    try:
        vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"
        async with AsyncSessionLocal() as db:
            stmt = sa_text(
                """
                SELECT id, memory_type, content,
                       embedding <=> CAST(:vec AS vector) AS distance
                FROM user_memories
                WHERE crew_id = :crew_id
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :top_k
                """
            )
            result = await db.execute(
                stmt,
                {"vec": vec_str, "crew_id": crew_id, "top_k": top_k},
            )
            rows = result.fetchall()
            memories = [
                {
                    "id": r[0],
                    "memory_type": r[1],
                    "content": r[2],
                    "distance": float(r[3]),
                }
                for r in rows
            ]
            # 更新 use_count + last_used_at（best-effort）
            if memories:
                ids = [m["id"] for m in memories]
                await db.execute(
                    sa_text(
                        "UPDATE user_memories SET use_count = use_count + 1, "
                        "last_used_at = NOW() WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
                await db.commit()
            return memories
    except Exception as e:
        logger.warning(f"search_relevant_memories failed: {e}")
        return []


def build_ltm_prefix(memories: list[dict]) -> str:
    """格式化用户记忆为 task description 前缀。"""
    if not memories:
        return ""
    type_label = {
        "user_preference": "用户偏好",
        "user_fact": "用户事实",
        "experience_summary": "经验摘要",
    }
    lines = ["以下是从历史交互中提取的用户偏好与经验，请参考：\n"]
    for m in memories:
        label = type_label.get(m["memory_type"], m["memory_type"])
        lines.append(f"- [{label}] {m['content']}")
    lines.append("\n--- 以上为用户记忆 ---\n")
    return "\n".join(lines)


# ---------- 后台提取（kickoff 后 fire-and-forget） ----------

_EXTRACT_SYSTEM_PROMPT = """你是一个记忆提取器。从以下用户与助手的对话中，提取值得长期记住的用户信息。
只提取跨会话有价值的内容（如用户偏好、用户身份事实、经验总结），忽略一次性细节（如本次具体问题的答案）。
输出 JSON 数组，每项格式：
{"memory_type": "user_preference" | "user_fact" | "experience_summary", "content": "简洁陈述"}
- user_preference: 用户对回答风格/语言/格式等的偏好
- user_fact: 用户的身份、领域、常驻场景等事实
- experience_summary: 从本次对话中可提炼的通用经验
若无值得记忆的内容，返回空数组 []。
只输出 JSON，不要其他文字。"""


def extract_memories_async(
    crew_id: int,
    session_id: int,
    user_input: str,
    assistant_output: str,
) -> None:
    """提交到后台线程：LLM 提取偏好 → embed → 入库。fire-and-forget。"""
    _ltm_extract_executor.submit(
        _run_memory_extraction, crew_id, session_id, user_input, assistant_output,
    )


def _run_memory_extraction(
    crew_id: int,
    session_id: int,
    user_input: str,
    assistant_output: str,
) -> None:
    """后台线程：提取用户记忆 → embed → 同步入库。失败仅记日志。"""
    import time
    t0 = time.perf_counter()
    try:
        # 1. LLM 提取
        llm = _get_extractor_llm()
        user_msg = f"用户: {user_input[:1000]}\n\n助手: {assistant_output[:1000]}"
        raw = llm.call(
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        # 解析 JSON
        raw_str = raw.strip() if isinstance(raw, str) else str(raw).strip()
        # 容错：去掉可能的 ```json 包裹
        if raw_str.startswith("```"):
            raw_str = raw_str.split("\n", 1)[1] if "\n" in raw_str else raw_str[3:]
            if raw_str.endswith("```"):
                raw_str = raw_str[:-3]
            raw_str = raw_str.strip()
        items = json.loads(raw_str)
        if not isinstance(items, list) or not items:
            logger.info("ltm extract: no memories extracted")
            return

        # 2. 过滤 + 截断 content
        valid_types = {"user_preference", "user_fact", "experience_summary"}
        candidates = []
        for it in items:
            if not isinstance(it, dict):
                continue
            mtype = it.get("memory_type", "")
            content = it.get("content", "").strip()
            if mtype in valid_types and content:
                candidates.append({"memory_type": mtype, "content": content[:500]})
        if not candidates:
            logger.info("ltm extract: no valid memories after filter")
            return

        # 3. 批量 embed
        contents = [c["content"] for c in candidates]
        embeddings = _embed_texts_sync(contents)
        if len(embeddings) != len(candidates):
            logger.warning(
                f"ltm extract: embedding count mismatch {len(embeddings)} != {len(candidates)}"
            )
            return

        # 4. 同步入库
        with _get_sync_session() as db:
            for c, emb in zip(candidates, embeddings):
                vec_str = "[" + ",".join(str(x) for x in emb) + "]"
                db.execute(
                    sa_text(
                        """
                        INSERT INTO user_memories
                            (crew_id, memory_type, content, source_session_id, embedding, use_count)
                        VALUES
                            (:crew_id, :mtype, :content, :sid, CAST(:vec AS vector), 0)
                        """
                    ),
                    {
                        "crew_id": crew_id,
                        "mtype": c["memory_type"],
                        "content": c["content"],
                        "sid": session_id,
                        "vec": vec_str,
                    },
                )
            db.commit()

        logger.info(
            "timing: ltm extract %.3fs (model=%s, extracted=%d)",
            time.perf_counter() - t0,
            getattr(settings, "LTM_EXTRACTOR_LLM_MODEL", "qwen-turbo"),
            len(candidates),
        )
    except Exception as e:
        logger.warning(f"ltm extract failed: {e}")


# ---------- 同步 embedding（后台线程内使用） ----------

_EMBED_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"


def _embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """同步批量 embed（后台线程内调用，不能用 asyncio）。"""
    import requests
    api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("缺少 QWEN_API_KEY")
    payload = {
        "model": settings.EMBEDDING_MODEL,
        "input": texts,
        "dimensions": settings.EMBEDDING_DIM,
        "encoding_format": "float",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # DashScope 限制每次最多 25 条
    all_embeddings = []
    for i in range(0, len(texts), 25):
        batch = texts[i : i + 25]
        payload["input"] = batch
        resp = requests.post(_EMBED_ENDPOINT, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        all_embeddings.extend([item["embedding"] for item in data["data"]])
    return all_embeddings
