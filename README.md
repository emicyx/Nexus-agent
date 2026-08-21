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
- HITL 人类在环审批：PENDING→APPROVED/REJECTED/TIMEOUT 状态机（60s 超时）
- 配置热更新：Agent/Crew/Task/Tool/Skill/OutputSchema 前端可配，改完即生效
- 三层记忆：会话内滑动窗口+滚动摘要 / 跨会话用户记忆 / 知识库预注入
- Agentic RAG：语义分块 + 向量/关键词 RRF 融合检索 + 网页抓取入库
- 24 个工具：搜索、RAG、浏览器自动化（Playwright）、文件读写（md/word/excel/code）、HITL、多模态图片

## 快速开始

### 1. 环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 QWEN_API_KEY
```

### 2. 启动全栈

```bash
make up
```

启动后访问：
- 前端: http://localhost:3000
- 后端 API: http://localhost:8000
- API 健康检查: http://localhost:8000/health

### 3. 使用

打开 http://localhost:3000/chat，输入问题，即可看到 Agent 的思考过程和流式回答。

## 项目结构

```
├── backend/          # FastAPI + CrewAI 后端
│   └── app/
│       ├── api/v1/   # API 路由（chat/sessions/agents/crews/tools/skills/documents/memories/approvals/output_schemas）
│       ├── core/     # 事件总线、异常
│       ├── crews/    # Crew 工厂、工具注册表、事件循环 offload patch
│       ├── llm/      # 自定义 AliyunLLM（流式+工具调用+多模态）+ Embedding
│       ├── models/   # SQLAlchemy ORM（迁移 0001-0007）
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
