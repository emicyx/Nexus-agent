"""KB 增量查询（v2 S2：日报数据源，SQL 为准）。

kb_delta(since)：documents.created_at > since 的新增文档 + 各自头部 chunk
摘要，按 source_type 分组。供 {{kb_delta}} 占位符渲染进 daily_digest crew。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models import DocumentChunk, DocumentConfig

logger = logging.getLogger("services.kb_delta")

# 单文档头部预览长度（日报只需要两三句的素材）
_HEAD_PREVIEW_CHARS = 200
# 兜底窗口：job 无历史成功运行时按最近 24h 取增量
_DEFAULT_WINDOW = timedelta(hours=24)


async def kb_delta(since: datetime | None = None) -> dict[str, Any]:
    """新增文档增量（SQL 为准）。since 缺省 = 24h 前。"""
    if since is None:
        since = datetime.now(timezone.utc) - _DEFAULT_WINDOW
    async with AsyncSessionLocal() as db:
        # 文档 + 分块数（一次聚合，避免 N+1）
        chunk_counts = dict(
            (
                await db.execute(
                    select(DocumentChunk.document_id, func.count(DocumentChunk.id))
                    .group_by(DocumentChunk.document_id)
                )
            ).all()
        )
        docs = (
            (
                await db.execute(
                    select(DocumentConfig)
                    .where(DocumentConfig.created_at > since)
                    .order_by(DocumentConfig.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for d in docs:
            head = ""
            if d.chunks:  # selectin 已加载，按 position 排序取头部
                head = (d.chunks[0].content or "").strip()[:_HEAD_PREVIEW_CHARS]
            groups.setdefault(d.source_type or "unknown", []).append({
                "id": d.id,
                "name": d.name,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "chunk_count": chunk_counts.get(d.id, len(d.chunks)),
                "head_preview": head,
            })
    total = sum(len(v) for v in groups.values())
    return {"since": since.isoformat(), "count": total, "groups": groups}


def render_kb_delta(delta: dict[str, Any]) -> str:
    """增量结构 → 人类可读文本（{{kb_delta}} 渲染结果）。"""
    if not delta.get("count"):
        return f"（自 {delta.get('since', '')} 起知识库无新增文档）"
    lines = [f"自 {delta['since']} 起新增 {delta['count']} 篇文档："]
    for source, items in sorted(delta.get("groups", {}).items()):
        source_label = {"web": "网页入库", "text": "文本导入", "file": "文件上传"}.get(source, source)
        lines.append(f"\n[{source_label}]")
        for it in items:
            preview = it.get("head_preview") or "（无内容预览）"
            preview = preview.replace("\n", " ")
            lines.append(f"- 《{it['name']}》（{it['created_at'][:16] if it['created_at'] else ''}，{it['chunk_count']} 块）：{preview}")
    lines.append(
        "\n以上头部预览可能被截断；如需某篇文档更多细节，可用 rag_search 检索其内容。"
    )
    return "\n".join(lines)
