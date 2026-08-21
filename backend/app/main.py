"""FastAPI 应用入口"""
import asyncio
import logging

from fastapi import Depends, FastAPI
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
from app.core.security import require_api_key
from app.crews.crewai_async_patch import is_applied as is_async_patch_applied
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
    # P0-2 鉴权提示：APP_API_KEY 未设置 = 接口裸奔（本地开发可接受，部署务必设置）
    if not settings.APP_API_KEY:
        logger.warning(
            "APP_API_KEY 未设置：所有 /v1/* 接口无鉴权。"
            "部署环境请在 .env 中设置 APP_API_KEY，并给前端配 NEXT_PUBLIC_API_KEY。"
        )
    # P1-5 fail-fast：async patch 未生效（crewai 版本漂移/源码变化被守卫跳过）时，
    # 同步工具（HITL 忙等 60s / Playwright 渲染 90s）会冻结整个事件循环。
    # 升级 crewai 后必须重新适配 patch，明确降级请设 CREWAI_PATCH_REQUIRED=false。
    if settings.CREWAI_PATCH_REQUIRED and not is_async_patch_applied():
        raise RuntimeError(
            "crewai_async_patch 未生效（版本漂移或源码变化被守卫跳过）。"
            "此时同步工具将阻塞事件循环，拒绝启动。"
            "升级 crewai 后请重新适配 patch，或显式设置 CREWAI_PATCH_REQUIRED=false 降级。"
        )
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


# 路由挂载（全部走 X-API-Key 鉴权，APP_API_KEY 留空时放行；/health 豁免）
_api_key_dep = [Depends(require_api_key)]
app.include_router(chat_router, prefix="/v1/chat", dependencies=_api_key_dep)
app.include_router(chat_sessions_router, prefix="/v1/chat/sessions", dependencies=_api_key_dep)
app.include_router(agents_router, prefix="/v1/agents", dependencies=_api_key_dep)
app.include_router(tools_router, prefix="/v1/tools", dependencies=_api_key_dep)
app.include_router(skills_router, prefix="/v1/skills", dependencies=_api_key_dep)
app.include_router(output_schemas_router, prefix="/v1/schemas", dependencies=_api_key_dep)
app.include_router(crews_router, prefix="/v1/crews", dependencies=_api_key_dep)
app.include_router(documents_router, prefix="/v1/documents", dependencies=_api_key_dep)
app.include_router(approvals_router, prefix="/v1/approvals", dependencies=_api_key_dep)
app.include_router(memories_router, prefix="/v1/memories", dependencies=_api_key_dep)
