# Nexus 架构白皮书

> 多智能体 Web 平台 "Project Nexus" 技术架构文档
> 版本 2.0 | 2026-08-21（基于 HEAD=5ac4c4f 全量代码核对重写）
>
> v1.0 → v2.0 主要修正：记忆系统实为**自建三层**（CrewAI 原生记忆默认关闭）；SSE 事件字典补全至 11 种；HITL 超时为 60s；RAG 升级为语义分块 + 混合检索；新增会话持久化与用户记忆；补充事件循环 offload patch 等关键机制。

---

## 一、系统概览

Nexus 是一个基于 CrewAI 的多智能体 Web 平台，核心目标是**将 CLI 设计的 CrewAI 框架移植到 Web 环境**，实现：

1. **多智能体流式输出** — 前端实时看到 Agent 的思考 token 流与协作步骤
2. **人类在环 (HITL)** — 高危操作暂停等待人类审批（已端到端验证通过）
3. **配置热更新** — 前端修改 Prompt/Tool，后端无需重启即刻生效（默认 Crew id 有 5 分钟 TTL 缓存）
4. **三层记忆** — 会话内滑动窗口+滚动摘要 / 跨会话用户长期记忆 / 知识库预注入
5. **Agent-Team 编排** — hierarchical 模式下 manager agent 自主拆解和委派，委派链路可追踪
6. **Agentic RAG** — 语义分块 + 向量/关键词双路 RRF 融合检索

---

## 二、技术栈

| 层 | 技术选型 | 说明 |
|---|---|---|
| 前端 | Next.js 14 + Tailwind + lucide-react | AppShell 三栏布局，SSE 流式渲染，会话列表 |
| 后端 | FastAPI + SQLAlchemy 2.0 async (asyncpg) | 异步 API，SSE 流式代理 |
| 引擎 | CrewAI 1.9.3（锁定版本） | Agent/Task/Crew 动态装配，sequential/hierarchical |
| LLM | 阿里云通义千问 (DashScope) | 自定义 AliyunLLM：流式输出 + Function Calling + 多模态 (qwen3-vl-plus)；摘要/评估/记忆提取走 qwen-turbo |
| 数据库 | PostgreSQL 16 + pgvector + zhparser | 业务配置 + 向量检索 + 中文全文检索 |
| 缓存 | Redis 7 | HITL 状态机 + 工具执行锁 |
| 部署 | Docker Compose | 4 容器：postgres(自编译 zhparser) + redis + backend + frontend |

---

## 三、分层架构

```
┌─────────────────────────────────────────────────────┐
│              表现层 (Next.js)                         │
│  对话视图(会话列表/步骤流/审批卡) │ 配置控制台 │ 知识库  │
├─────────────────────────────────────────────────────┤
│           网关与控制层 (FastAPI)                      │
│  /v1: chat sessions agents crews tools skills        │
│       documents memories output_schemas approvals    │
├─────────────────────────────────────────────────────┤
│           核心引擎层 (CrewAI + 自研扩展)              │
│  CrewFactory │ ToolRegistry(懒加载) │ 三层记忆        │
│  事件包装/委派追踪 │ crewai_async_patch(offload)      │
├─────────────────────────────────────────────────────┤
│           基础设施层                                  │
│  PostgreSQL(pgvector+zhparser) │ Redis │ 文件沙箱     │
└─────────────────────────────────────────────────────┘
```

---

## 四、核心数据流

### 4.1 对话流（SSE 闭环）

```
用户输入 → POST /v1/chat/stream {message, crew_id, session_id}
  → 三层记忆读路径：
      ① KB 预注入：embedding 相似度 ≥0.65 的知识库片段 top-2
      ② LTM：user_memories 表语义检索用户偏好/经验
      ③ STM：chat_messages 滑动窗口 + 滚动摘要（chat_session_summary）
  → CrewFactory.build_crew_from_db() 从 DB 装配 Crew
  → Crew.akickoff() 异步执行
  → 事件（thinking_token / tool_call / delegation / ...）→ asyncio.Queue → SSE
  → 前端 useChat 消费 SSE → 实时渲染
  → final_answer → done
  → 后台 fire-and-forget：滚动摘要刷新 + LTM 记忆提取（qwen-turbo）
```

