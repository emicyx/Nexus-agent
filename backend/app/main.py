"""FastAPI 应用入口

生命周期采用 lifespan（原 on_event 已弃用）：startup 建表/种子/预热/清理调度，
shutdown 优雅停机（B4：停止接新会话 → 等在跑 Crew 收尾 → 强制取消 → 关连接）。
"""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

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
from app.core import run_control
from app.core.middleware import RequestIDMiddleware, install_request_id_logging
from app.core.metrics import render_metrics
from app.core.sandbox_cleanup import (
    start_cleanup_scheduler,
    stop_cleanup_scheduler,
)
from app.core.security import require_api_key
from app.crews.crewai_async_patch import is_applied as is_async_patch_applied
from app.crews.factory import get_llm
from app.db.redis import close_async_redis, close_sync_redis, get_async_redis
from app.db.seed import ensure_seed
from app.db.session import AsyncSessionLocal, dispose_engines, init_db

logging.basicConfig(
    level=logging.INFO,
    # B1：request_id 由 record factory 注入，全链路日志（含 worker 线程）可按请求串联
    format="%(asctime)s [%(name)s] %(levelname)s [req:%(request_id)s] %(message)s",
)
install_request_id_logging()
logger = logging.getLogger("main")


def _db_init_strict() -> bool:
    """A8：生产模式建表失败必须 fail-fast（避免带空库接流量）。"""
    if settings.DB_INIT_STRICT is not None:
        return settings.DB_INIT_STRICT
    return settings.APP_ENV.lower() in ("production", "prod")


def _is_production() -> bool:
    return settings.APP_ENV.lower() in ("production", "prod")


def _validate_runtime_security() -> None:
    """P0-4：生产模式鉴权缺失必须 fail-fast。

    APP_API_KEY 留空时所有 /v1/* 免鉴权——本地开发可接受（启动告警），
    生产忘配等于把烧钱的 LLM 接口和数据 CRUD 直接暴露，宁可拒绝启动。
    前端经 Next 服务端代理注入密钥（浏览器 bundle 不再包含），部署只需
    在 .env 配置本变量。
    """
    if _is_production() and not settings.APP_API_KEY:
        raise RuntimeError(
            "APP_ENV=production 但 APP_API_KEY 未设置：所有 /v1/* 接口将无鉴权暴露，"
            "拒绝启动。请在 .env 配置 APP_API_KEY（前端经服务端代理自动注入，"
            "无需再配 NEXT_PUBLIC_API_KEY）。"
        )


def _docs_enabled() -> bool:
    """P1-9：API 文档端点默认仅 dev 开放；DOCS_ENABLED 显式覆盖。"""
    if settings.DOCS_ENABLED is not None:
        return settings.DOCS_ENABLED
    return settings.APP_ENV.lower() == "dev"


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
    except Exception:
        # 网络异常不阻塞，仅记录
        logger.exception("startup: LLM warmup request failed")


