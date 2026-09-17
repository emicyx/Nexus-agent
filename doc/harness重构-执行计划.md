# Nexus v2 执行计划（交接版，2026-09-14）

> **本文件是唯一执行依据**。设计原理、市场调研、面试素材见《[harness化重构-调研与方案.md](./harness化重构-调研与方案.md)》（下称"调研文档"），本文只引用其结论，不重复论证。
>
> **核心目标：让系统变得好用。** 唯一验收标准是 **7 天自用保留**——每条细条上线后，用户连续 7 天真实使用才算交付；连续 7 天没用 = 砍掉或重做。技术指标（测试通过、部署成功）只是入场券。
>
> **修订记录**：
> - v1（2026-09-14 上午）：初版。
> - v1.1（2026-09-14 晚）：①部署形态改**本机优先**（S1–S4 全部在本机 compose 栈开发与验收，云端迁移作为收官步骤，见第 10 节）；②细条重组——原 S3（QQ 双向问答）提前为 S1（与原 S1 的渠道底座合并），原 S1 核心（巡检）与原 S2（日报）合并为新 S2；③QQ 侧 HITL 处置定案 A；④R0 完成态与小修清单（见 §3b 状态注记）。渠道技术事实不变。

---

## 0. 执行会话工作方式（必读）

1. **默认只做当前细条（Slice）**，做完停在验收门，等用户验证后再开下一条。不提前建后续 Slice 的任何组件。
2. 每条 Slice 的完成定义（DoD）在第 9 节，全部满足才算完。
3. 与调研文档冲突时，以本文为准；本文未覆盖的实现细节，遵循现有代码库的模式（第 2 节速查）。
4. 遵守第 3 节全局纪律与安全不变量，**任何一条不让步**。
5. 背景一句话：v1 是一个 CrewAI 多智能体聊天平台（前端发起对话、产物困在沙箱），自用结论是"只有知识库问答被真正使用"。v2 把它变成常驻的个人助理：定时/事件触发任务、连接 IM 渠道收发消息、产物推送到用户手机。

---

## 1. 路线总览（细条顺序，禁止横向铺层）

| Slice | 内容 | 依赖 | 上线形态（本机） |
|---|---|---|---|
| **R0** ✅ | 重构清理：crew 目录退役 + 评测重锚 + API/前端 Auto 模式适配（§3b） | 无 | **已完成（2026-09-14）**：Web 端助手模式对话上线，三套评测 36/36 全绿。收尾待办见 §3b 状态注记 |
| **S1** | **QQ 双向问答（渠道底座）**：NapCat 本机容器 + OneBot 双向 adapter + owner 白名单 + 路由 v0 + 会话映射 | R0 | QQ 私聊里直接跟助手对话：直接提问走默认链路、`/kb` 走知识库；NapCat 与告警通道底座就位。**验收通过（2026-09-15）**：问答//kb//write/断连演练全绿，7 天自用验证进行中（账本）；遗留观察：重启免扫码（ACCOUNT 已配）。7 天验证结果记入运行账本后本条才关闭 |
| **S2** | **定时任务与推送**：调度器 + jobs + 服务器巡检告警 + KB 每日日报 | S1 | 早 8 点巡检日报 + 服务异常即时告警 + KB 增量日报（21:00），全部推到 QQ。**技术验收通过（2026-09-16）**：单测 362/集成 61 全绿、eval 零外发红线实测（agent 真实调 push_message 被抑制 + 评测用例「完全」）、调度准点触发、state_change/断连降级/真实 QQ 推送全验证。**7 天自用验证进行中（2026-09-16 起）**：8:00/21:00 日报连续到达 + ≥1 次 down→up 告警演练 + 断连演练（一晚 stop napcat 看 job_runs 落账），结果记运行账本后本条才关闭 |
| **S3** | **钉钉告警备用通道——已搁置（2026-09-17 用户裁定"做了也没意义"，代码休眠：webhook 未配置=零行为差异；解锁条件=QQ 再发真实失联事件或用户重启意愿，启用 ≈2 分钟。规格存档见 §6）** | S2 验收通过 | egress 备推：QQ 推送失败时兜底推用户自建钉钉群 webhook（backup 缺省）；零新依赖、无企业应用 |
| **S4** | 飞书双向接入（用户指定的最终验收；**含阶段 A：im_pipeline 抽象重构自 S3 移入**；S3 搁置后前置改为：S2 7 天验证关闭 + 用户确认飞书有真实自用场景——钉钉教训：没有真实使用场景的渠道不做） | S2 验证关闭 + 用户 go | 阶段 A 渠道无关管线抽取回归全绿 → 阶段 B 飞书零核心改动接入，多渠道网关收口 |
| S5+ | 按需解锁，见第 8 节解锁登记表 | — | 有触发证据才建 |

**渠道顺序与风险声明**：
- QQ 优先是用户决策：QQ 为日常主用 IM。**开发期 NapCat 部署在本机**（compose 第 5 容器，与 backend 同 compose 内网；家宽住宅 IP 对 QQ 协议的风控画像比 IDC 更自然）；**云端迁移是收官步骤**（第 10 节）。
- **单实例铁律**：同一 QQ 账号只能跑一个 NapCat。开发期本机独占；云端上线时**停用本机 NapCat 再启用云端**（双实例 = 互踢 + 高封号风险）。这是迁移不是叠加。移动端 QQ 不受影响（NapCat 占用的是 PC 协议位，手机照常用）。
- 降险措施必须执行：napcat 容器端口一律不对公网暴露（仅 compose 内网）；本机形态下 WebUI 走 `http://localhost:6099`（仅 loopback 绑定）；开启 access token；告警消息保持稀疏（state_change 策略天然节制）。
- 残余风险：协议端账号风控（云端迁移时大陆 VPS 优先，海外 IP 冻结/验证概率更高）；S3 钉钉双推作为**账号维度**的兜底通道。
- 云端迁移后，"本机休眠即通道中断"的问题不复存在（告警 24/7 可达）；开发期接受此限制（见 §9 DoD 注记）。

**明确推迟的组件**（不做，除非第 8 节触发条件成立）：LLM 路由器、plan 编译器、订阅自动摄入、HITL IM 卡片审批、config 页 Channels 管理面板、助手级 LTM 命名空间重构。

---

## 1b. 存量系统处置总表（保留 / 改造 / 退役 / 新增）

原则：**三根柱子不动**——执行引擎（factory/tool 体系）、安全体系（core 全家）、评测体系（eval 全家）是项目资产；重构对象是产品壳：crew 目录、API 契约、前端交互。

### 1b.1 Crew 目录（6 → 4 保留 + 2 新增）

