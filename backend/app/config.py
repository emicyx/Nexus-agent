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
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # 基础设施（Week 3+5 使用）
    POSTGRES_DSN: str = "postgresql://nexus:nexus@postgres:5432/nexus"
    REDIS_URL: str = "redis://redis:6379/0"

    # Embedding 配置（Week 4 RAG，复用 QWEN_API_KEY）
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIM: int = 1024

    # CrewAI 记忆存储路径（Week 8 双层记忆，ChromaDB + SQLite 持久化）
    CREWAI_STORAGE_DIR: str = "/app/data"

    # Week 11 性能优化：LongTermMemory 评估异步执行 + 用小模型。
    # 默认启用（保留长期记忆能力）。
    # 关闭方法：设 CREWAI_LONG_TERM_MEMORY_ENABLED=false（彻底跳过评估，回到 11s）
    CREWAI_LONG_TERM_MEMORY_ENABLED: bool = True

    # Week 11：TaskEvaluator 评估专用 LLM 模型。
    # 默认 qwen-turbo（评估调用从 9-11s 降到 1-2s），与主回答 LLM_MODEL 隔离。
    CREWAI_EVALUATOR_LLM_MODEL: str = "qwen-turbo"

    # 三层记忆系统开关
    # Layer 1 STM（会话内压缩）：默认开
    STM_ENABLED: bool = True
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
