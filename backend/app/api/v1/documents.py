"""文档 RAG 路由（Week 4）

- POST   /v1/documents        上传文本或文件文档
- GET    /v1/documents        列出文档
- DELETE /v1/documents/{id}   删除文档
- GET    /v1/documents/search 调试用语义检索
"""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.document import DocumentCreate, DocumentRead, SearchResult
from app.services import document_service

router = APIRouter()
logger = logging.getLogger("documents")


@router.get("", response_model=list[DocumentRead])
async def list_documents(session: AsyncSession = Depends(get_db)):
    return await document_service.list_documents(session)


@router.post("", response_model=DocumentRead, status_code=201)
async def create_document(
    payload: DocumentCreate,
    session: AsyncSession = Depends(get_db),
):
    """JSON 方式上传：{name, content, source_type?}"""
    try:
        doc = await document_service.ingest_document(
            session, name=payload.name, content=payload.content,
            source_type=payload.source_type,
        )
        # 补 chunk_count（refresh 后 selectin 已加载 chunks）
        chunk_count = len(doc.chunks) if doc.chunks else 0
        return DocumentRead(
            id=doc.id, name=doc.name, source_type=doc.source_type,
            chunk_count=chunk_count, created_at=doc.created_at,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("create_document_failed")
        raise HTTPException(500, f"文档上传失败: {e}")


@router.post("/upload", response_model=DocumentRead, status_code=201)
async def upload_document_file(
    file: UploadFile = File(...),
    name: str = Form(None),
    session: AsyncSession = Depends(get_db),
):
    """multipart 上传：file 字段为文件，name 可选（默认取 filename）。"""
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        # 尝试常见中文编码
        try:
            content = raw.decode("gbk")
        except UnicodeDecodeError as e:
            raise HTTPException(400, f"无法解码文件（需 UTF-8 或 GBK 文本）: {e}")
    doc_name = name or file.filename or "未命名文档"
    try:
        doc = await document_service.ingest_document(
            session, name=doc_name, content=content, source_type="file",
        )
        chunk_count = len(doc.chunks) if doc.chunks else 0
        return DocumentRead(
            id=doc.id, name=doc.name, source_type=doc.source_type,
            chunk_count=chunk_count, created_at=doc.created_at,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("upload_document_failed")
        raise HTTPException(500, f"文件上传失败: {e}")


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: int, session: AsyncSession = Depends(get_db)):
    ok = await document_service.delete_document(session, doc_id)
    if not ok:
        raise HTTPException(404, "Document not found")


@router.get("/search", response_model=list[SearchResult])
async def search_documents(
    q: str,
    top_k: int = 5,
    document_id: int | None = None,
    session: AsyncSession = Depends(get_db),
):
    """调试用语义检索端点。"""
    if not q.strip():
        raise HTTPException(400, "q 不能为空")
    results = await document_service.search_documents(
        session, q, top_k=top_k, document_id=document_id,
    )
    return results