| crew | 处置 | 理由 |
|---|---|---|
| researcher_writer | **保留**（默认 crew，R0 已补文件请求纪律） | 助手 shell 的默认入口 |
| knowledge_qa | **保留** | 自用验证过唯一持续在用的能力；`/kb` |
| iterative_write_crew | **保留** | `/write`；评审循环是 multi-agent 真正付得起成本的形态 |
| web_ingest_crew | **保留** | URL→KB 流水线；订阅摄入（解锁表）复用；远期拆 plan |
| team_orchestrator | **已退役删除**（R0 完成，seed 幂等清理存量） | 职责被助手 shell 取代；crew 内 LLM manager 即兴编排慢、贵、不可测（调研 §8.3）。评测已重锚（§3b） |
| safety_check | **已退役删除**（R0 完成） | 纯 demo crew；HITL 机制由 hook + 单测/集成测试保证；e2e smoke_hitl 已改挂 iterative_write_crew |
| ops_patrol / daily_digest | **新增**（S2 seed） | 巡检与日报 |

### 1b.2 工具（24 → 26）

- **全部 24 个保留**：9 个 Playwright 自动化工具保留（lazy import 已隔离启动成本、属原始需求），但**不进助手默认链路**（仅显式配置的 crew 可挂）；
- **新增**：`http_check`、`push_message`（S2）；
- 核心工具（rag_search / kb_ingest / fetch_url / human_approval / write_* / view_file / baidu_search）不动。

### 1b.3 后端模块

| 处置 | 内容 |
|---|---|
| **不动（资产）** | factory 装配/记忆/补丁、tool_registry + hooks + tool_events、core 全部（sandbox_cleanup/net_guard/token_budget/rate_limit/security/metrics/llm_errors）、services 全部、eval 体系全部、crews/route_v0（R0 新增） |
| **改造** | seed.py（S2 增 ops_patrol/daily_digest）、api/v1/chat.py（R0 已完成 mode 参数与 routed_crew 事件）、main.py（lifespan 挂调度器，S2） |
| **新增** | channels/onebot_adapter（S1）、api/v1/channels（S1）、services/job_scheduler（S2）、services/egress（S2）、api/v1/jobs（S2）、models(jobs/job_runs)（S2）、run_crew_job（factory 内新增函数，S2）、tools/http_check_tool + push_message_tool（S2） |
| **不拆** | factory.py 1215 行的模块拆分**不做**（见 1c） |

### 1b.4 API 契约（v2 变更清单）

**已完成（R0）**：`POST /v1/chat/stream` 增补可选 `mode: "auto"|"manual"`（默认 manual，与 crew_id/single 互斥）；SSE 新增 `routed_crew` 事件（Auto 模式流首事件，告知前端实际服务的 crew）。

**新增（S1 起）**：`GET /v1/channels`（渠道在线状态）。

**新增（S2 起）**：`POST/GET/PATCH /v1/jobs`、`GET /v1/jobs/{id}/runs`、`POST /v1/jobs/{id}/run`（body 可带 `"eval":true`）。

**变更（S1 起）**：sessions 元数据增加来源标记（web/qq）；现有 sessions/agents/crews/... CRUD 全部兼容保留。

**移除**：无硬移除（退役 crew 只是种子数据不再存在，相关 CRUD 天然 404）。

### 1b.5 前端（Next.js）

| 页面 | 改造 | 时点 |
|---|---|---|
| `/chat` | **已完成（R0）**：默认 Auto 模式、`/` 命令列表、开发者模式开关、routed_crew 徽标 | ✅ |
| `/chat` | 顶栏渠道在线状态 chip | S1 |
| `/chat` | 会话列表显示来源徽标（web/qq） | S1 |
| `/config` | 新增 **Jobs 页签**（任务列表/启停/手动触发(eval)/最近运行状态） | S2 |
| 复用不动 | SSE 解析器、审批卡、step panel、登录、server proxy、agent-style | — |

### 1b.6 数据与评测

- **R0 已完成重锚**（2026-09-14 人工确认）：inc-a5 → researcher_writer（A1）、inc-b2 → iterative_write_crew、q04 语料与参考文更新为 4 套团队；三数据集版本 v2026.09.14；**终版全绿基线 run 20260914-203506（36/36，TSR 100%）**，归档于 testing/eval/评测报告-20260909.md §十。
- **成本分层 v2026.09.15**（全量 85 trial/164 万 token 偏贵的瘦身）：schema 增 tier 字段（core/full，缺省 core）+ runner `--tier`（缺省 core，进 fingerprint notes 防跨层对比）。core=日常缺省（29 用例/49 trial，缺陷锚一条不砍；同攻击面第二变体 rt-2/rt-6 与常规检索题 q05/q06/q09/q11/q14 降 full）；capability trials 3→2（写路径大户 inc-a3/b3/b4 单 trial）；redteam trials 2→1（安全断言硬门禁单 trial 判定）。里程碑验收跑 `--tier full`（36 用例/61 trial）。
- KB / 会话 / 记忆 / 配置数据全保留；所有迁移只增不减。
- markdown_write crew（用户自建，非 seed）已做收敛纪律修复（原值备份 `tmp-crew87-backup.json`）；**云端库需同步三处修改**（清单在评测报告 §十"补充"小节，已并入第 10 节收官步骤）。

### 1b.7 仓库杂物清理

- ✅ 已完成（R0）：删除 `nexus-fix.bundle`、`nexus-rag-fix.bundle`、`tmp/`。
- 保留：`testing/eval_rag`（面试指标的原始证据）、`kb_seed/team_json`（数据缓存）。

## 1c. 明确不动清单（防重构失控）

| 项 | 不动的理由 |
|---|---|
| factory.py 拆分（1215 行） | 纯内部重构、无用户可见价值、回归风险大；roadmap P2 顺延（解锁表登记） |
| LTM 助手命名空间 / ChatSession.crew_id 可空化 | 解锁表：跨 crew 连续性痛点真实出现再做（调研 §9.2） |
| LLM 路由器 / plan 编译器 | 解锁表：触发证据（2026-09-14 已确认按计划路径：先收集 /cmd 使用证据） |
| 删除 Playwright 工具族 | lazy import 已隔离成本，属原始需求功能 |
| CrewAI 框架升级（1.9.3 锁定 + 补丁） | 独立大工程，与 v2 目标无关 |
| 单租户 → 多租户 | 无第二用户，伪需求 |

---

## 2. 代码库事实速查（新会话必读）

### 2.1 目录与关键文件

