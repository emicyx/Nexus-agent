# Nexus 评测 harness（testing/eval）

> 依据《Agent 评测体系》方法论落地的最小回路（Phase 1）：
> 声明式数据集 + 确定性评分器 + 四级判定 + 多次试验聚合 + 可 diff 报告。

## 目录

```
testing/eval/
├── datasets/                  # 声明式用例（JSON，进 git，版本化）
│   ├── rag_v3.json            #   知识库问答 15 条（迁移自 eval_rag v2）
│   └── capability_incidents.json  # 事故回流 12 条（三次线上事故各 4 条）
├── scorers/                   # 评分器注册表（纯函数，零依赖）
├── schema.py                  # 数据集加载与校验
├── aggregate.py               # 四级判定（完全/部分/错误/未完成）+ majority/flaky
├── fingerprint.py             # 运行指纹：数据集哈希 + git SHA + Crew 配置快照
├── report.py                  # Markdown 报告 + 两次运行 diff
├── runner.py                  # CLI 编排
└── results/{run_id}/          # 运行产物（gitignore）
```

## 运行

前置：Docker 全栈启动（`docker compose up -d`），RAG 语料已入库
（`testing/eval_rag/reingest_docs.py`），SOP 语料已入库（`kb_seed/`）。

```bash
# 本机直跑（宿主机需能访问 localhost:8000 与 backend/data/outputs）
python testing/eval/runner.py --dry-run                 # 只校验数据集装配
python testing/eval/runner.py --datasets rag_v3 --trials 1
python testing/eval/runner.py                           # 全部数据集，默认试验数
python testing/eval/runner.py --diff 20260908-1530     # 与上次运行对比
```

环境变量：`NEXUS_BASE_URL`（默认 http://localhost:8000）、`NEXUS_API_KEY`（= APP_API_KEY）。

## 评分器（7 类，纯确定性）

| 类型 | 锚点 | 说明 |
|---|---|---|
| sse_contract | 事件流 | 必含事件 / done 哨兵 / 禁发事件 |
| golden_phrase | 回答文本 | 语料逐字子串命中；检索到没用上记 partial |
| pydantic_valid | task_completed 事件 | 结构化输出校验（后端现成字段） |
| regex_not_match | 回答文本 | 泄漏断言（tool-call JSON 等禁用模式） |
| db_assert | /v1/documents 快照 diff | 环境对账防自我报告；多环节计部分分 |
| sandbox_file | outputs/** 文件系统 | 产物存在性/大小 |
| trajectory_rule | 事件流 FSM | expect/forbidden/不重复参数/最少调用 |

规则：快照缺失或 spec 非法 → `judge_error`（评分器故障），绝不计为 agent 失败。

## 判定与聚合

- 四级：完全 / 部分（partial 无 fail）/ 错误（任一 fail）/ 未完成（环境错误或 SSE 未收尾）
- 每用例 N 次试验（数据集 `default_trials`，用例可覆盖）：majority 定案，
  并列时保守取更差判定；结果不一致 → flaky 标记
- TSR = (完全×1 + 部分×0.5) / 总数

## 指纹（历史对比的前提）

每次运行记录：数据集哈希 + git SHA + Crew 配置快照（agents/crews/tools API 哈希）。
配置快照未采集时报告显式降级标注——热更新平台的被测对象有一半在 DB 里。

## 后续阶段（见方案讨论）

Phase 2：LLM-judge 服务化（rubric + 金标校准 + 缓存）+ 消融 A/B + 成本门禁（token_budget）
Phase 3：红队数据集（目标=0 进 CI）+ A 层 conformance（装配保真/隔离性/畸形配置）
Phase 4：饱和度淘汰 + 配置变更影子评测 + /metrics 线上镜像
