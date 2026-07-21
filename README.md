# Project Nexus

多功能、可配置的私人 AI Agent 助手。

## 技术栈

- **Backend**: Python + FastAPI + CrewAI + Aliyun LLM (通义千问)
- **Frontend**: Next.js 14 + Tailwind CSS + TypeScript
- **Infra**: PostgreSQL (pgvector) + Redis + Docker Compose

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
│       ├── api/v1/   # API 路由
│       ├── core/     # 事件总线、异常
│       ├── crews/    # Crew 工厂（动态实例化）
│       ├── llm/      # 自定义 LLM (Aliyun)
│       ├── tools/    # CrewAI 自定义工具
│       └── db/       # 数据库连接（Week 3+5）
├── frontend/         # Next.js 前端
│   └── src/
│       ├── app/      # App Router 页面
│       ├── components/ # UI 组件
│       ├── lib/      # API 客户端 + SSE 解析
│       └── hooks/    # React Hooks
├── infra/            # 基础设施脚本
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