| 路径 | 事实 |
|---|---|
| `backend/app/main.py` | FastAPI 入口；lifespan 启动时建表、seed 同步、LLM 预热、注册后台周期任务 |
| `backend/app/crews/factory.py` | ~1240 行。`build_crew_from_db(crew_id)` 每请求从 DB 装配 Crew 用后即毁（热更新天然成立）；`run_crew_chat(crew_id, message, queue, loop, session_id)` 是带三层记忆的对话执行入口（SSE 队列版）；`run_single_agent_chat` 单 agent 兜底；`get_default_crew_id()` / `get_crew_id_by_name(name)` 均带 5 分钟 TTL 缓存 |
| `backend/app/crews/route_v0.py` | **R0 新增**：路由 v0 纯函数。`COMMAND_CREW_MAP`（/kb→knowledge_qa、/write→iterative_write_crew、/ingest→web_ingest_crew）+ 默认 crew（researcher_writer）；未知命令/空内容返回可读错误。命令表是唯一权威，前端 `frontend/src/lib/commands.ts` 镜像同步 |
| `backend/app/crews/tool_registry.py` | 静态 dict：tool_key → 工具类，lazy import。**新增工具类必须在此注册** |
| `backend/app/crews/tool_events.py` | 所有工具的包装器：SSE 事件 + 同失败形态连续 3 次触发"[系统护栏]"停止重试 |
| `backend/app/db/seed.py` | ~1080 行幂等 seed 同步（crew/agent/task/tool 配置的权威来源），**支持退役删除**（`RETIRED_CREWS`/`RETIRED_AGENTS`，R0 起）。**注意坑**：`SEED_DEMO_DATA=false` 时启动跳过 seed 同步，需在容器里手动跑一次 `ensure_seed()`（历史事故教训） |
| `backend/app/core/sandbox_cleanup.py` | 周期后台任务先例（保留天数清理沙箱）——**调度器接入 lifespan 的参考模式** |
| `backend/app/core/net_guard.py` | SSRF 防护：禁私网/环回/保留地址，有 CIDR allowlist 机制，DNS pinning |
| `backend/app/core/token_budget.py` | 每日 token 预算熔断 |
| `backend/app/api/v1/chat.py` | `POST /v1/chat/stream`（SSE）；R0 起支持 `mode=auto`（路由 v0 → crew 解析 → routed_crew 首事件）；`_ensure_session(session_id, crew_id, message)` 建 ChatSession（**crew_id 硬绑定**，不动） |
| `backend/app/models/chat.py` | `ChatSession.crew_id` 非空外键（**S4 前不要动它**，解锁表管着） |
| `backend/app/llm/embedding.py` | DashScope text-embedding-v3，1024 维 |
| `backend/app/services/hybrid_search.py` | 向量 + zhparser 全文 RRF 混合检索 |
| `backend/alembic/versions/` | 迁移 0001–0009，下一个用 **0010**（S2） |
| `backend/requirements.txt` | 依赖必须进这里并重建镜像（**教训 #6：不许只 docker cp 热部署**） |
| `docker-compose.yml` | 容器（postgres pgvector+zhparser / redis / backend / frontend，**S1 加 napcat 第 5 容器**），不可变镜像；backend 与 napcat 同 compose 内网互访，OneBot 反向 WS 走内网不经公网 |
| `testing/eval/runner.py` | 评测入口：EnvAdapter 走 HTTP 打真实栈、SSE、auto-approve、clean_sandbox/ingest_documents/cleanup_new_documents 封闭环境控制；**--tier 缺省 core**（2026-09-15 成本分层：日常全量只跑防回归锚+安全不变量，29 用例/49 trial ≈ 80 万 token，约为 full 的一半；里程碑验收显式 `--tier full`；修复验证的"完全×3"用 `--trials 3` 定点）；`--base-url` 缺省 localhost:8000（**评测全量复跑留在本机跑**，服务器不为评测 burst 买单） |
| `testing/eval/datasets/` | 数据集版本化（当前 **v2026.09.14**），scorer 8 种 + llm_rubric；judge_error 纪律：任一硬评分器故障即试验不可判（R0 #10 修复） |
| `doc/上线部署手册.md` | 部署步骤（新能力上线后在此追加增量） |
| `doc/运行账本.md` | 尚未创建；S1 起新建，记日期/用途/成本/备注与 7 天自用验证结果 |

### 2.2 现有可复用机制

- **工具执行链**：注册进 tool_registry 的工具自动获得 tool_events 包装（事件 + 重试护栏）。新工具照抄现有工具的类结构（看 `backend/app/tools/` 任一实现，BaseTool 子类）。
- **token 成本**：`GET /metrics` 的 `token_usage_today_total` 前后差值是现成的成本采集法（评测 runner 同款）。
- **seed 新 crew**：在 seed.py 加配置块（幂等），重启或手动 ensure_seed 生效。
- **后台任务**：lifespan 里起，参考 sandbox_cleanup 的写法。
- **路由与透明度**：route_v0 纯函数（单测覆盖）+ `routed_crew` SSE 事件 + 前端徽标（R0 已建）；S1 的 QQ 入站直接复用同一函数，QQ 回复前缀标注实际 crew。

### 2.3 NapCat / OneBot 11（S1 的 QQ 通道，核心事实）

- **拓扑（开发期本机）**：`mlikiowa/napcat-docker:latest` 作为 docker-compose 第 5 容器，与 backend 同 compose 网络；用 OneBot 11 **反向 WebSocket**：NapCat 连 `ws://backend:8000/v1/channels/onebot/ws?token=...`（内网明文即可）。adapter 设计与部署位置无关（本机/云端同一代码路径，只是 URL 不同）。
- **NapCat 侧配置**：QQ 数据目录挂 volume（登录态持久化，容器重启不重登）；首次登录走 WebUI（默认 6099 端口）——**本机形态直接浏览器开 `http://localhost:6099`**（compose 端口仅绑 loopback）；手机 QQ 扫码一次；反向 WS 填上述 URL 与 token；开启 access token；容器 `restart: unless-stopped`，断线自动重连。
- **OneBot 11 协议要点**：连接上是双向 JSON——NapCat 向上推事件（`post_type=="message"`，含 `message_type`（group/private）、`raw_message`、`user_id`、`group_id`、`sender`）；Nexus 向下发 API 调用（`{"action":"send_private_msg","params":{...},"echo":"..."}`），同连接收响应（`status/retcode/data`）。私聊 `send_private_msg`、群 `send_group_msg`；消息是纯文本段（**无 markdown**）。
- **心跳与重连**：NapCat 反连自带重连；Nexus 侧 WS ping/pong + 断连可见——`/metrics` 加 `onebot_connected` gauge；**推送前查连接状态，断连时如实记 `failed(channel_offline)` 并重试一次**（S2 job 侧），不静默丢。
- **频率纪律**：QQ 侧无限频承诺，消息保持稀疏；超长分段（单条建议 <1500 字）。

### 2.4 钉钉速览（S3）

群自定义机器人 webhook（推送：一行 POST、加签 HMAC-SHA256、markdown 子集、**每分钟 20 条限流**，响应 `errcode` 判成败）+ Stream 模式（`dingtalk-stream` SDK，WebSocket 反向推送，免公网，凭据 Client-ID/Secret）。详见调研文档 §1.3。

### 2.5 飞书速览（S4）

企业自建应用 + `lark-oapi` SDK WebSocket 长连接订阅 `im.message.receive_v1`（免公网回调，个人免费建"企业"）。详见调研文档 §1.3。

