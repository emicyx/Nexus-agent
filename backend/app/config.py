"""应用配置 - 通过 Pydantic Settings 从环境变量读取"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM 配置
    QWEN_API_KEY: str
    BAIDU_API_KEY: str = ""
    LLM_MODEL: str = "qwen-plus"
    LLM_REGION: str = "cn"
    LLM_TEMPERATURE: float = 0.7
    LLM_TIMEOUT: int = 600

    # 服务配置
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # API 鉴权（P0-2）：X-API-Key 静态密钥。留空 = 不鉴权（本地开发兼容，启动时告警）。
    # 设置后所有 /v1/* 接口强制校验；前端需同步配置 NEXT_PUBLIC_API_KEY。
    APP_API_KEY: str = ""

    # 文件工具沙箱（P0-1）：所有工具的读写限制在 SANDBOX_DATA_DIR 内。
    # 写入仅允许 {SANDBOX_DATA_DIR}/outputs/**；读取允许 outputs/** 与
    # screenshots/**（产物目录）+ SANDBOX_EXTRA_READ_DIRS——data 根的其余
    # 部分（CrewAI 内部存储 .db / .crewai_user.json）不可读，防越权读取进 prompt。
    SANDBOX_DATA_DIR: str = "/app/data"
    SANDBOX_EXTRA_READ_DIRS: list[str] = []

    # SSRF 防护（P0-3）：fetch_url / navigate 拒绝私网/环回/链路本地地址。
    # 内网部署场景如需抓取内网页面：
    # - 精确方式（推荐）：SSRF_EXTRA_ALLOW_CIDRS 指定网段白名单（P1-8）
    # - 粗放方式：SSRF_ALLOW_PRIVATE_NETWORK=true 全局放行（含云元数据 169.254.169.254，
    #   仅在内网与外界完全隔离时才可接受）
    SSRF_ALLOW_PRIVATE_NETWORK: bool = False
    # SSRF 内网白名单：CIDR 列表（如 ["10.0.1.0/24"]），命中的私网地址放行。
    # 用于内网部署只需抓取个别内网服务的场景，替代全局放行。
    SSRF_EXTRA_ALLOW_CIDRS: list[str] = []

    # API 文档端点（/docs /redoc /openapi.json）：None=按 APP_ENV 推断（dev 开、
    # 其余关）；显式 true/false 覆盖。生产关闭避免暴露完整 API 结构（P1-9）。
    DOCS_ENABLED: bool | None = None

    # 聊天接口限流（P0-5）：每分钟每调用方（X-API-Key 或客户端 IP）最大
    # /v1/chat/stream 请求数，固定窗口 Redis 计数（不可用时内存兜底）。0 = 不限。
    CHAT_RATE_LIMIT_PER_MIN: int = 20
    # 并发 SSE 运行上限（P0-5）：每个请求一个 Crew + worker 线程 + SSE 队列，
    # 超过返回 503，防并发打满构成成本型 DoS。0 = 不限。
    MAX_CONCURRENT_RUNS: int = 8

    # crewai monkey-patch 守卫（P1-5）：async patch 未生效时拒绝启动。
    # crewai 版本漂移/源码变化会让 patch 静默跳过，届时 HITL 忙等、Playwright
    # 渲染会重新冻结事件循环。明确知道自己要降级运行时才设 false。
    CREWAI_PATCH_REQUIRED: bool = True

    # ── 上线加固（欠账清单修复）────────────────────────────────────
    # 运行环境：production 下建表失败直接终止启动（避免带空库接流量），其余环境宽容。
    APP_ENV: str = "dev"
    # 显式覆盖建表严格模式：true=无条件 fail-fast，false=无条件宽容，None=按 APP_ENV 推断。
    DB_INIT_STRICT: bool | None = None
    # 种子演示数据开关：默认 false（生产安全方向——忘配时宁可不灌演示数据）。
    # 本地开发想要开箱即用的演示 Agent/工具，在 .env 设 SEED_DEMO_DATA=true；
    # 生产环境保持 false，用文档上传接口导入真实语料。
    SEED_DEMO_DATA: bool = False

    # SSE 事件队列上限：慢客户端背压保护。满时丢弃最旧事件（final_answer/error
    # 走 await put 有背压，不受影响），避免内存无界增长。
    SSE_QUEUE_MAXSIZE: int = 200

    # Token 日预算（prompt+completion 合计，跨会话累计，Redis 持久 + 内存兜底）。
    # 0 = 不限。超限拒绝新请求并返回 budget_exceeded 错误；进行中的会话可跑完当前轮。
    LLM_TOKEN_DAILY_BUDGET: int = 0

    # 上下文窗口逃生口：>0 时覆盖按模型名查表的值（防止查表过时误导 CrewAI 裁剪）。
    LLM_CONTEXT_WINDOW_OVERRIDE: int = 0

    # 沙箱产物清理：outputs/screenshots 下超过保留天数的文件定期删除，防磁盘写满。
    SANDBOX_RETENTION_DAYS: int = 7
    SANDBOX_CLEANUP_INTERVAL_HOURS: int = 24
    # 磁盘用量告警阈值（百分比），每次清理任务运行时检查并打告警日志。
    DISK_USAGE_WARN_PCT: int = 85

    # 优雅停机：停止接新会话后，给在跑 Crew 的收尾窗口（秒），超时强制取消。
    SHUTDOWN_GRACE_SECONDS: int = 60

    # 基础设施（Week 3+5 使用）
    POSTGRES_DSN: str = "postgresql://nexus:nexus@postgres:5432/nexus"
    REDIS_URL: str = "redis://redis:6379/0"

    # Embedding 配置（Week 4 RAG，复用 QWEN_API_KEY）
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIM: int = 1024
    # 单次请求最大条数：统一 async/sync 所有 embedding 路径。
    # 历史上三处实现各用 6/10/25（DashScope 对 batch 上限的反馈不一致），
    # 取实测最稳的 10 作为默认；如确定账号支持更大 batch 可调高。
    EMBEDDING_BATCH_SIZE: int = 10

    # CrewAI 记忆存储路径（Week 8 双层记忆，ChromaDB + SQLite 持久化）
    CREWAI_STORAGE_DIR: str = "/app/data"

    # Week 11 性能优化：LongTermMemory 评估异步执行 + 用小模型。
    # 默认启用（保留长期记忆能力）。
    # 关闭方法：设 CREWAI_LONG_TERM_MEMORY_ENABLED=false（彻底跳过评估，回到 11s）
    CREWAI_LONG_TERM_MEMORY_ENABLED: bool = True

    # Week 11：TaskEvaluator 评估专用 LLM 模型。
    # 默认 qwen-turbo（评估调用从 9-11s 降到 1-2s），与主回答 LLM_MODEL 隔离。
    CREWAI_EVALUATOR_LLM_MODEL: str = "qwen-turbo"

    # Week 15：CrewAI 内置记忆总开关（默认关闭）。
    # 项目使用自己搭建的三层记忆（STM/LTM/KB，见 memory_stm/memory_ltm/document_service），
    # CrewAI 内置记忆（ShortTermMemory ChromaDB + LongTermMemory SQLite + EntityMemory）默认不启用，
    # 避免：① TaskEvaluator LLM 评估开销；② STM 不跨请求（每请求新建 Crew 白跑）；③ 需配 embedder。
    # 如确需开启：设 CREWAI_NATIVE_MEMORY_ENABLED=true（同时需 embedder 指向 DashScope + CREWAI_STORAGE_DIR）。
    CREWAI_NATIVE_MEMORY_ENABLED: bool = False

    # 三层记忆系统开关
    # Layer 1 STM 滚动摘要：滑出窗口的旧消息增量压缩为滚动摘要（后台 qwen-turbo，fire-and-forget）。
    # 关闭方法：设 STM_SUMMARY_ENABLED=false（回到纯滑动窗口，早期上下文直接丢弃）
    STM_SUMMARY_ENABLED: bool = True
    STM_SUMMARY_LLM_MODEL: str = "qwen-turbo"
    STM_SUMMARY_MAX_CHARS: int = 1200  # 摘要注入 context 的长度上限
    # Layer 2 LTM（用户偏好/经验，跨会话语义检索）：默认开
    LTM_USER_MEMORY_ENABLED: bool = True
    # LTM 提取专用 LLM（后台线程，fire-and-forget）
    LTM_EXTRACTOR_LLM_MODEL: str = "qwen-turbo"
    # Layer 3 KB 预注入（kickoff 前高置信知识库片段注入）
    KB_PREINJECT_ENABLED: bool = True
    KB_PREINJECT_THRESHOLD: float = 0.65
    KB_PREINJECT_TOP_K: int = 2

    # fetch_url 工具：requests 抓取失败/内容过短/命中反爬关键词时，降级到 Playwright 浏览器渲染。
    # 关闭方法：设 FETCH_URL_PLAYWRIGHT_FALLBACK=false（仅用 requests，失败直接报错）
    FETCH_URL_PLAYWRIGHT_FALLBACK: bool = True

    # 流式 LLM + tool_calls 支持（使有工具的 agent 也能实时推送思考 token）
    # 关闭方法：设 STREAMING_WITH_TOOLS_ENABLED=false（回退到不流式）
    STREAMING_WITH_TOOLS_ENABLED: bool = True


settings = Settings()
