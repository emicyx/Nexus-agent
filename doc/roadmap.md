# Nexus 优化路线图

> 2026-08-21 基于 HEAD=5ac4c4f 全量代码评审整理。
> 优先级定义：P0 = 安全/正确性硬伤，必须尽快；P1 = 用户可感知的缺陷与工程保障；P2 = 架构健康度与体验增强。
> 每项标注现状、目标、涉及文件与验收标准，做完一项勾一项并更新本文档。

---

## P0 — 安全与正确性（✅ 2026-08-21 全部完成）

> 实施记录：文件沙箱（`_file_utils.py` 重写 + 7 工具适配 + 截图目录入沙箱）、X-API-Key 鉴权（空值放行+启动告警）、`net_guard` SSRF 防护（重定向逐跳校验）、compose 口令 env 化（端口映射按用户决定保留）、factory 两处修复（history 兜底 + delegation map 改 ContextVar）。回归测试：`testing/unit/test_file_utils.py`、`test_net_guard.py`、`test_factory_p0.py`、`testing/integration/test_api_auth.py`。行为变更：读取范围收缩到 `SANDBOX_DATA_DIR`、截图目录移至 `{SANDBOX_DATA_DIR}/screenshots`、LLM 完整报文仅 DEBUG 可见。

### P0-1 ✅ 文件工具路径沙箱
- **现状**：`backend/app/tools/_file_utils.py` 的 `resolve_read_path` 放行绝对路径（`view_file` 可读宿主机任意文件，包括 `.env` 里的 API Key）；`resolve_output_path` 无 containment 校验，`../` 可逃出 `/app/data/outputs`（write_markdown/write_word/write_excel/write_code 同理）；`fixed_directory_read` 可 `os.walk` 任意目录。工具入参来自 LLM，等于把文件系统交给模型。
- **目标**：所有读写路径先 `Path.resolve()` 后强制 `is_relative_to(SANDBOX_ROOT)`，越界直接拒绝并返回明确错误；白名单目录（如 outputs/、uploads/）外一律 403 语义。
- **涉及**：`backend/app/tools/_file_utils.py` 及全部文件工具。
- **验收**：`view_file("/app/.env")`、`write_markdown("../../etc/x")` 均被拒绝；正常沙箱内读写不受影响。

### P0-2 ✅ 最小可用鉴权
- **现状**：全部 `/v1/*` 接口无认证，任何人可增删配置、上传文档、触发 LLM 消费、代答审批。
- **目标**：FastAPI 全局依赖校验 `X-API-Key`（`Settings.APP_API_KEY`，`.env` 配置），前端 api-client 统一注入；`/health` 豁免。私人部署够用，暂不做用户体系。
- **涉及**：`backend/app/main.py`、`config.py`、`frontend/src/lib/api-client.ts`。
- **验收**：无 Key 请求 401；带 Key 全功能正常。

### P0-3 ✅ SSRF 防护 + 敏感信息出日志
- **现状**：`fetch_url_tool` 可抓任意 URL（含内网）；`aliyun_llm.py:330,374` 以 INFO 级 `json.dumps` 完整 messages/result（含 base64 图片、用户对话）。
- **目标**：fetch_url 解析目标 host，私网/环回/链路本地地址拒绝；LLM 日志降 DEBUG 且截断（只记 role/长度），图片数据不落日志。
- **涉及**：`fetch_url_tool.py`、`aliyun_llm.py`。
- **验收**：`fetch_url("http://127.0.0.1:8000/health")` 拒绝；INFO 日志无完整对话内容。

### P0-4 ✅ 部署面收紧（按用户决定：保留端口映射，仅口令 env 化）
- **现状**：docker-compose 硬编码 `POSTGRES_PASSWORD: nexus` 并把 5432/6379 映射到宿主机；`config.py` DSN 默认含明文口令。
- **目标**：口令走 `.env`（compose 变量注入）；postgres/redis 去掉宿主机端口映射（仅容器网络内互通，调试用 profile 开启）；`.env.example` 同步。
- **验收**：`docker compose up` 后宿主机 `nc localhost 5432` 不通；全栈功能正常。