---

## 3. 全局纪律与安全不变量

### 3.1 防玩具三纪律

1. **细条优先**：每条 Slice 端到端当周可交付，以"用户手机/本机上的真实结果"收尾，禁止先铺层。
2. **复杂度按需解锁**：每建一个推迟组件，须在第 8 节登记表补触发证据。没有证据不建。
3. **替代品检验**：每条 Slice 要能一句话答出"比 UptimeRobot / 直接开 ChatGPT / 手动做强在哪"。已知答案：S2 巡检 = 告警带初诊（agent 第一时间查的指标/日志上下文附在告警里）+ 与 KB/处置同闭环；S2 日报 = 私有 KB 增量；S1 问答 = 私有 KB + 记忆随身。

### 3.2 安全不变量（任何 Slice 都不得违反）

1. **egress 目标锁定**：QQ 推送目标只来自 env `QQ_ALERT_TARGET`（`{"type":"private","user_id":...}` 或 `{"type":"group","group_id":...}`）。`push_message` 工具**不暴露任何目标参数**——user_id / group_id / webhook url 一律不许成为模型可填参数。（S1 的对话回复不受此限：回复走**事件来源** reply-to-source，不引入新目标面。）
2. **eval 零外发**：评测/dry-run 触发的 job 严禁真实推送（S2 落地，见 5.6）。
3. **http_check 目标来自配置**（env `MONITOR_TARGETS`），模型不可自由输入 URL——注入打不开 SSRF 面；**不为巡检放松 net_guard 全局规则**。
4. **密钥只进 env/compose 配置**：`ONEBOT_WS_TOKEN`、`QQ_ALERT_TARGET`、`QQ_OWNER_IDS`、`QQ_BOT_SELF_ID`（S1）、NapCat WebUI 密码、钉钉/飞书凭据（S3/S4）；不落 DB、不进 git。
5. **渠道连接鉴权与输入不可信**：OneBot WS endpoint 必须校验 token，未授权连接立即关闭；**S1 起入站 IM 消息一律以不可信标签包裹注入**（沿用 kb_content 纪律——IM 消息里的"忽略之前的指令"类文本不得进入系统语义）。
6. **owner 白名单**：S1 起入站消息校验 env `QQ_OWNER_IDS`，名单外拒答并记日志。
7. **新依赖烧进镜像**：requirements.txt + `docker compose build` 验证通过才算装上。
8. **job 约束**（S2）：每 job 并发 = 1（不重叠）、单次 token 上限、连续 N 次失败（默认 5）自动停用 + 推送一条告警后静默。
9. **新 API 挂现有 X-API-Key 鉴权**；cron 一律显式时区（settings 增加 `JOB_TIMEZONE`，默认 `Asia/Shanghai`）。

### 3.3 部署与回滚

- 变更全部走 git + 镜像重建（compose build && up -d），沿用现有 healthcheck 验证（`/health` 200）。
- 回滚 = `jobs.enabled=false` 或 `docker compose stop napcat`（通道自然失效）；所有迁移只新增表/列，可前滚不可破坏。
- 开发期一切部署都在本机 compose；云端部署统一走第 10 节收官步骤，不逐 Slice 上云。

---

## 3b. Slice R0 规格：重构清理与契约适配 ✅ 已完成（2026-09-14）

> **状态注记（新会话从这里接手）**：
> - R0 五项全部完成：crew 目录退役（seed 幂等删除 + DB 级联实证）、评测重锚（inc-a5→A1 researcher_writer、inc-b2→iterative_write_crew、q04 语料 4 套、v2026.09.14）、route_v0 + mode=auto + routed_crew、前端 Auto 模式、杂物清理、architecture.md v2 数据流（§4.5）。
> - 重锚过程三个发现已当日闭环：#10 judge_error 静默通过（runner any() 修复 + 单测）、#11 文件等同幻觉默认链路复发（researcher 文件请求纪律 prompt，inc-a5 完全×3）、markdown_write crew 调研过度（收敛纪律，配置层）。详见 testing/eval/评测报告-20260909.md §十。
> - 验证：单测 308 全绿（含 route_v0 16 项、score_trial 5 项、SSE 契约守卫）、集成 49 全绿（含 auto 模式 5 项）、前端 tsc/build 过、三套评测 36/36 全绿（run 20260914-203506）、Web 端人工验证过。
> - **待办（新会话第一动作）**：①全部改动仍在工作区**未 commit**——核对 `git status`（约 13 改 4 增，另加今日的数据集/语料/报告/crew 修复）后 commit 合入 master_server，附完整变更说明；②R0 后小修两条（半小时，改后定点回归 inc-a5 与 /write 手测）：researcher 身份措辞（"你是谁"类问题以 Nexus 助手身份介绍能力边界：可直接提问、/kb /write /ingest 命令）、strict_reviewer 容差（字数 ±10% 以内等非实质问题不作 REVISE 依据）。

R0 原规格（存档）：范围五项——crew 目录退役（seed 支持删除存量）、评测重锚（人工确认后改锚或退役 + 版本升级 + 三套全量复跑全绿）、chat API `mode` 参数 + `routed_crew` 事件 + route_v0 独立小函数（纯确定性零 LLM，单测覆盖）、前端 /chat Auto 模式适配、杂物清理。

---

## 4. Slice 1 规格：QQ 双向问答（渠道底座）

**目标一句话**：在 QQ 里直接跟助手说话——直接提问走默认链路（researcher_writer，三层记忆全生效），`/kb` 走知识库；同时把 NapCat 容器与 OneBot 双向通道这个底座建好（S2 的所有推送都走它）。

### 4.1 NapCat 本机容器（compose 第 5 服务）

- 镜像 `mlikiowa/napcat-docker:latest`；QQ 数据目录挂 volume；`restart: unless-stopped`；纳入 healthcheck。
- 端口纪律：**不对公网暴露任何端口**；WebUI 6099 仅绑 `127.0.0.1`（本机浏览器直开扫码，无需隧道）。
- NapCat 侧配置（WebUI 里做一次）：反向 WS 地址 `ws://backend:8000/v1/channels/onebot/ws?token=${ONEBOT_WS_TOKEN}`、开启 access token。
- **单实例铁律**：确认本机原 NapCat（如有独立安装）停用，QQ 协议位只留给这个容器。

### 4.2 OneBot adapter（`backend/app/channels/onebot_adapter.py`）

- FastAPI WebSocket 路由 `/v1/channels/onebot/ws`：校验 `token` query 参数（env `ONEBOT_WS_TOKEN`），失败立即关闭；单连接模型（新连接顶旧连接，旧连接优雅关闭）。
- **入站**：`post_type=="message"` 事件进入处理管线（4.3）；其余事件（心跳/元事件）忽略但记 debug 日志。
- **出站**：`send_qq_message(content, *, user_id=None, group_id=None) -> SendResult`——对话回复走事件来源（reply-to-source）；纯文本、>1500 字分段、温和频控；同连接收 API 响应（echo 匹配）判成败。
- 连接状态 gauge `onebot_connected` 进 `/metrics`；断连/重连记 INFO 日志。
- WS ping/pong 保活；NapCat 断线自动重连（被动接受即可）。