### 4.2 HITL 审批流

```
Agent 调用 HumanApprovalTool._run()
  → Redis 写入 PENDING 状态 (TTL 兜底)
  → SSE 推送 approval_requested 事件（含 approval_id/action/risk_level/timeout）
  → 前端渲染审批卡片（消息流内 + 吸顶），3s 轮询 pending 兜底
  → 用户 POST /v1/approvals/{id} {decision}
  → Redis 更新状态
  → 工具轮询检测到状态变更，返回结果
  → Agent 继续/停止；60s 无响应自动 TIMEOUT
```

状态机：`PENDING → APPROVED | REJECTED | TIMEOUT`（TIMEOUT 由工具超时写入，前端展示为超时未执行）。

### 4.3 配置热更新

```
前端 /config 修改 Agent/Tool/Crew → PUT /v1/...
  → DB 更新配置
  → 下次对话请求 build_crew_from_db() 从 DB 重新读取
  → 新 Crew 实例使用最新配置
```

注意：Prompt/Tool/Crew 改动即时生效（Crew 每请求重建）；仅"默认 Crew id"有 TTL=300s 缓存（`factory.get_default_crew_id`），Crew CRUD 时主动失效。

### 4.4 检索流（Agentic RAG）

```
文档入库：上传/网页抓取 → semantic_chunker 语义分块
  → DashScope text-embedding-v3 (1024 维) → document_chunks
  （tsv 列 GENERATED to_tsvector('chinese') 自动生成，zhparser 分词）

检索（rag_search 工具 / KB 预注入）：
  向量路：pgvector cosine_distance，取前 200
  关键词路：zhparser lexemes → OR tsquery（防全 AND 召回失效）→ ts_rank，取前 200
  融合：RRF (k=60) → top_k
```

---

## 五、SSE 事件字典（前后端契约）

后端 `core/events.py` 的 `AgentEvent` 为准，前端 `lib/api-client.ts` 镜像了一份联合类型（**双端手工同步，无单一事实来源，修改时需同步两处**）：

| 事件 | 载荷 | 前端消费 | 说明 |
|---|---|---|---|
| `agent_thinking` | content, step, agent | ✅ | 思考步骤（整段） |
| `thinking_token` | content, step, agent | ✅ | 思考流式 token（STREAMING_WITH_TOOLS_ENABLED） |
| `tool_call` | agent, tool, input | ✅ | 工具调用开始 |
| `tool_result` | agent, tool, output | ✅ | 工具调用结束 |
| `approval_requested` | agent, tool, input{approval_id, action, risk_level, reason, timeout} | ✅ | HITL 审批请求 |
| `token` | content | ✅ | 最终回答分块 |
| `final_answer` | content | ✅ | 最终完整回答 |
| `task_completed` | content, agent, output{task_name, agent, output_format, pydantic_valid, raw_preview} | ⚠️ 已定义未渲染 | 任务级产出（含 pydantic 校验结果） |
| `delegation` | content, agent, input{task, context, coworker} | ⚠️ 已定义未渲染 | manager 委派追踪 |
| `error` | content | ✅ | 错误 |
| `done` | — | ✅ | 流结束哨兵 |

> 历史约定名 `hitl_request` 已废弃，实现为 `approval_requested`。

---

## 六、关键设计决策

### 1. Crew 每请求新建即销毁
`build_crew_from_db()` 每次调用从 DB 重新装配 Crew，执行完丢弃。配置热更新无需缓存失效逻辑（默认 Crew id 缓存除外）。

### 2. SSE 用 StreamingResponse 而非 sse-starlette
后者对 dict 输入有双重包装 bug，直接用 StreamingResponse + `media_type="text/event-stream"`，15s keepalive ping。

