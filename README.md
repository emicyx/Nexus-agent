# Project Nexus

多功能、可配置的私人 AI Agent 助手。

## 技术栈

- **Backend**: Python + FastAPI + CrewAI 1.9.3 + Aliyun LLM (通义千问，自定义流式 AliyunLLM)
- **Frontend**: Next.js 14 + Tailwind CSS + TypeScript
- **Infra**: PostgreSQL 16 (pgvector + zhparser 中文全文检索) + Redis + Docker Compose

## 文档索引

| 文档 | 内容 |
|---|---|
| [doc/architecture.md](doc/architecture.md) | 架构白皮书 v2.0：分层架构、SSE 事件字典、三层记忆、数据模型、已知限制 |
| [doc/roadmap.md](doc/roadmap.md) | **优化路线图：P0/P1/P2 分级的下一步目标** |
| [doc/tool-guide.md](doc/tool-guide.md) | 自定义工具开发指南 v2.0（24 个已注册工具一览） |
| [doc/require.txt](doc/require.txt) | 原始需求与 TODO（含 2026-08 状态刷新） |
| [doc/进度.md](doc/进度.md) | 迭代进度（头部为当前状态快照，下方为 Week 1-10 归档） |

## 核心能力（当前状态）

- 多智能体流式对话：思考 token 流 + 工具调用 + 委派追踪，SSE 实时渲染
- HITL 人类在环审批：PENDING→APPROVED/REJECTED/TIMEOUT 状态机（后端 150s 超时 > 前端 120s 卡片窗口）
- 配置热更新：Agent/Crew/Task/Tool/Skill/OutputSchema 前端可配，改完即生效
- 三层记忆：会话内滑动窗口+滚动摘要 / 跨会话用户记忆 / 知识库预注入
- Agentic RAG：语义分块 + 向量/关键词 RRF 融合检索 + 网页抓取入库
- 24 个工具：搜索、RAG、浏览器自动化（Playwright）、文件读写（md/word/excel/code）、HITL、多模态图片

## 快速开始

