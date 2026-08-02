"""阿里云 DashScope Embedding 客户端（Week 4 RAG）

复用 QWEN_API_KEY，调用 OpenAI 兼容的 /v1/embeddings 端点。
- async embed_texts(texts) -> list[list[float]]，批量嵌入
- 默认 model=text-embedding-v3，dim=1024（与 pgvector 列一致）
- 原生异步 httpx.AsyncClient（连接池复用），失败重试 2 次
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("llm.embedding")

# DashScope OpenAI 兼容 embeddings 端点
_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_RETRY_COUNT = 2
_TIMEOUT = 60
# DashScope 兼容模式单次请求最多 10 条输入（实测 batch=20 报 400），超出需分批
_BATCH_SIZE = 10

# 模块级 httpx.AsyncClient 单例（连接池复用，进程生命周期不关闭）
_async_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
        )
    return _async_client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量嵌入文本，返回与输入顺序一致的向量列表。

    Args:
        texts: 待嵌入的文本列表（非空）

    Returns:
        list[list[float]]，每个内层向量长度 = settings.EMBEDDING_DIM
    """
    if not texts:
        return []

    api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("缺少 API Key：请设置 QWEN_API_KEY 环境变量")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    client = _get_http_client()

    # 按 _BATCH_SIZE 分批（DashScope 单次最多 10 条），逐批调用后拼接
    all_embeddings: list[list[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        batch_embeddings = await _embed_batch(client, api_key, batch, headers)
        all_embeddings.extend(batch_embeddings)
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