### 4.3 入站处理管线（adapter → 复用现有引擎，不另造执行器）

1. **群/私聊判定**：私聊直收；群消息仅当 @ 本机器人时处理（env `QQ_BOT_SELF_ID` 匹配消息 at 段），并剥离 at 前缀取正文。
2. **owner 白名单**：`user_id` 校验 env `QQ_OWNER_IDS`（逗号分隔）；名单外：记 WARNING 日志 + 静默不回（不给攻击者确认 bot 存在的信号）。
3. **不可信包裹**：入站正文进入 agent 上下文前以 `<im_content>` 不可信标签包裹（照抄 rag_search 对 kb_content 的纪律与措辞），防 IM 注入。
4. **会话映射**：`session_key = qq:{user_id}`（私聊；群聊暂不做会话化，见 4.7 取舍）；首次消息 lazy 创建 ChatSession（绑默认 crew，复用 `_ensure_session` 模式；ChatSession.crew_id 硬绑定为 v1 现状，不动）。
5. **路由 v0 复用**：`route_message(text)` 同一函数——`/kb` `/write` `/ingest` 前缀 → 对应 crew（前缀剥离），无前缀 → 默认 crew。**QQ 回复的透明度**：命令路由时回复前缀 `[已转交 {crew_name}]`，默认路由不标注（保持对话自然）。
6. **执行**：`run_crew_chat(crew_id, message, queue, loop, session_id=session_key)` 原样复用（三层记忆/成本采集/事件流全生效）；事件流不转发到 QQ（只有 final_answer / error 回推）。
7. **回复**：final_answer 经 `send_qq_message` 回事件来源；error 走 `classify_llm_error` 的用户友好文案；超长分段。
8. **HITL 定案 A**：QQ 侧仅开放默认问答 + `/kb`（均不触发审批 hook）。`/write`、`/ingest` 及其他会触发审批的命令 → 固定回复"该命令需要人工审批，请到 Web 端使用"（IM 内审批卡片在解锁表，触发条件：第一个需要在 IM 里审批的真实场景）。

### 4.4 并发纪律

- 同一 `session_key` 串行：处理中的会话再来消息 → 排队（FIFO，上限 5 条，超限回一句"正在处理中，请稍候"）；跨会话可并发；全局受 `MAX_CONCURRENT_RUNS` 约束。

### 4.5 API 与前端

- `GET /v1/channels`：`{"onebot": {"connected": bool, "connected_since": ts}}`，挂 X-API-Key。
- 前端 `/chat` 顶栏渠道在线状态 chip（`onebot_connected`）；会话列表来源徽标（web/qq，`session_key` 前缀判别）。

### 4.6 env 清单（新增）

`ONEBOT_WS_TOKEN`（WS 鉴权）、`QQ_OWNER_IDS`（白名单）、`QQ_BOT_SELF_ID`（群 @ 匹配）。全部进 `.env.example` 与上线手册。

### 4.7 已知取舍（记录，不提前修）

- 群聊不做会话化（无 STM 连续性），只做 @ 即答——私聊才是主场景。
- /cmd 切 crew 后 LTM 仍按 crew 隔离（v1 现状）；跨 crew 记忆连续性按解锁表触发条件解锁。
- QQ 侧无 routed_crew SSE（事件流不转发），透明度靠回复前缀标注。

### 4.8 测试

- 单测：adapter token 鉴权拒绝、单连接顶替、组包与超长分段、断连时 send 降级（FastAPI TestClient websocket + fake 连接）；白名单；@ 段匹配与剥离；session_key 映射；不可信包裹格式。
- 集成：fake WS 客户端连入 → 推 message 事件 → mock LLM 跑通 → 断言 send 调用参数与内容；非 owner 无 send 调用；HITL 命令的引导回复。
- e2e（手动，真实 QQ）：扫码登录后私聊一轮默认问答 + 一轮 /kb + 一轮 /write（应收到引导提示）。

### 4.9 验收门（全部满足才进 S2）

1. CI 全绿（现有 pytest dummy-key 模式 + 新增测试）。
2. 本机 compose 五容器 healthy（含 napcat），`/health` 200。
3. 真实 QQ 端到端：私聊提问收到 agent 回复（默认链路）；`/kb` 命中知识库且回复带转交标注；`/write` 收到 Web 引导；非 owner 发消息无回复但有日志；群内 @ 不响应未配置时静默。
4. 断连演练：`docker compose stop napcat` 一段时间 → 恢复后自动重连续服；`/metrics` 的 `onebot_connected` 可见变化。
5. 文档增量：上线手册（env 清单、napcat 容器部署 + 扫码登录步骤、回滚步骤）；**新建 `doc/运行账本.md` 记第一条**。
6. 开始 7 天自用验证（一周 ≥10 次真实提问）；结果记入运行账本后本 Slice 才允许关闭。

---

## 5. Slice 2 规格：定时任务与推送（巡检告警 + KB 日报）

**目标一句话**：把"常驻助理"补齐——定时/事件触发的 job 跑 crew、产物按策略推 QQ；先做服务器巡检告警与 KB 每日日报两个真实任务。

### 5.1 数据模型（alembic 0010，纯新增）

```
jobs:
  id, name, enabled(bool, default true)
  trigger_type: cron | interval          # trigger_config 与之匹配
  trigger_config: JSON                   # {"expr":"0 8 * * *"} 或 {"seconds":1800}
  crew_id FK -> crew_configs             # 巡检走 crew，保持执行链单一
  input_template: text                   # 支持 {{date}} {{targets_report}} {{kb_delta}} 等占位
  output_config: JSON                    # {"push":{"on":"state_change|always|daily_summary"}}
  cost_cap_tokens: int null
  max_consecutive_failures: int default 5
  last_run_at, next_run_at, consecutive_failures int default 0

job_runs:
  id, job_id FK, status: running|succeeded|failed|disabled_by_circuit|eval
  started_at, finished_at, error
  tokens_used int, cost_note text        # /metrics 全局差值法，允许粗粒度，注明近似
  result_summary: JSON                   # 巡检存各 target 状态快照，用于状态变化检测
  pushed_to: text null                   # 'qq' / 'suppressed(eval)' / 'failed(channel_offline)' / null
```

### 5.2 调度器

- 新增 `backend/app/services/job_scheduler.py`：APScheduler `AsyncIOScheduler`（memory jobstore），在 main.py lifespan 启动，模式参考 sandbox_cleanup。
- 启动时从 `jobs` 表加载 enabled 任务注册；job CRUD 后同步增删（暴露 `reschedule(job_id)` 给 service 层调用）。
- 参数纪律：`max_instances=1`、`coalesce=True`、`misfire_grace_time=300`、`timezone=JOB_TIMEZONE`。
- 依赖：`apscheduler` 进 requirements.txt。