### P0-5 ✅ 修复 factory 两处正确性缺陷
- **现状**：① `factory.py:970` 附近 `history` 仅在 `sess is not None` 分支赋值，`_ensure_session` 失败路径引用即 NameError，整次聊天变 error；② `factory.py:731` `_delegation_pydantic_map` 是模块级全局，每次 `build_crew_from_db` 覆写，两个 hierarchical 会话并发互相污染。
- **目标**：① 补默认值 `history = []`；② 把 map 改为通过 `contextvars.ContextVar` 或闭包传给 patch（与流式上下文同风格）。
- **验收**：并发两个不同 hierarchical Crew 聊天，delegation 结构化输出各自正确；删库/会话创建失败时聊天降级而非报错。

---

## P1 — 用户可感知缺陷与工程保障

### P1-1 前端补齐 task_completed / delegation 渲染（require.txt todo 5 收尾）
- **现状**：后端已推送 `task_completed`（含 task_name/output_format/pydantic_valid/raw_preview）和 `delegation`（含 coworker/task/context）事件，`api-client.ts` 已定义类型，但 `use-chat.ts` 无对应 case，**事件被静默丢弃**——"更详细的返回"做了后端到不了界面。
- **目标**：use-chat 新增两个 case；step-panel 渲染委派卡片（manager → 某子 agent，任务摘要）与任务完成卡片（产出预览 + pydantic 校验徽标）。
- **涉及**：`frontend/src/hooks/use-chat.ts`、`components/chat/step-panel.tsx`。
- **验收**：hierarchical Crew 聊天中可见"A 委派给 B 做 X"及每个任务的产出预览。

### P1-2 提交测试 + 最小 CI
- **现状**：`testing/` 目录（含 e2e smoke 证据）未纳入 git，无 pytest 配置、无 CI。monkey-patch、路径工具、SSE 协议全靠手测。P0 已新增 4 个回归测试文件（file_utils/net_guard/api_auth/factory_p0），**但 testing/ 未提交意味着这些测试随工作区丢失即失效**——这是当前最高性价比的一步。
- **本地跑法**（已验证）：单测 `.venv/Scripts/python.exe -m pytest testing/unit -q`；集成需先 `docker compose up -d postgres redis` 并带 `POSTGRES_DSN=postgresql://nexus:nexus@localhost:5432/nexus REDIS_URL=redis://localhost:6379/0`；前端 `npx tsc --noEmit`。
- **已知过期测试**：`testing/integration/test_api_documents.py::test_create_document_and_search` 断言 `chunk_count >= 3`，与语义分块（e9ef389）后的合并行为不符，为存量失败，需更新断言。
- **目标**：① `git add testing/`（注意 `.gitignore` 检查是否排除了它）并排除 e2e 证据产物；② GitHub Actions：backend ruff+pytest（带 PG service 容器）、frontend tsc+build。
- **验收**：CI 绿；改坏 tsquery 构造或沙箱校验会被测试抓住。

### P1-3 检索/上传健壮性
- **现状**：`documents.py:57` 上传无大小限制（整文件进内存）；embedding 客户端三份且 batch 上限矛盾（`embedding.py` 10 vs `kb_ingest_tool._embed_texts_sync` 25，DashScope 上限 25——统一为共享实现）；`approvals.py:21` 用生产禁用的 Redis `KEYS approval:*`。
- **目标**：上传限流（如 10MB）+ 流式读；embedding 收敛到 `llm/embedding.py` 单实现（同步版包一层）；`KEYS` 改 `SCAN`。
- **涉及**：`api/v1/documents.py`、`llm/embedding.py`、`tools/kb_ingest_tool.py`、`services/memory_ltm.py`、`api/v1/approvals.py`。

### P1-3b P0 加固后的安全残余（低优先）
- **Redis 无密码且映射宿主机**：加 `--requirepass ${REDIS_PASSWORD:-}`，`REDIS_URL` 同步带密码（compose 改动小，但需确认工具内同步 Redis 客户端的连接串来源）。
- **Playwright 渲染路的浏览器内重定向**：入口 URL 已过 net_guard，但 Chromium 内部跟随 30x 跳私网未拦截；彻底解法是 `page.route("**")` 请求拦截逐个校验，低优先（需先有真实内网页面抓取需求才值得做）。
- **FastAPI `/docs` Swagger 无鉴权暴露**：接口结构对局域网可见，私人部署可接受；如需收紧给 docs_url 也挂依赖或关闭。