### 3. 同步工具 + crewai_async_patch 事件循环 offload
CrewAI 1.9.3 的异步执行循环会在事件循环内**同步内联**调用工具 `_run`。同步工具一旦阻塞（HITL 忙等、Playwright 渲染），整个 FastAPI 事件循环被冻结。`crews/crewai_async_patch.py` 将 `_handle_native_tool_calls` 整体丢入 worker 线程（`asyncio.to_thread` 复制 contextvars，保住流式输出上下文）。
**注意**：该 patch 带版本守卫（仅 crewai==1.9.3 且源码 sentinel 匹配时生效），版本漂移会**静默跳过并告警**——升级 crewai 必须重新验证。

### 4. HITL 用 Redis 状态机而非 CrewAI 原生 human_input
CrewAI 原生 human_input 阻塞线程，无法用于 Web。自定义 HumanApprovalTool 通过同步 Redis 轮询实现异步等待，超时 `DEFAULT_TIMEOUT=60s` 写 TIMEOUT。

### 5. 三层记忆（自建），CrewAI 原生记忆默认关闭
`CREWAI_NATIVE_MEMORY_ENABLED=false`（默认）。原因：① TaskEvaluator 评估有 LLM 开销；② CrewAI STM 不跨请求（每请求新建 Crew 白跑）；③ 需配 embedder。自建三层：

| 层 | 存储 | 机制 | 开关（默认） |
|---|---|---|---|
| L1 STM | PG `chat_messages` | 滑动窗口 + 单条截断 + 总量兜底；滑出窗口的消息由后台 qwen-turbo 增量压缩为滚动摘要（`chat_session_summary`，幂等自愈） | STM_ENABLED=true / STM_SUMMARY_ENABLED=true |
| L2 LTM | PG `user_memories` | 对话结束后后台 qwen-turbo 提取用户偏好/经验；下轮 kickoff 前按语义检索注入 | LTM_USER_MEMORY_ENABLED=true |
| L3 KB 预注入 | PG `document_chunks` | kickoff 前按 embedding 相似度 ≥0.65 取 top-2 片段注入上下文 | KB_PREINJECT_ENABLED=true |

### 6. Skills 注入 backstory 而非 system prompt
CrewAI Agent 的 backstory 是最直接的字段注入点，skill prompt_template 拼接到 backstory 末尾。

### 7. hierarchical 模式 — manager 不能挂 tools + 委派追踪
CrewAI 限制 manager_agent 仅拆解委派。通过包装 `DelegateWorkTool` 推送 `delegation` 事件、monkey-patch `output_pydantic` 为每个子 agent 注入结构化输出 schema（output_schemas 表），任务完成推送 `task_completed`（含 pydantic 校验结果）。
**已知问题**：`_delegation_pydantic_map` 是模块级全局，并发构建多个 hierarchical Crew 时可能互相污染。

### 8. 工具注册表懒加载
`tool_registry.py` 存 `(模块路径, 类名)` 元组，`instantiate_tool` 时才 import，避免启动时全量加载 playwright/python-docx/openpyxl 等重依赖（Week 11 性能优化）。

---

## 七、数据模型

```
AgentConfig (name, role, goal, backstory, llm_model, temperature, max_iter, memory)
    ├── M2M tools  (AgentTool → ToolConfig)
    ├── M2M skills (AgentSkill → SkillConfig)
    └── output_schema_id → OutputSchemaConfig (结构化输出，1:1 可选)

CrewConfig (name, description, process_type, manager_agent_id?)
    ├── M2M agents (CrewAgent, with position)
    ├── 1:N tasks (TaskConfig)
    └── manager_agent → AgentConfig (标量 FK, ondelete=SET NULL)

TaskConfig (crew_id, agent_id?, name, description, expected_output, position, context_task_ids JSONB)

ToolConfig  (name, tool_key, description, config_json JSONB)   # rag_search: top_k；baidu_search: max_results
SkillConfig (name, description, prompt_template, skill_key, config_json)
OutputSchemaConfig (name, description, schema_json JSONB)

DocumentConfig (name, source_type, content_text)
    └── 1:N DocumentChunk (content, embedding Vector(1024), tsv zhparser 生成列, position, metadata_json)

ChatSession (uuid, title, created_at)                          # 迁移 0006，前端会话列表
    └── 1:N ChatMessage (role, content, agent, metadata_json)
ChatSessionSummary (session_id, summary, last_message_id)      # STM 滚动摘要
UserMemory (user_id, content, embedding Vector, source_session_id)  # LTM，迁移 0007
```

