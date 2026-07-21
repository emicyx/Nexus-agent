"""FastAPI 应用入口"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.agents import router as agents_router
from app.api.v1.approvals import router as approvals_router
from app.api.v1.chat import router as chat_router
from app.api.v1.chat_sessions import router as chat_sessions_router
from app.api.v1.crews import router as crews_router
from app.api.v1.documents import router as documents_router
from app.api.v1.memories import router as memories_router
from app.api.v1.output_schemas import router as output_schemas_router
from app.api.v1.skills import router as skills_router
from app.api.v1.tools import router as tools_router
from app.config import settings
from app.crews.factory import get_llm
from app.db.seed import ensure_seed
from app.db.session import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("main")


app = FastAPI(title="Project Nexus API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    """启动时建表 + 写入种子数据 + 预热 LLM 连接。"""
    logger.info("startup: initializing DB...")
    try:
        await init_db()
        await ensure_seed()
        logger.info("startup: DB ready")
    except Exception as e:  # 建表失败不阻塞启动（如 PG 未就绪）
        logger.warning(f"startup: DB init failed (非致命): {e}")

    # Week 11 性能优化：异步预热 LLM 网络连接（DNS/TLS/连接池），
    # 避免首次 chat 承担 DashScope 冷启动延迟。仅做 TCP/TLS 握手，不发
    # chat completion 请求，零 token 消耗。失败不阻塞启动，仅在后台跑。
    async def _warmup_llm() -> None:
        try:
            llm = get_llm()
            await asyncio.to_thread(_warmup_llm_sync, llm)
            logger.info("startup: LLM warmup ok (TLS handshake)")
        except Exception as e:
            logger.warning(f"startup: LLM warmup failed (非致命): {e}")

    asyncio.create_task(_warmup_llm())


def _warmup_llm_sync(llm) -> None:
    """同步预热：用 AliyunLLM 的内部 Session 做 HEAD 请求，建立 TCP+TLS+连接池会话。

    后续 chat 调用会复用同一 Session 的 keep-alive 连接，避免每次重新握手。
    """
    try:
        session = llm._get_session()
        # HEAD 请求通常返回 405/404，目的只是建立 TCP+TLS+连接池会话。
        resp = session.head(llm.endpoint, timeout=10)
        logger.info(
            "startup: LLM warmup endpoint=%s status=%s",
            llm.endpoint, resp.status_code,
        )
    except Exception as e:
        # 网络异常不阻塞，仅记录
        logger.warning(f"startup: LLM warmup request failed: {e}")


@app.get("/health")
def health():
    return {"status": "ok"}


# 路由挂载
app.include_router(chat_router, prefix="/v1/chat")
app.include_router(chat_sessions_router, prefix="/v1/chat/sessions")
app.include_router(agents_router, prefix="/v1/agents")
app.include_router(tools_router, prefix="/v1/tools")
app.include_router(skills_router, prefix="/v1/skills")
app.include_router(output_schemas_router, prefix="/v1/schemas")
app.include_router(crews_router, prefix="/v1/crews")
app.include_router(documents_router, prefix="/v1/documents")
app.include_router(approvals_router, prefix="/v1/approvals")
app.include_router(memories_router, prefix="/v1/memories")