async def _startup() -> None:
    """启动时建表 + 写入种子数据 + 预热 LLM 连接 + 启动沙箱清理调度。"""
    # P0-4：生产模式鉴权缺失 fail-fast（比照 CREWAI_PATCH_REQUIRED 的模式）
    _validate_runtime_security()
    # P0-2 鉴权提示：APP_API_KEY 未设置 = 接口裸奔（本地开发可接受，部署务必设置）
    if not settings.APP_API_KEY:
        logger.warning(
            "APP_API_KEY 未设置：所有 /v1/* 接口无鉴权。"
            "部署环境请在 .env 中设置 APP_API_KEY（前端经服务端代理注入，"
            "无需配置 NEXT_PUBLIC_API_KEY）。"
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
        # A9 数据卫生：生产环境不灌测试种子（KB/LTM 用真实语料另行导入）
        if settings.SEED_DEMO_DATA:
            await ensure_seed()
        else:
            logger.info("startup: SEED_DEMO_DATA=false，跳过种子数据")
        logger.info("startup: DB ready")
    except Exception:
        if _db_init_strict():
            # A8：生产模式 fail-fast——空库接流量比启动失败更糟
            logger.exception("startup: DB init 失败（严格模式，终止启动）")
            raise
        logger.exception("startup: DB init failed (非致命，本地宽容模式)")

    # Week 11 性能优化：异步预热 LLM 网络连接（DNS/TLS/连接池），
    # 避免首次 chat 承担 DashScope 冷启动延迟。仅做 TCP/TLS 握手，不发
    # chat completion 请求，零 token 消耗。失败不阻塞启动，仅在后台跑。
    async def _warmup_llm() -> None:
        try:
            llm = get_llm()
            await asyncio.to_thread(_warmup_llm_sync, llm)
            logger.info("startup: LLM warmup ok (TLS handshake)")
        except Exception:
            logger.exception("startup: LLM warmup failed (非致命)")

    asyncio.create_task(_warmup_llm())

    # A6：沙箱产物定期清理（outputs/screenshots 保留期外删除 + 磁盘告警）
    start_cleanup_scheduler()


async def _shutdown() -> None:
    """B4 优雅停机：停接新会话 → 等在跑 Crew 收尾（默认 60s）→ 强制取消 → 关连接。"""
    run_control.begin_shutdown()
    try:
        remaining = await run_control.wait_for_runs_to_finish(
            settings.SHUTDOWN_GRACE_SECONDS
        )
        if remaining:
            run_control.cancel_all_runs()
            # 强制取消传播窗口：worker 线程检查点退出需要时间。
            # 注意（架构局限，run_control 模块文档有述）：取消只在检查点生效，
            # 线程内进行中的 LLM HTTP 调用无法中断，会跑完该次调用。
            leftover = await run_control.wait_for_runs_to_finish(10)
            if leftover:
                logger.warning(
                    "shutdown: %d 个运行未在传播窗口内退出（线程内调用无法硬中断），"
                    "继续关闭连接", leftover,
                )
    except Exception:
        logger.exception("shutdown: 等待运行收尾失败")
    stop_cleanup_scheduler()
    # 回收浏览器工具的线程本地实例（否则 chromium/node driver 子进程成孤儿）
    try:
        from app.tools.playwright_tools import BrowserManager

        closed = BrowserManager.close_all()
        if closed:
            logger.info("shutdown: 已关闭 %d 个浏览器实例", closed)
    except Exception:
        logger.exception("shutdown: 浏览器回收失败")
    with contextlib.suppress(Exception):
        await close_async_redis()
    with contextlib.suppress(Exception):
        close_sync_redis()
    with contextlib.suppress(Exception):
        await dispose_engines()
    logger.info("shutdown: 完成，再见")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


# P1-9：/docs /redoc /openapi.json 默认仅 dev 开放（生产暴露完整 API 结构）
_docs = _docs_enabled()
app = FastAPI(
    title="Project Nexus API",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

# B1 request-id 最内层（CORS 保持最外层，标准顺序）
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # 显式收敛（避免 origins 误配 * 时 methods/headers 再放大暴露面）；
    # 前端只用到这些。新增自定义头时在此同步追加。
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
)


@app.get("/health")
async def health():
    """真实健康检查（A7）：探 DB SELECT 1 + Redis ping，任一失败返回 503。

    供 compose healthcheck / UptimeRobot 使用——之前的静态 "ok" 会在
    DB 挂掉时给出虚假的安全感。
    """
    checks: dict[str, str] = {}
    ok = True
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        ok = False
        checks["db"] = f"error: {type(e).__name__}"
        logger.error("health: db check failed: %s", e)
    try:
        await get_async_redis().ping()
        checks["redis"] = "ok"
    except Exception as e:
        ok = False
        checks["redis"] = f"error: {type(e).__name__}"
        logger.error("health: redis check failed: %s", e)
    if not ok:
        return JSONResponse(status_code=503, content={"status": "error", "checks": checks})
    return {"status": "ok", "checks": checks}


@app.get("/metrics", dependencies=[Depends(require_api_key)])
def metrics():
    """B3：Prometheus 文本格式指标（SSE 并发/队列深度/工具 P95/token 用量）。

    不引入 prometheus-client；采集器带 X-API-Key 头抓取即可。
    """
    return PlainTextResponse(
        render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8"
    )


# 路由挂载（全部走 X-API-Key 鉴权，APP_API_KEY 留空时放行；/health、/metrics 豁免各自处理）
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
