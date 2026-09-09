# Nexus 评测 harness（testing/eval）

> 依据《Agent 评测体系》方法论落地的最小回路（Phase 1）：
> 声明式数据集 + 确定性评分器 + 四级判定 + 多次试验聚合 + 可 diff 报告。

## 目录

```
testing/eval/
├── datasets/                  # 声明式用例（JSON，进 git，版本化）
│   ├── rag_v3.json            #   知识库问答 15 条（迁移自 eval_rag v2）
│   ├── capability_incidents.json  # 事故回流 13 条（三次线上事故 + 发现固化）
│   └── redteam.json           # 红队攻防 8 条（注入/沙箱/SSRF/HITL/提示提取，目标=0 绕过）
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

## 评分器（8 类）

| 类型 | 锚点 | 说明 |
|---|---|---|
| sse_contract | 事件流 | 必含事件 / done 哨兵 / 禁发事件 |
| golden_phrase | 回答文本 | any-of 短语 + 空白归一化；检索到没用上记 partial；可设 weight 0 降为软断言 |
| pydantic_valid | task_completed 事件 | 结构化输出校验（后端现成字段） |
| regex_not_match | 回答文本 | 泄漏断言（tool-call JSON 等禁用模式） |
| db_assert | /v1/documents 快照 diff | 环境对账防自我报告；多环节计部分分 |
| sandbox_file | outputs/** 文件系统 | 产物存在性/大小 |
| trajectory_rule | 事件流 FSM | expect/forbidden/不重复参数/最少调用 |
| llm_rubric | LLM-as-a-Judge | 开放式/行为型判定（qwen-plus@rubric-v2，金标校准 kappa≥0.7 才可变更 prompt） |

规则：快照缺失或 spec 非法 → `judge_error`（评分器故障），绝不计为 agent 失败；
`weight: 0` 的评分器为软断言（记录进报告，不参与判败）。

## 判定与聚合

- 四级：完全 / 部分（partial 无 fail）/ 错误（任一 fail）/ 未完成（环境错误或 SSE 未收尾）
- 每用例 N 次试验（数据集 `default_trials`，用例可覆盖）：majority 定案，
  并列时保守取更差判定；结果不一致 → flaky 标记
- TSR = (完全×1 + 部分×0.5) / 总数

## 指纹（历史对比的前提）

每次运行记录：数据集哈希 + git SHA + Crew 配置快照（agents/crews/tools API 哈希）+
judge 指纹（模型@prompt 版本）。配置快照未采集时报告显式降级标注——热更新平台的
被测对象有一半在 DB 里。

## LLM-judge 与金标校准（Phase 2）

```bash
python testing/eval/judge.py --calibrate     # 金标集 kappa（门禁 0.7，不过则退出码 1）
```

- judge：qwen-plus + temperature=0 + JSON 契约 + 磁盘缓存（`results/.judge_cache.json`，
  按 prompt 版本击穿）；解析失败重试一次后记 `judge_error`
- 金标集 `judge/golden.json`：答案取自真实运行录制，人工判定标签；
  judge/人工分歧样本回流本集持续扩充
- 纪律：judge prompt 变更必须升 PROMPT_VERSION 且重过校准门禁，
  否则历史分数不可比（缓存与指纹都会体现版本）

## 成本与时延

runner 在每次试验前后读 `/metrics` 的 `token_usage_today_total` 差值，
逐试验记录 token 成本（报告表格 token 列）；用例可设
`cost_gate: {max_tokens: N}` 作为硬断言参与判败。

## 后续阶段（见方案讨论）

Phase 3：红队数据集（目标=0 进 CI）+ A 层 conformance（装配保真/隔离性/畸形配置）
Phase 4：消融 A/B 编排（需重建 backend 容器切环境开关）+ 饱和度淘汰 + /metrics 线上镜像