### 5.3 执行链（复用现有引擎，不另造执行器）

- 新增 `backend/app/crews/factory.py::run_crew_job(crew_id, message) -> JobOutcome`：复用 `build_crew_from_db` 装配与 kickoff，事件收进本地队列落 `job_runs`（截断存储），无 STM 会话上下文（job 一次性），KB 预注入按现有开关。
- 新 seed crew **ops_patrol**（seed.py 幂等新增，注意 SEED_DEMO_DATA 坑）：单 agent，挂 `http_check` + `push_message` 两个工具；任务 = 解读巡检结果、生成一行状态 + 异常初诊（哪个目标、从何时起、当时延迟/状态码、可能原因），按 output 策略推送。
- 新 seed crew **daily_digest**：输入模板注入 `{{kb_delta}}`，产出日报（新增了什么、每条两三句、值得关注的一条）。
- **kb_delta(since) 查询**（`backend/app/services/` 新增）：按 `documents.created_at` 增量取新增文档 + 各自头部 chunk 摘要，来源分组（SQL 为准）。
- **http_check 工具**（`backend/app/tools/http_check_tool.py`，注册进 tool_registry）：
  - 目标来自 env `MONITOR_TARGETS`（JSON：`[{"name":"api","url":"https://...","expect_status":200,"timeout_s":10}]`），工具参数只有可选的 `name` 过滤——**无自由 URL 参数**；
  - 直接 requests 调用（不走 fetch_url，不与 net_guard 的私网限制冲突；私网目标在 env 白名单里由运维显式声明）；
  - 返回结构化结果：name/status/latency_ms/error。
- **push_message 工具**（`backend/app/tools/push_message_tool.py`）：无目标参数；发送内容长度截断（1500 字）；返回推送结果供 agent 续作。

### 5.4 渠道与 Egress（复用 S1，不新建）

- 推送走 S1 的 `onebot_adapter.send_qq_message`——按 env `QQ_ALERT_TARGET` 组包（主动推送目标，与对话回复的 reply-to-source 是两条路径、同一个出口函数）；**全系统唯一出口**。
- 推送前检查连接：断连 → `pushed_to='failed(channel_offline)'` + 重试一次，不静默丢。
- eval 抑制：见 5.6。

### 5.5 REST API（最小）+ 前端

- `POST /v1/jobs`、`GET /v1/jobs`、`PATCH /v1/jobs/{id}`（enabled 等）、`GET /v1/jobs/{id}/runs`、`POST /v1/jobs/{id}/run`（手动触发，body 可带 `"eval": true`）。
- 挂 X-API-Key。前端配套（最小）：config 页新增 **Jobs 页签**（列表/启停/手动触发(eval)/最近运行状态）。

### 5.6 eval 零外发（S2 必须落地）

- `POST /v1/jobs/{id}/run` 带 `eval:true`（或 env `NEXUS_EVAL_MODE=1` 全局开关）→ 该次 run 状态记 `eval`，egress 直接跳过并记 `pushed_to='suppressed(eval)'`，且**不计入**连续失败熔断。
- 评测 runner 侧（`testing/eval/`）加一个 job 适配器：触发 → 轮询 job_run 终态 → 断言 `pushed_to == 'suppressed(eval)'`（零外发硬断言）+ result_summary 内容。这是调研文档 9.1 节红线的落地。

### 5.7 巡检与日报语义

- 默认 job：每 30 分钟巡检（interval），输出策略 `state_change`——与上一次成功 run 的 `result_summary` 对比，任何目标 up→down 或 down→up 时推送；另建一个每日 8 点 cron job 推送全部目标一行状态日报。
- KB 日报 job：每日 cron（时间用户定），推送策略 always（纯文本分段 <1500 字/条）。
- 推送目标 = `QQ_ALERT_TARGET`（建议私聊直达）；用户在 `MONITOR_TARGETS` 里放 2-5 个真实目标（自己的服务 + 至少一个公网站点做对照）。

### 5.8 测试

- 单测：调度器 DB↔APScheduler 同步逻辑（fake 时钟）；http_check（mock requests，含超时/非预期状态码/名称过滤）；push_message 截断；OneBot adapter 推送路径（S1 已建的补主动推送组包用例）；eval 抑制路径。
- 集成：`POST /v1/jobs` → 手动 run（eval:true）→ job_runs 记录 + 无真实外发（mock adapter 层断言发送调用数为 0）。
- 评测：5.6 的 job 适配器 + 1 条巡检用例（断言 result_summary 结构与 suppressed 标记）。

### 5.9 验收门（全部满足才进 S3）

1. CI 全绿（现有 pytest dummy-key 模式 + 新增测试）。
2. 本机镜像重建部署成功：`docker compose build && up -d`，`/health` 200。
3. 本机真实运行：早 8 点日报连续 7 天到达 QQ；人为停掉一个监控目标，下一巡检周期内收到 down 告警，恢复后收到 up 通知（本机不睡眠前提，账本如实记录）。
4. **断连演练**：`docker compose stop napcat` 一晚，确认期间 job_runs 如实记录 `failed(channel_offline)` 且容器恢复后自动重连续推（无静默丢失）；`/metrics` 能看到 `onebot_connected` 变化。
5. 一次 eval 模式全流程验证零外发。
6. `doc/上线部署手册.md` 增量（env 变量清单、MONITOR_TARGETS/QQ_ALERT_TARGET 格式、回滚步骤）；运行账本记录（含 7 天自用结果与单日成本实测）。

---

## 6. Slice 3 规格：钉钉告警备用通道（定向变更版 2026-09-17，替代原"钉钉双向接入"；**当日用户裁定搁置——代码已上线但休眠，验收演练取消**）

> **状态（2026-09-17）**：代码完成并部署（commit `3c14cef`，单测 384/集成 62/CI 三 job 全绿，live eval 零外发复验通过），但用户裁定"做了也没意义，暂时搁置"——不建机器人、不做真实演练、不推进 7 天验证。`DINGTALK_PUSH_WEBHOOK` 未配置 = 备推关闭 = 零行为差异，无需回滚。解锁条件（任一）：QQ 渠道再发真实失联/风控事件造成告警丢失；用户重启意愿（启用成本 ≈ 2 分钟：建群加机器人 + 填 env + `up -d`）。以下规格存档备启用时用。

