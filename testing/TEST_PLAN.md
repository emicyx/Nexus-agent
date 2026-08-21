# Nexus 测试与评审规划（落地版）

> 生成时间：2026-08-01
> 目的：为评价 Nexus 项目最终成果设计完整测试矩阵与评审标准。三层递进：纯逻辑单测 → API 集成（Mock LLM）→ 真实 E2E smoke。

## 一、被测范围（功能地图）

| 模块 | 关键文件 | 功能 |
|---|---|---|
| SSE 事件协议 | `backend/app/core/events.py` | `AgentEvent` / `format_sse` / `event_stream`（9 种事件类型） |
| 短期记忆 | `backend/app/services/memory_stm.py` | `compress_history` 三级压缩 / `build_history_context` |
| 工具注册表 | `backend/app/crews/tool_registry.py` | `instantiate_tool` 参数化实例化（22 个 tool_key） |
| DSN 转换 | `backend/app/db/session.py` | `_make_dsn` asyncpg 前缀 |
| 文档切块 | `backend/app/services/document_service.py` | `_split_chunks` / 混合检索 RRF / KB 预注入 |
| LLM 适配 | `backend/app/llm/aliyun_llm.py` | 多模态归一化 / 流式累积 / 重试熔断 |
| HITL 状态机 | `backend/app/db/redis.py` + `tools/human_approval_tool.py` | PENDING→APPROVED/REJECTED/TIMEOUT |
| Crew 工厂 | `backend/app/crews/factory.py` | DB 驱动装配 / 占位符替换 / embedder / 热更新 |
| REST API | `backend/app/api/v1/*.py`（11 router） | agents/tools/skills/crews/schemas/documents/sessions/approvals/memories/chat |
| 前端 | `frontend/src/**` | SSE 解析 / 会话恢复 / 审批卡 / 配置表单 |

## 二、测试矩阵

### Tier 1：纯逻辑单元测试（`testing/unit/`，无需 DB/网络）

| 文件 | 被测对象 | 用例 |
|---|---|---|
| `test_events.py` | `format_sse` / `event_stream` | ① 格式 `event: <type>\ndata: <json>\n\n` ② None 字段省略 ③ 中文保留 ④ 队列按序产出 ⑤ None 哨兵→`done` ⑥ 超时→`: ping` |
| `test_memory_stm.py` | `compress_history` / `build_history_context` | ① 滑窗留 6 ② 单条截断 500 ③ 总量兜底 3000 且 ≥2 ④ 空输入 ⑤ 格式化标签 |
| `test_tool_registry.py` | `instantiate_tool` | ① rag_search+top_k ② baidu_search+max_results ③ 未注册→KeyError ④ 无参默认 |
| `test_session.py` | `_make_dsn` | ① postgresql://→asyncpg ② 已 asyncpg 不变 ③ 密码特殊字符 |
| `test_document_chunks.py` | `_split_chunks` | ① 段落分段 ② 超 500 硬切 ③ 空输入 |
| `test_aliyun_llm.py` | `_normalize_multimodal_tool_result` / `_call_streaming` / `_do_call` | ① tool 消息 base64→image_url 块 ② ReAct 模式 ③ flush 合成 user ④ 流式 content/tool_calls 互斥 ⑤ arguments 跨 chunk 累积 ⑥ 5xx/429 重试 ⑦ 空响应熔断 |
| `test_approvals.py` | redis 审批函数 / `HumanApprovalTool._run` | ① `approval_key` 格式 ② 写入 PENDING ③ APPROVED/REJECTED 更新 ④ 不存在返回 False ⑤ 工具轮询→approved ⑥ 超时→TIMEOUT |
| `test_factory.py` | `_substitute_user_input` / `_build_embedder_config` | ① 占位符替换 ② 多次出现 ③ 其他 `{}` 不报错 ④ embedder 指向 DashScope |

### Tier 2：API 集成测试（`testing/integration/`，Docker PG+Redis，Mock LLM）