Alembic 迁移 0001-0007。启动时 `create_all + ensure_seed` 自动建表/种子（幂等）；Alembic 迁移**不会自动执行**，改表结构后需手动 `alembic upgrade head` 对齐。

种子 Crew：`researcher_writer`（默认，sequential）、`knowledge_qa`、`safety_check`、`safety_review`、`team_orchestrator`（hierarchical）、`web_ingest_crew`（hierarchical，URL→markdown→审阅→入库）。

---

## 八、安全机制（2026-08 P0 加固）

| 机制 | 位置 | 说明 |
|---|---|---|
| 文件沙箱 | `tools/_file_utils.py` | 读写强制 containment：写仅 `{SANDBOX_DATA_DIR}/outputs/**`，读限 `SANDBOX_DATA_DIR/**` + `SANDBOX_EXTRA_READ_DIRS` 白名单；越界抛 `SandboxViolation`，工具返回错误串 |
| API 鉴权 | `core/security.py` + `main.py` | `X-API-Key` 静态密钥（`APP_API_KEY`），空值放行+启动告警；全部 `/v1/*` 挂依赖，`/health` 豁免；前端 `NEXT_PUBLIC_API_KEY` 自动透传 |
| SSRF 防护 | `core/net_guard.py` | `validate_public_url`（scheme 白名单 + DNS 全量解析拒私网/环回/链路本地）+ `safe_get_with_redirects`（重定向逐跳校验）；fetch_url 与 navigate 均接入；`SSRF_ALLOW_PRIVATE_NETWORK` 逃生门 |
| 日志脱敏 | `llm/aliyun_llm.py` | INFO 级只记消息 role/长度与响应摘要；完整报文仅 DEBUG（guarded） |
| 部署口令 | `docker-compose.yml` | postgres 口令与 DSN 走 `${POSTGRES_PASSWORD:-nexus}` 注入；端口映射按部署决定保留 |

## 九、已知限制与演进方向

| 限制 | 影响 | 方向 |
|---|---|---|
| Redis 无密码且映射宿主机 | 局域网可直连 | requirepass（P1 待办） |
| crewai 1.9.3 锁死 | async patch / delegation patch 版本漂移即静默失效 | 升级时重验 + patch 失效 fail-fast（P1-5） |
| AliyunLLM 同步/异步双实现 ~700 行镜像 | 维护成本高，改一处忘一处 | 合并为单实现 |
| factory.py 千余行 | 装配/记忆/patch/缓存混杂 | 拆分模块（P2-2） |
| SSE 事件类型双端手工同步 | 漂移风险（task_completed/delegation 已漏） | codegen 或共享 schema |
| git 中零测试、无 CI | monkey-patch 与 SSE 协议无回归保障 | 提交 testing/ + CI（P1-2，P0 回归测试已就位） |
| 部署仅 dev 模式 | Dockerfile 带 --reload / npm run dev | 生产构建路径 |
| Playwright 渲染路的浏览器内重定向 | 入口 URL 已校验，30x 跳私网属残余风险 | request 拦截（低优先） |
| ivfflat 静态索引 | 大规模语料检索慢 | HNSW 或按数据量调参 |

分优先级的完整优化清单见 [roadmap.md](roadmap.md)。