### P1-4 前端静默错误与 O(N) 绕路
- **现状**：`chat/page.tsx` 多处 `.catch(() => {})` 吞错，会话列表加载失败无感知；`use-chat.ts:439` 取单个会话靠拉全量列表线性 find（后端已有 `get_session_by_uuid` service 但无路由）。
- **目标**：新增 `GET /v1/chat/sessions/{uuid}`；catch 里至少 toast/console.error；清理 `next.config.js` 死代码 rewrites（与 api-client 直连并存）。
- **涉及**：`api/v1/chat_sessions.py`、`hooks/use-chat.ts`、`app/chat/page.tsx`、`next.config.js`。

### P1-5 crewai 版本守卫 fail-fast
- **现状**：`crewai_async_patch` / delegation patch 在版本漂移或 sentinel 不匹配时**静默跳过仅告警**，升级 crewai 后 HITL 忙等、Playwright 会重新冻结事件循环，且无人察觉。
- **目标**：启动时 patch 未应用且 `settings` 要求启用 → 抛异常拒绝启动（或显式 `CREWAI_PATCH_REQUIRED=false` 才允许降级）。
- **涉及**：`crews/crewai_async_patch.py`、`main.py` 启动检查。

---

## P2 — 架构健康度与体验增强

### P2-1 AliyunLLM 同步/异步镜像合并（~700 行重复）
- **现状**：`_do_call/_ado_call`、`_call_streaming/_acall_streaming`、`_handle_function_calls/_ahandle_function_calls`、`_fallback_call/_afallback_call` 全镜像，改一处忘一处。
- **目标**：核心逻辑抽成纯函数（输入 messages/工具 → 产出动作），sync/async 只剩薄壳（requests vs httpx.AsyncClient）。

### P2-2 factory.py 拆分（1137 行）
- **现状**：DB 装配、SSE 回调、两个 monkey-patch、缓存、三层记忆读路径混在一个文件。
- **目标**：拆为 `crew_builder.py`（装配）/`memory_pipeline.py`（三层记忆读写）/`patches/`（monkey-patch），factory 只留编排入口。

### P2-3 SSE 事件单一事实来源
- **现状**：事件类型是裸字符串，前端手工镜像联合类型，已发生漂移（P1-1 的丢弃即后果）。
- **目标**：后端用 `Literal`/Enum 定义全集并导出 JSON；前端脚本生成或至少加编译期断言测试对齐。

### P2-4 生产部署路径
- **现状**：backend Dockerfile CMD 带 `--reload`，frontend 跑 `npm run dev`；镜像即开发环境。
- **目标**：多阶段构建：frontend `next build` + standalone；backend 去掉 `--reload`，dev/prod profile 分离。

### P2-5 产品待办（源自 require.txt）
- **执行速度**（todo 1，部分完成）：已做 tool 懒加载、async patch、评估模型降级 qwen-turbo；剩余排查点：sequential 多任务串行 LLM 调用是否可并行、STM/KB 预注入的 embedding 串行等待。
- **loop-agent**（todo 6）：新 建 loop 型 crew（manager 反复审阅直至达标），复用 delegation 追踪可视化循环轮次。
- **熔断补全**：LLM 幻觉降级（输出 JSON Schema 校验失败重试/标注）与 Token 超限的明确报错文案，目前仅 API 超时有重试。
- **crew 生态**（todo 4）：继续沉淀种子 crew（数据分析、代码生成等），配套 output_schema。

### P2-6 仓库卫生
- 删除根目录与 `doc/agent-development-analysis.md` 重复的分析稿及无关个人文档；`.agents/`、`.claude/` 个人 skills 是否入库二选一（建议本地保留、git 忽略）。

---

## 建议执行顺序

```
P0-1 → P0-2 → P0-5 → P0-3 → P0-4     ✅ 已全部完成（2026-08-21）
P1-1 → P1-2 → P1-3 → P1-4 → P1-5     （下一步：两周内，可感知缺陷 + 工程保障）
P2 按需穿插，P2-1/P2-2 在下次大改动前完成
```

每完成一项：勾选状态、在 `进度.md` 记一笔、同步更新本文档与 architecture.md 对应章节。
