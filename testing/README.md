# Project Nexus 测试与评审（testing/）

> 本目录存放 Nexus 项目的测试与评审材料，已加入 `.gitignore`（仅作一次性成果评价，不纳入版本管理）。
> 详细规划见 [TEST_PLAN.md](./TEST_PLAN.md)，评审产出见 [REVIEW_REPORT.md](./REVIEW_REPORT.md)。

## 目录结构

```
testing/
├── README.md            # 本文件（运行速查）
├── TEST_PLAN.md         # 测试矩阵 + 执行指南（权威规划）
├── REVIEW_REPORT.md     # 评审报告（11 维度评分模板）
├── unit/                # Tier 1 纯逻辑单测（无需 DB/网络，零 LLM 成本）
├── integration/         # Tier 2 API 集成测试（Docker PG+Redis，Mock LLM）
├── e2e/                 # Tier 3 真实 E2E smoke（真实 QWEN_API_KEY）
├── eval/                # Tier 4 能力评测 harness（声明式数据集+评分器+报告）
└── eval_rag/            # 旧版 RAG 专项评估（被 eval/ 逐步取代）
```

## 四层测试概览

| 层级 | 测什么 | 依赖 | 成本 | 目标 |
|---|---|---|---|---|
| Tier 1 `unit/` | 纯函数：SSE 格式、记忆压缩、工具注册、DSN、切块、多模态归一化、HITL 状态机 | 无（仅 Python 标准库 + backend 模块） | 零 | 逻辑正确性 |
| Tier 2 `integration/` | 全部 REST 端点 CRUD + 校验 + SSE 事件序列 | Docker PG+Redis；LLM 被 Mock | 零 | 系统可用性 |
| Tier 3 `e2e/` | 6 条真实主流程闭环（对话/RAG/HITL/hierarchical/热更新/入库） | Docker 全栈 + 真实 `QWEN_API_KEY` | 花钱/慢 | 简历亮点可复现 |
| Tier 4 `eval/` | 能力评测：RAG 问答 15 条 + 事故回流 12 条，四级判定 + TSR + 可 diff 报告 | 同 Tier 3（离线跑，非 CI） | 花钱 | 度量"变好还是变坏" |

Tier 1-3 在 CI（`.github/workflows/ci.yml`）自动执行；Tier 4 用法见
[eval/README.md](./eval/README.md)：
```bash
python testing/eval/runner.py --dry-run      # 校验数据集装配
python testing/eval/runner.py                # 全量评测（指纹+报告+diff）
```

## 快速开始

### 0. 前置
- Docker Desktop 已启动；`.env` 已配置 `QWEN_API_KEY`
- 后端依赖齐全：在 backend 容器内装测试库（**不改 requirements.txt，避免 rebuild**）

```bash
docker compose up -d
docker compose exec backend pip install pytest pytest-asyncio httpx pytest-cov
```

### 1. 运行 Tier 1 + Tier 2（Mock LLM，零成本）

```bash
docker compose exec backend sh -c "PYTHONPATH=/app pytest /app/testing/unit /app/testing/integration -v --cov=app --cov-report=term-missing"
```

### 2. 运行 Tier 3（真实 LLM，逐个执行）

```bash
# 例：真实单 Agent 对话 smoke
docker compose exec backend sh -c "PYTHONPATH=/app pytest /app/testing/e2e -m e2e -v --run-e2e -k single_chat"
# 例：HITL smoke（会触发审批，需人工在 60s 内 approve）
docker compose exec backend sh -c "PYTHONPATH=/app pytest /app/testing/e2e -m e2e -v --run-e2e -k hitl"
```

### 3. 查看覆盖率

Tier 1+2 运行后终端会输出 `pytest-cov` 覆盖率表；把结果粘贴到 `REVIEW_REPORT.md` 的「测试覆盖」小节。

## 约定
- **数据库隔离**：集成测试只创建/删除自己的测试实体，对种子数据只读断言，不破坏 5 套预置 Crew
- **LLM Mock**：集成测试 monkeypatch `AliyunLLM.call` 返回固定字符串，CrewAI 走 `llm.call()`，全程零网络请求
- **Tier 3 标记**：`@pytest.mark.e2e` + 自定义 `--run-e2e` 参数（conftest 里注册），平时不执行
- **Windows 本地**：本项目统一在 Docker 内跑测试（容器 UTF-8，规避 GBK 控制台问题）