> **变更缘由（2026-09-17 用户定向）**：原方案的一个隐含动机"监听公司钉钉群的公告消息"经核实**不可行**——用户非企业管理员，自建企业的机器人进不了公司组织的群（钉钉企业间完全隔离）；群自定义机器人只能发不能收；公告类消息不 @ 机器人，永远到不了机器人；钉钉无个人消息 API，也无成熟第三方协议端（公司 IM 上跑非官方客户端 = 纪律/安全事件风险，不做）。而"自建企业里自用问答"对用户无真实场景（7 天 ≥10 次提问必然过不了，防玩具纪律直接砍）。保留价值 = **QQ 被风控时的告警兜底通道**（2026-09-16 NapCat 风控失联约 5 小时的教训）。原双向细化稿（dingtalk-stream SDK 事实核实：conversation_type/sender_staff_id/session_webhook 过期时间等）存档于 git `923d5d3`，未来做入站钉钉直接复用；**im_pipeline 抽象重构与"抽象完型"验收移至 S4 飞书（阶段 A）**。

**目标一句话**：告警/日报推送在 QQ 渠道失败时自动兜底推到用户自建钉钉群（只含自己的群 + 自定义机器人 webhook），QQ 主通道行为与落账语义零变化。

### 6.1 拓扑与凭据（无新依赖、无企业应用）

- 用户手机钉钉自建群（成员只有自己）→ 群设置 → 机器人 → 添加**自定义机器人**，安全设置选**加签** → 得 webhook URL + 加签密钥。
- env（全部进 .env.example 与上线手册 §十六；不变量 4——只进 env 不进 git）：
  - `DINGTALK_PUSH_WEBHOOK`（webhook 完整 URL；**空 = 功能关闭 = 天然回滚**）
  - `DINGTALK_PUSH_SECRET`（加签密钥，与 webhook 成对必配）
  - `DINGTALK_PUSH_MODE=backup|always|off`（缺省 **backup**）
- 不需要企业内部应用、不需要 dingtalk-stream SDK、**零新依赖**（requests 已在镜像，不变量 7 自动满足）。

### 6.2 发送协议（纯函数，单测覆盖）

- 加签（官方 demo 同款）：`sign = urlencode(base64(HMAC-SHA256(key=secret, msg="{timestamp_ms}\n{secret})))`，追加 `&timestamp=..&sign=..`（webhook 已含 access_token 参数，用 `&` 拼接）；**时钟偏差 >1h 拒签**（部署手册注明）。
- POST JSON `{"msgtype":"text","text":{"content": 段}}`；响应 `errcode==0` 判成功（310000=签名/安全设置错）。
- 纯文本 + 复用 `split_long_message`（1500/段、段间 0.3s，与 QQ 同纪律；不启用钉钉 markdown，保持格式面最小）；超时 10s 单次尝试——无状态 HTTP，无"渠道在线"概念，不做断连重试。
- 限流 20 条/分钟（机器人侧硬限）：state_change + 日报天然稀疏，不额外做客户端限速。

### 6.3 egress 集成与落账（一切 egress 调用方自动获得兜底）

- 新服务 `backend/app/services/dingtalk_push.py`：加签 + 发送 + 分段（渠道发送细节封闭在此）；egress 只做 mode 判定与编排。
- `push_to_qq` 主流程不变；主推送结束后按 mode 决定备推：
  - `backup`：仅当主推送 `pushed_to` 为 `failed(...)`（含 channel_offline / no_target / api_error）时兜底；
  - `always`：每次与 QQ 双发（QQ 成功也发）；
  - `off` 或 webhook/secret 未配置：现状（QQ 单通道）；mode 非 off 但凭据缺失时每次推送记一条 WARNING（推送稀疏，不构成噪音）。
- **eval 零外发不变量延伸（硬红线）**：eval 抑制的早返回先于一切发送——QQ 与钉钉在 eval 模式下都必须零调用，集成测试断言两个出口调用数均为 0。
- 落账（alembic 0011，纯新增）：job_runs 加 `pushed_to_backup`（text null：`dingtalk` / `failed(...)` / null=未尝试）；**`pushed_to` 主渠道语义不变**——eval 硬断言、前端徽标、既有查询零影响。`PushOutcome` 增 `backup` 字段，agent 侧推送与 runner 兜底推送共用一套落账（既有纪律）。
- push_message 工具回话如实反映兜底：QQ 失败但钉钉送达时提示"已兜底推送"，不让 agent 向用户误报"全部失败"。

### 6.4 测试与验收门

- 单测（`testing/unit/test_dingtalk_push.py`）：加签固定向量（独立预计算 pin 进测试）；客户端 mock requests（errcode 0 / 310000 / 超时 / 网络异常）；mode 三态（backup 只在主 failed 触发、always 双发、off/未配置零调用）；eval 模式两渠道零调用；分段复用。
- 集成（`testing/integration/test_api_jobs.py` 扩展）：QQ 渠道离线（mock onebot offline）+ 假 webhook → `pushed_to=failed(channel_offline)` 且 `pushed_to_backup=dingtalk`；eval run 断言两个发送出口调用数均为 0。
- 评测：job_zero_egress 用例断言不变（suppressed(eval) 下 backup 必为 null）。
- **验收门**：①CI 全绿；②镜像重建部署 `/health` 200 + 0011 迁移生效；③**真实兜底演练一次**：停 napcat → 手动触发巡检 job → 钉钉收到兜底告警 + job_runs 两列如实落账 → 恢复 napcat → 下一轮推送回 QQ 主通道；④上线手册 §十六 + 账本记条目。7 天验证**并入 S2 窗口**（日报到达记录表加"到达渠道"备注），不单设 ≥10 次提问门（本切片无问答功能，无自用提问面）。

## 7. Slice 4 规格：飞书双向接入（用户指定的最终验收；前置：S2 7 天验证关闭 + 用户确认真实自用场景）

> 2026-09-17 联动调整：原 S3 的**im_pipeline 抽象重构与"抽象完型"验收移到本 Slice 作为阶段 A**（S3 定向变更后，飞书是第一个真实第二入站渠道——抽象在其接入前完成并回归，接入本身零核心改动）。钉钉双向方案的 SDK 事实核实存档见 §6 变更注记（git `923d5d3`）。
> **开工门（S3 搁置教训）**：动手前先回答"我会真的每天在飞书里跟它说话吗"——没有肯定答案就不开（防玩具纪律，S3' 当日搁置实录在账本）。

