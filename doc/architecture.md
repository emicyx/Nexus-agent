# Nexus 架构白皮书

> 多智能体 Web 平台 "Project Nexus" 技术架构文档
> 版本 1.0 | 2026-07-12

---

## 一、系统概览

Nexus 是一个基于 CrewAI 的多智能体 Web 平台，核心目标是**将 CLI 设计的 CrewAI 框架移植到 Web 环境**，实现：

1. **多智能体流式输出** — 前端实时看到 Agent 的思考与协作步骤
2. **人类在环 (HITL)** — 高危操作暂停等待人类审批
3. **配置热更新** — 前端修改 Prompt/Tool，后端无需重启即刻生效
4. **双层记忆** — 短期对话上下文 + 长期记忆持久化
5. **Agent-Team 编排** — hierarchical 模式下 manager agent 自主拆解和委派

---

## 二、技术栈

| 层 | 技术选型 | 说明 |
|---|---|---|
| 前端 | Next.js 14 + Tailwind + Shadcn UI | 三栏式 AppShell，SSE 流式渲染 |
| 后端 | FastAPI + SQLAlchemy 2.0 async | 异步 API，SSE 流式代理 |
| 引擎 | CrewAI 1.9.3 | Agent/Task/Crew 动态装配，process sequential/hierarchical |
| LLM | 阿里云通义千问 (DashScope) | 自定义 AliyunLLM 继承 BaseLLM，支持 Function Calling + 多模态 |
| 数据库 | PostgreSQL 16 + pgvector | 业务配置 + 向量检索（RAG） |
| 缓存 | Redis 7 | HITL 状态机 + 对话历史 |
| 部署 | Docker Compose | 4 容器：postgres + redis + backend + frontend |

---

## 三、分层架构

```
┌─────────────────────────────────────────────────┐
│              表现层 (Next.js)                     │
│  对话视图 │ 配置控制台 │ 审批中心 │ 知识库管理      │
├─────────────────────────────────────────────────┤
│           网关与控制层 (FastAPI)                   │
│  路由鉴权 │ 配置中心 CRUD │ SSE 流式代理           │
├─────────────────────────────────────────────────┤
│           核心引擎层 (CrewAI)                      │
│  CrewFactory │ ToolRegistry │ Memory │ Skills     │
├─────────────────────────────────────────────────┤
│           基础设施层                              │
│  PostgreSQL + pgvector │ Redis │ ChromaDB/SQLite  │
└─────────────────────────────────────────────────┘
```

---

## 四、核心数据流

### 4.1 对话流（SSE 闭环）

```
用户输入 → POST /v1/chat/stream
  → CrewFactory.build_crew_from_db() 从 DB 装配 Crew
  → Crew.akickoff() 异步执行
  → step_callback → asyncio.Queue → SSE event stream
  → 前端 useChat Hook 消费 SSE → 实时渲染
  → final_answer → done
```

### 4.2 HITL 审批流

```
Agent 调用 HumanApprovalTool._run()
  → Redis 写入 PENDING 状态 (TTL=300s)
  → SSE 推送 approval_requested 事件
  → 前端渲染审批卡片
  → 用户 POST /v1/approvals/{id} {decision}
  → Redis 更新状态
  → 工具检测到状态变更，返回结果
  → Agent 继续/停止
```

### 4.3 配置热更新

```
前端 /config 修改 Agent → PUT /v1/agents/{id}
  → DB 更新 AgentConfig
  → 下次对话请求 build_crew_from_db() 从 DB 重新读取
  → 新 Crew 实例使用最新配置
  → 无缓存 → 天然热更新
```

---

## 五、关键设计决策

### 1. Crew 每请求新建即销毁
`build_crew_from_db()` 每次调用从 DB 重新装配 Crew，执行完丢弃。配置热更新无需缓存失效逻辑。

### 2. SSE 用 StreamingResponse 而非 sse-starlette
后者对 dict 输入有双重包装 bug，直接用 StreamingResponse + `media_type="text/event-stream"` 更可控。

### 3. 工具内访问 DB/Redis 用同步客户端
CrewAI `akickoff()` 在主事件循环中调用工具 `_run`，`asyncio.run()` 会报 "Cannot run the event loop while another loop is running"。所有工具内部使用同步 SQLAlchemy engine (psycopg2) 和同步 redis.Redis。

### 4. HITL 用 Redis 状态机而非 CrewAI 原生 human_input
CrewAI 原生 human_input 阻塞线程，无法用于 Web。自定义 HumanApprovalTool 通过 Redis 轮询实现异步等待。

### 5. 双层记忆 — CrewAI 内置 + 对话历史
- **Crew 内置记忆**：`Crew(memory=True)` + DashScope embedder → ShortTermMemory (ChromaDB) + LongTermMemory (SQLite) + EntityMemory (ChromaDB)
- **多轮对话**：Redis 存储最近 3 轮对话摘要，注入 Task description 前缀

### 6. Skills 注入 backstory 而非 system prompt
CrewAI Agent 的 backstory 是最直接的字段注入点，skill prompt_template 拼接到 backstory 末尾。

### 7. hierarchical 模式 — manager_agent 不能挂 tools
CrewAI 限制：manager_agent 仅负责拆解和委派，不能直接调用工具。

---

## 六、数据模型

```
AgentConfig (id, name, role, goal, backstory, llm_model, temperature, max_iter, memory)
    ├── M2M tools (AgentTool → ToolConfig)
    └── M2M skills (AgentSkill → SkillConfig)

CrewConfig (id, name, description, process_type, manager_agent_id?)
    ├── M2M agents (CrewAgent → AgentConfig, with position)
    ├── 1:N tasks (TaskConfig)
    └── manager_agent → AgentConfig (标量 FK, ondelete=SET NULL)

TaskConfig (id, crew_id, agent_id?, name, description, expected_output, position, context_task_ids JSONB)

ToolConfig (id, name, tool_key, description, config_json JSONB)
SkillConfig (id, name, description, prompt_template, skill_key, config_json)

DocumentConfig (id, name, source_type, content_text)
    └── 1:N DocumentChunk (id, document_id, content, embedding Vector(1024), position, metadata_json)
```

---

## 七、已知限制与未来演进

| 限制 | 影响 | 未来方向 |
|---|---|---|
| AliyunLLM 同步 requests.post | chat 期间阻塞事件循环 | 改用 httpx async 或 LLM 线程池 |
| 无分布式工作流引擎 | 服务重启任务丢失 | 引入 Temporal/Prefect |
| 单用户无 RBAC | 无法多租户 | tenant_id 行级隔离 + RBAC |
| 表单式配置 | 非技术人员门槛高 | React Flow 可视化编排画布 |
| 无成本追踪 | 不知 Token 消耗 | 集成 Langfuse |
| ivfflat 静态索引 | 大规模语料检索慢 | 按 HNSW 或数据量调参 |