| 文件 | 覆盖端点 | 用例 |
|---|---|---|
| `test_api_health.py` | `GET /health` | 200 + `{"status":"ok"}` |
| `test_api_agents.py` | `/v1/agents` `/{id}/tools` `/{id}/skills` | CRUD、挂载/清空工具技能、422、404 |
| `test_api_tools.py` | `/v1/tools` `/options` | CRUD、options 含全部注册 key |
| `test_api_skills.py` | `/v1/skills` | CRUD |
| `test_api_crews.py` | `/v1/crews` `/tasks` | CRUD、manager_agent_id、agents 有序、tasks 子资源 |
| `test_api_schemas.py` | `/v1/schemas` | OutputSchema CRUD |
| `test_api_documents.py` | `/v1/documents` `/upload` `/search` | 文本入库、文件上传、检索带分数、删除级联 |
| `test_api_sessions.py` | `/v1/chat/sessions` | list/get/create/patch/delete、messages 详情 |
| `test_api_approvals.py` | `/v1/approvals` | pending/list、get、approve/reject、400、404 |
| `test_api_memories.py` | `/v1/memories` | 按 crew 列表、删除 |
| `test_api_chat_sse.py` | `POST /v1/chat/stream` | Mock LLM 下 SSE 事件序列与逐行格式（最核心） |

### Tier 3：真实 E2E smoke（`testing/e2e/`，真实 QWEN_API_KEY，`-m e2e`）

| 文件 | 场景 | 通过标准 |
|---|---|---|
| `smoke_single_chat.py` | 默认 researcher_writer「1+1等于几」 | 完整 SSE 闭环 + final_answer 非空 |
| `smoke_rag.py` | 上传文档→提问 | Agent 自主调 `rag_search` 且回答有依据 |
| `smoke_hitl.py` | safety_check 触发审批 | `approval_requested`→approve→继续 |
| `smoke_hierarchical.py` | team_orchestrator | manager 决策→作答 |
| `smoke_hot_reload.py` | PUT 改 goal→再对话 | 回答风格变化 |
| `smoke_web_ingest.py` | web_ingest_crew+URL | manager→reader→writer→审阅→入库 |

## 三、评审维度（REVIEW_REPORT.md，通用软件质量）

| # | 维度 | 要点 |
|---|---|---|
| 1 | 架构与分层 | 分层清晰、Config-as-Code、工厂无状态 |
| 2 | 代码质量与可维护性 | 命名/注释/重复/死代码/函数长度/类型标注 |
| 3 | 健壮性与可靠性 | 重试/熔断/超时兜底/降级/启动韧性 |
| 4 | 并发与线程安全 | call_soon_threadsafe、Playwright 线程池、共享单例 |
| 5 | 安全性 | 鉴权缺失、注入面、XSS、密钥管理 |
| 6 | 可观测性 | 日志/timing 埋点/health/错误透传 |
| 7 | 性能 | LTM 异步、lazy import、缓存、预热 |
| 8 | 数据与一致性 | FK 级联、持久化、seed 幂等、Alembic drift |
| 9 | 前端质量 | SSE 解析/错误态/会话恢复/表单校验 |
| 10 | 文档与交付 | 四份文档一致性、README/.env.example |
| 11 | 技术债 | 简历梳理 §9 六项短板严重度 |

每个维度 1–5 分 + 证据（`file:line`）+ 结论。另附 `pytest --cov` 覆盖率。

## 四、执行顺序

1. ✅ 建 `testing/` + 规划文档 + `.gitignore`（本次）
2. 写 Tier 1 `unit/*`（9 文件）
3. 写 Tier 2 `integration/*`（12 文件 + conftest）
4. 写 Tier 3 `e2e/*`（7 文件 + conftest）
5. Docker 内跑 Tier 1+2（Mock，零成本），收集通过率 + 覆盖率
6. Docker 内逐个跑 Tier 3（真实 Key），记录 SSE trace + 耗时
7. 按 11 维度代码走读，产出 `REVIEW_REPORT.md`
8. 汇总最终成果评价 + 改进清单