- **阶段 A（先做，纯重构零新渠道、无需凭据）**：onebot_adapter 内嵌入站管线抽为渠道无关 `backend/app/channels/im_pipeline.py`，行为不变，全量回归（单测/集成/评测 core 层）全绿才算完成。设计要点（原 S3 细化稿 §6.1 移入，2026-09-17）：
  - 归一化结构 `InboundMessage{channel, sender_id, text, is_group, owners, reply: Callable[[str], Awaitable[None]]}`——adapter 把原生事件解析成它，之后进入管线；回复闭包把渠道发送细节封闭在 adapter 内；
  - 管线主入口 `handle_inbound(msg)`：白名单 → `wrap_untrusted`（单份共享，不变量 5）→ 路由 v0（同一纯函数）→ HITL 定案 A（`IM_ALLOWED_COMMANDS={"/kb"}` 共享常量）→ 并发上限检查 → 会话串行排队（lock key `{channel}:{sender_id}`，跨渠道天然不互锁）→ `run_crew_chat`（run_id 前缀 `{channel}-`）→ 回复；
  - 平移内容（from onebot_adapter，行为不变）：`wrap_untrusted` / `_ensure_session` / `_run_crew_and_collect`（含 run_control 登记）/ 会话锁与排队 / MAX_CONCURRENT_RUNS 检查与"正在处理"提示 / Web 引导文案 / `[已转交 X]` 前缀 / CREW_DISPLAY_NAMES / `split_long_message`；
  - adapter 保留（渠道差异封闭清单，验收审查对象）：传输层（连接/鉴权/重连/接收循环）、原生事件解析（@ 判定与剥离、群/单聊判定、字段容错）、owner env 解析、回复发送 API、在线状态 gauge；
  - 渠道注册表：channels 内聚合各渠道 `get_status()`，`GET /v1/channels` 与前端 chip 改为遍历注册表，不 import 具体渠道；
  - 阶段 A 验收：onebot 相关单测平移全绿 + diff 审查确认 im_pipeline 无 QQ 特有引用（无 user_id/group_id 字段、无 send_qq_message 调用、无 "qq" 字面量分支）。
- **阶段 B**：飞书**企业自建应用** + `lark-oapi` SDK **WebSocket 长连接**订阅 `im.message.receive_v1`（免公网回调；个人免费建"企业"即可）；凭据 env `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。`backend/app/channels/feishu_adapter.py` 只写传输/解析/回复，核心链路全部来自 im_pipeline；验收 = 第二入站渠道接入对核心链路**零改动**，多渠道网关就此收口。
- 边界校准（同钉钉教训）：飞书同样是自建企业里的机器人，**监听不了用户所在公司组织的飞书群**——S4 的用户价值定位是"自用第二问答渠道 + 抽象完型"，不是消息监听。
- 验收门：一周 ≥10 次真实提问 + 抽象完型审查（原 §6.8.4 条款移此：services/crews 零渠道分支守卫 + 人工 diff 复核）。

---

## 8. 解锁登记表（推迟组件，填了触发证据才准建）

| 组件 | 调研文档 | 触发条件 | 触发证据（日期+事实） |
|---|---|---|---|
| LLM 路由器 + 8.6 保障套件 | §8.2/8.6 | ≥4 个 crew 日常在用，或 /cmd 记忆负担成痛点 | —（2026-09-14 用户确认按计划路径：先收集使用证据） |
| plan 编译器 + 管道执行器 | §8.8 | 同一复合任务第二次出现 | — |
| HITL IM 卡片审批（QQ/钉钉/飞书，含 Redis Lua 原子化） | §4/§8 | 第一个需要在 IM 里审批的场景 | —（S1 定案 A：QQ 侧 /write 等引导回 Web） |
| 订阅自动摄入 + KB 治理 | §9.5 | 手动入库成为负担 | — |
| config 页 Channels 管理面板 | §六 P2 | S3 双渠道后 curl 不够用 | — |
| factory.py 模块拆分（roadmap P2 遗留） | roadmap | 评测基线稳定后专门排期 | — |
| 助手级 LTM 命名空间（含 ChatSession.crew_id 可空化） | §9.2 | 跨 crew 记忆连续性痛点真实出现 | — |
| web_ingest_crew 拆分为 plan | §8.8 | plan 编译器已解锁且需要 | — |

---

## 9. 每条 Slice 的完成定义（DoD 模板）

1. 代码合入 master_server，CI 全绿（单测 + 集成，dummy key 模式）。
2. `docker compose build` 成功，新依赖出现在安装清单里（教训 #6）。
3. **本机** compose 部署 + `/health` 200 + 真实端到端结果（进手机：QQ 消息真实到达）。云端部署不逐 Slice 做，统一走第 10 节收官。
4. 安全不变量逐条自查（第 3.2 节）；S2 起 eval 零外发验证一次。
5. 文档增量：上线手册（env/配置/回滚）+ 运行账本记录。
6. 开始 7 天自用验证；结果（保留/砍掉/重做）记入运行账本后，本 Slice 才允许关闭。

---

## 10. 云端迁移收官（一次性步骤，S4 验收后执行）

> 开发期一切在本机；此节是唯一的上云动作清单。执行前置：服务器选型（按届时本机实测稳态/峰值占用决定；参考量级：纯 Nexus 栈 2C4G40G 舒适，混部另加邻居峰值；大陆 VPS 优先——NapCat 风控）。

1. 代码合入 + `docker compose build && up -d` + `/health` 200（五容器）。
2. 容器内一次性 `ensure_seed()`（`SEED_DEMO_DATA=false` 坑：退役清理与 seed prompt 才会同步到云端库）。
3. q04 语料重传：删云端旧《Nexus简历梳理》文档，上传新版 `testing/简历项目梳理-Nexus.md`（保持文档名一致）。
4. markdown_write crew 三处收敛修改（云端 config 页操作；对照清单见 testing/eval/评测报告-20260909.md §十"补充"小节：blog_orchestrator 收敛纪律+max_iter 12、任务"解析写作意图"检索预算、任务"提炼结构化风格指令"禁再检索）。
5. 数据迁移：本机 `pg_dump` → 云端导入（会话/KB/记忆/配置）。
6. NapCat 迁移：云端容器部署 + QQ 数据 volume 搬运（或云端 WebUI 经 SSH 隧道重扫码一次）。
7. **停用本机 NapCat**（单实例铁律——先起云端、验收连通后再停本机，避免互踢窗口）。
8. 7 天观察期（告警可达性 + 通道稳定 + 评测云端基线复跑一次），运行账本记录；此后"本机休眠即通道中断"限制解除。
9. 回滚路径：`jobs.enabled=false` / `docker compose stop napcat` / 镜像回滚上一 tag。

---

## 附：与调研文档的章节映射

- 渠道事实（QQ/NapCat 风险、钉钉/飞书接入方式）：调研文档 §1
- v2 总架构与数据模型雏形：§2/§3
- 安全设计（本计划 3.2 是其子集）：§4
- 评测扩展：§5/§8.5/§8.8；遗漏补遗：§9
- 防玩具执行纪律的完整表述：§10（本计划即其执行化）
- 业界成熟度对照（2026-09-14 核验）：迭代预算与收敛纪律 ≈ LangGraph recursion_limit / AutoGen max_round / OpenAI Agents SDK max_turns 的"安全网+显式退出条件"共识；确定性命令路由 ≈ Claude Code slash commands 与 hybrid routing 模式（规则优先、LLM 兜底）；评测层 ≈ MT-Bench/LLM-as-judge 校准管线（golden 集 + 一致率门禁 + 回归门控）。