### 1. 环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 QWEN_API_KEY
# 本地开发想要开箱即用的演示 Agent/工具：加 SEED_DEMO_DATA=true（默认 false）
```

### 2. 启动全栈

```bash
make up    # 生产形态：镜像不可变交付、无源码挂载、数据面仅 loopback
make dev   # 开发形态：源码热挂载 + uvicorn --reload + next dev（docker-compose.dev.yml）
```

启动后访问：
- 前端: http://localhost:3000（设置了 APP_ACCESS_PASSWORD 时先在 /login 登录）
- 后端 API: http://localhost:8000（仅 loopback，正常流量走前端同源代理）
- API 健康检查: http://localhost:8000/health

### 3. 使用

打开 http://localhost:3000/chat，输入问题，即可看到 Agent 的思考过程和流式回答。

### 4. 生产部署必读（上线加固）

**流量模型**：浏览器所有 API 请求发往 Next 自身（相对路径 `/v1/*`），由
`frontend/src/app/v1/[...path]/route.ts` 服务端代理转发到后端并注入
`X-API-Key`——API 密钥只在服务端持有，浏览器 bundle 不含任何凭据；
部署到任意域名 / HTTPS 反代后无需改代码。可选设置 `APP_ACCESS_PASSWORD`
启用浏览器侧登录门（HttpOnly Cookie，7 天）。

上线前在 `.env` 中确认以下配置（详见 `.env.example` 注释）：

| 配置 | 生产建议值 | 作用 |
|---|---|---|
| `APP_API_KEY` | 长随机串 | `/v1/*` 服务端鉴权（**生产留空直接拒绝启动**） |
| `APP_ACCESS_PASSWORD` | 另一个长随机串 | 浏览器登录门（可选但建议；不设则任何访客可经代理使用） |
| `APP_ENV` | `production` | 建表/种子失败终止启动 + 缺密钥拒启 + 关闭 /docs /redoc /openapi.json |
| `SEED_DEMO_DATA` | `false`（默认已是） | 不灌测试种子数据；KB/LTM 用真实语料经文档上传接口导入 |
| `LLM_TOKEN_DAILY_BUDGET` | 按预算设（如 `2000000`） | Token 日预算熔断：新请求拒绝 + 长跑 Crew 中途复查（30s 节流） |
| `CHAT_RATE_LIMIT_PER_MIN` | `20`（默认） | 每分钟每调用方对话上限（429），防成本型 DoS |
| `SSRF_EXTRA_ALLOW_CIDRS` | 按需 | 内网网段精确白名单（替代全局放行私网） |

**应用数据库账号**：backend 默认以 `nexus_app`（非 superuser，init.sh 首次初始化
时创建）连接 Postgres，账号/口令可用 `APP_DB_USER`/`APP_DB_PASSWORD` 覆盖。
**存量数据卷**（init.sh 不会再执行）需手工补建一次：

```sql
-- docker compose exec postgres psql -U nexus -d nexus
CREATE ROLE nexus_app LOGIN PASSWORD 'nexus_app' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT USAGE, CREATE ON SCHEMA public TO nexus_app;
-- 存量表/序列的所有权仍属 nexus，需补表级授权（新表由 nexus_app 自建则无需）
GRANT ALL ON ALL TABLES IN SCHEMA public TO nexus_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO nexus_app;
```

或临时设 `APP_DB_USER=nexus` 回退到 superuser 账号（不推荐长期使用）。

**已知的单租户边界**（多用户前必须改造）：会话（ChatSession）不区分用户——
所有通过登录门/持有密钥的调用方共享全部会话与配置。单租户私部署可接受；
多人共用需要引入用户体系（会话归属字段 + 按用户隔离），见 roadmap。

已内置的上线加固（无需配置）：断连后执行真正终止（不再白烧 LLM 调用）、SSE 事件队列有界、
API Key 常量时间比较、SSRF 校验后锁定 IP 直连（封 DNS rebinding）+ 浏览器路径
page.route 逐请求守卫、沙箱产物 7 天自动清理 + 读取白名单不含 CrewAI 内部存储、
`/health` 真实探活 DB+Redis（失败 503，compose healthcheck 已接）、`/metrics` 暴露
SSE 并发/队列深度/工具 P95/Token 用量（带 `X-API-Key` 抓取）、全链路 request-id 日志
（消毒防日志注入）、优雅停机（收尾窗口 + 强制取消传播窗口 + 浏览器实例回收）、
并发 SSE 运行上限、数据库迁移链与模型一致性由 CI 保证（干净库 `alembic upgrade head`）。
部署日另需配置：数据库备份 cron、UptimeRobot 拨测 `/health`、HTTPS 反代（Caddy/Nginx
→ 前端 3000）。

## 项目结构

```
├── backend/          # FastAPI + CrewAI 后端
│   └── app/
│       ├── api/v1/   # API 路由（chat/sessions/agents/crews/tools/skills/documents/memories/approvals/output_schemas）
│       ├── core/     # 事件总线、异常
│       ├── crews/    # Crew 工厂、工具注册表、事件循环 offload patch
│       ├── llm/      # 自定义 AliyunLLM（流式+工具调用+多模态）+ Embedding
│       ├── models/   # SQLAlchemy ORM（迁移 0001-0009）
│       ├── services/ # 会话/记忆/文档/混合检索/语义分块
│       └── tools/    # 24 个 CrewAI 自定义工具
├── frontend/         # Next.js 前端
│   └── src/
│       ├── app/      # App Router 页面（chat/config）
│       ├── components/ # UI 组件（聊天/审批卡/配置表单）
│       ├── lib/      # API 客户端 + SSE 解析
│       └── hooks/    # React Hooks
├── doc/              # 文档（架构/路线图/工具指南/需求/进度）
├── infra/            # 基础设施（postgres + zhparser 镜像）
└── docker-compose.yml
```

## 常用命令

```bash
make up          # 启动全部服务
make down        # 停止全部服务
make logs        # 查看前后端日志
make psql        # 进入 PostgreSQL
make backend-shell  # 进入后端容器
```
