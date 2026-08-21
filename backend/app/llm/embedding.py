"""阿里云 DashScope Embedding 客户端（Week 4 RAG）

复用 QWEN_API_KEY，调用 OpenAI 兼容的 /v1/embeddings 端点。
- async embed_texts(texts) -> list[list[float]]，批量嵌入（httpx 异步，连接池复用）
- sync embed_texts_sync(texts)：同语义的同步版（工具/后台线程内使用，
  历史上 kb_ingest_tool 与 memory_ltm 各自维护过 sync 实现，batch 上限
  互相矛盾，现已统一收敛到本模块，批量大小由 EMBEDDING_BATCH_SIZE 控制）
- 默认 model=text-embedding-v3，dim=1024（与 pgvector 列一致）
- 失败重试 2 次
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
import requests

from app.config import settings

logger = logging.getLogger("llm.embedding")

# DashScope OpenAI 兼容 embeddings 端点
_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_RETRY_COUNT = 2
_TIMEOUT = 60

# 模块级 httpx.AsyncClient 单例（连接池复用，进程生命周期不关闭）
_async_client: httpx.AsyncClient | None = None
# 模块级 requests.Session 单例（同步路径连接复用）
_sync_session: requests.Session | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
        )
    return _async_client


def _get_sync_session() -> requests.Session:
    global _sync_session
    if _sync_session is None:
        _sync_session = requests.Session()
    return _sync_session


def _get_api_key() -> str:
    api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("缺少 API Key：请设置 QWEN_API_KEY 环境变量")
    return api_key


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量嵌入文本，返回与输入顺序一致的向量列表。

    Args:
        texts: 待嵌入的文本列表（非空）

    Returns:
        list[list[float]]，每个内层向量长度 = settings.EMBEDDING_DIM
    """
    if not texts:
        return []

    api_key = _get_api_key()
    headers = _headers(api_key)
    client = _get_http_client()

    # 按 EMBEDDING_BATCH_SIZE 分批，逐批调用后拼接
    all_embeddings: list[list[float]] = []
    for start in range(0, len(texts), settings.EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + settings.EMBEDDING_BATCH_SIZE]
        batch_embeddings = await _embed_batch(client, api_key, batch, headers)
        all_embeddings.extend(batch_embeddings)
    return all_embeddings


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """同步批量嵌入（工具线程/后台线程内使用，语义同 embed_texts）。

    kb_ingest_tool 与 memory_ltm 的历史 sync 实现已收敛到这里，
    batch 大小统一由 settings.EMBEDDING_BATCH_SIZE 控制。
    """
    if not texts:
        return []

    api_key = _get_api_key()
    headers = _headers(api_key)
    session = _get_sync_session()

    all_embeddings: list[list[float]] = []
    for start in range(0, len(texts), settings.EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + settings.EMBEDDING_BATCH_SIZE]
        payload: dict[str, Any] = {
            "model": settings.EMBEDDING_MODEL,
            "input": batch,
            "dimensions": settings.EMBEDDING_DIM,
            "encoding_format": "float",
        }
        last_exc: BaseException | None = None
        for attempt in range(_RETRY_COUNT + 1):
            resp = session.post(_ENDPOINT, json=payload, headers=headers, timeout=_TIMEOUT)
            if resp.status_code >= 500 and attempt < _RETRY_COUNT:
                logger.warning(
                    "embedding_sync_server_error_retry status=%s attempt=%s",
                    resp.status_code, attempt + 1,
                )
                last_exc = RuntimeError(
                    f"Embedding 服务器错误 {resp.status_code}: {resp.text[:200]}"
                )
                continue
            if not resp.ok:
                # 记录响应体片段，便于诊断 400 的具体原因
                logger.error(
                    "embedding_sync_api_error status=%s batch=%d body=%s",
                    resp.status_code, len(batch), resp.text[:500],
                )
            resp.raise_for_status()
            data = resp.json()["data"]
            if len(data) != len(batch):
                raise ValueError(
                    f"Embedding 数量不匹配：输入 {len(batch)}，返回 {len(data)}"
                )
            # DashScope 返回按 input 顺序排列
            all_embeddings.extend([d["embedding"] for d in data])
            last_exc = None
            break
        if last_exc:
            raise last_exc
    return all_embeddings


async def _embed_batch(
    client: httpx.AsyncClient,
    api_key: str,
    batch: list[str],
    headers: dict[str, str],
) -> list[list[float]]:
    """嵌入单批文本（≤ _BATCH_SIZE 条），带重试。"""
    payload: dict[str, Any] = {
        "model": settings.EMBEDDING_MODEL,
        "input": batch,
        # DashScope text-embedding-v3 支持 dimensions 参数
        "dimensions": settings.EMBEDDING_DIM,
        "encoding_format": "float",
    }
    last_exc: BaseException | None = None
    for attempt in range(_RETRY_COUNT + 1):
        try:
            resp = await client.post(
                _ENDPOINT, json=payload, headers=headers, timeout=_TIMEOUT
            )
            if resp.status_code >= 500 and attempt < _RETRY_COUNT:
                logger.warning(
                    "embedding_server_error_retry status=%s attempt=%s",
                    resp.status_code, attempt + 1,
                )
                last_exc = RuntimeError(f"Embedding 服务器错误 {resp.status_code}: {resp.text[:200]}")
                continue
            resp.raise_for_status()
            data = resp.json()
            # OpenAI 兼容格式：data[i].embedding
            embeddings = [item["embedding"] for item in data["data"]]
            if len(embeddings) != len(batch):
                raise ValueError(
                    f"Embedding 数量不匹配：输入 {len(batch)}，返回 {len(embeddings)}"
                )
            return embeddings
        except httpx.TimeoutException as e:
            last_exc = TimeoutError(f"Embedding 请求超时（{_TIMEOUT}s）")
            if attempt < _RETRY_COUNT:
                logger.warning("embedding_timeout_retry attempt=%s", attempt + 1)
                continue
            raise last_exc from e
        except httpx.RequestError as e:
            last_exc = RuntimeError(f"Embedding 请求失败: {e}")
            if attempt < _RETRY_COUNT:
                logger.warning("embedding_request_error_retry error=%s", e)
                continue
            raise last_exc from e
    if last_exc:
        raise last_exc
    raise RuntimeError("Embedding 请求失败：未知错误")


async def embed_query(text: str) -> list[float]:
    """嵌入单条查询文本，返回单个向量。"""
    vectors = await embed_texts([text])
    return vectors[0]
