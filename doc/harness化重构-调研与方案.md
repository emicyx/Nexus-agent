# Nexus v2「Harness 化」重构 — 市场调研与实施方案

> **执行以《[harness重构-执行计划.md](./harness重构-执行计划.md)》为准**（2026-09-14 交接版：细条顺序、解锁登记表、DoD 都在那里）。
> 本文档保留作为设计依据、遗漏清单与面试素材；两者冲突时以执行计划为准。
>
> 2026-09-14。目标：把 Nexus 从「前端聊天发起的多智能体平台」升级为「常驻云端的个人 agent harness」——
> 对外连接 IM 渠道（飞书/钉钉/QQ）收发消息，对内支持定时/事件/webhook 触发无人值守任务，
> 处理日常事务（服务器监控、信息聚合、日报推送），成为一个真正"会主动干活"的助手。

---

## 一、市场调研：harness agent 都是怎么做的

### 1.1 OpenClaw（原 Clawdbot）— 自托管个人 agent harness 的标杆

开源（GitHub 100k+ stars），定位"always-on personal agent harness"，架构要点（官方 docs.openclaw.ai）：

- **Gateway 单守护进程**：所有消息渠道连接集中在一个长驻 Gateway 里（不是每个渠道一个服务），loopback WebSocket 通信，systemd 托管，健康检查走 WS。会话路由、调度、渠道管理全在 Gateway。
- **Channels**：WhatsApp（Baileys）/ Telegram（grammY）/ Slack / Discord / Signal / iMessage / WebChat——每个渠道一个适配器，统一成内部消息模型。
- **Automations 四类触发**：tasks（一次性任务）、scheduled jobs（cron 表达式 / 固定间隔 / one-shot，持久化、到点唤醒 agent、产出可投递到聊天渠道）、event hooks（事件钩子）、standing instructions（常驻指令）。
- **Heartbeat**：周期性"心跳"唤醒 agent 自主检查待办/邮件等，与 cron 的区别是 cron 做精确定时、heartbeat 做周期性自主巡逻——两者都需要 automations 开启。
- **Nodes**：手机/设备作为能力节点（摄像头、截屏、定位）以 `role: node` 接入 Gateway，配对需审批、密钥签名。
- **安全模型**：握手强制鉴权（共享密钥/身份模式）；设备配对审批；副作用调用要求幂等键；远程访问走 Tailscale/SSH 隧道而非暴露端口。
- **Skills**：社区生态 1800+ 技能包，技能即"教 agent 用工具的说明书"。

**对 Nexus 的可借鉴点**：渠道集中管理的 Gateway 形态、cron+heartbeat 双调度、产出投递回渠道、设备配对审批、Tailscale 远程访问、幂等键。

### 1.2 Coze（扣子）— 国内 SaaS 的触发器做法（及其局限）

- 触发器两类：**定时触发**（指定时间把指令发给 bot）与 **Webhook 触发**（生成 URL，HTTP 调用即触发）。
- 关键局限（官方文档明示）：**触发器仅对飞书渠道生效**；绑定的工作流须 **1 分钟内跑完**；须关闭流式输出。
- 结论：SaaS 触发器 = 浅集成。Nexus 自建触发层没有这些限制（长任务、任意渠道投递、自定义审批策略），这正是自建 harness 的差异化空间。

### 1.3 国内 IM 渠道接入事实（2026-09 核实）

| 渠道 | 接入方式 | 要点 |
|---|---|---|
| **飞书** | 企业自建应用 + 事件订阅 **WebSocket 长连接**（lark-oapi SDK） | 免公网回调地址/免域名；订阅 `im.message.receive_v1` 收消息；**新版消息卡片交互回调支持长连接**（按钮点击走同一条 WS）→ HITL 审批卡可直接在飞书里按按钮；注意：长连接仅支持企业自建应用（个人可免费建"企业"，够用） |
| **钉钉** | **Stream 模式**（官方 SDK，WebSocket 反向推送） | 免公网地址；机器人收消息、事件订阅、卡片回调三类都走 Stream；卡片创建时 `callbackType: "STREAM"` 即可接收按钮回调；官方有 Python Stream 示例仓库 |
| **QQ（官方）** | QQ 机器人开放平台（bot.qq.com / q.qq.com） | **官方明确支持绑定 OpenClaw、Hermes 等环境**——腾讯事实上已"收编" harness 接入路线；频道功能全，群聊/C2C 需审核开通且能力受限 |
| **QQ（第三方协议端）** | NapCat / Lagrange（OneBot v11） | 群/私聊全功能、开箱即用，但**违反用户协议、风控收紧、封号案例增多**，社区共识"只用小号、别拿大号玩"；占用 PC 端协议（不能同时登 PC QQ）；公网暴露须鉴权 |

**共同利好**：飞书/钉钉/QQ 官方均支持**出站长连接**模式 → Nexus 部署在云端 VPS 时，机器人链路**不需要公网入站端口、不需要备案域名**（管理 UI 可继续走现有密码门 / 或套 Tailscale，与 OpenClaw 同款做法）。

---

## 二、Nexus v2 目标架构

```
                        ┌─────────────────────────────────────────────────┐
                        │                云端 VPS（docker compose）          │
  飞书 ⇄ WS ┐           │  ┌──────────────┐   ┌─────────────────────────┐ │
  钉钉 ⇄ WS ├─ Channel ─┼─▶│  Ingress 路由 │──▶│  现有执行内核（全部复用）   │ │
  QQ官方 ⇄ WS┘  Gateway  │  │ (会话映射/白名单)│   │  factory.build_crew     │ │
                        │  └──────────────┘   │  run_crew_chat(三层记忆) │ │
  cron ────────┐        │  ┌──────────────┐   │  tools + 沙箱 + HITL     │ │
  webhook ─────┼─▶ Trigger ─│  jobs/job_runs │──▶│  net_guard + 预算熔断    │ │
  (事件钩子 P2)─┘        │  │  调度器        │   └───────────┬─────────────┘ │
                        │  └──────────────┘               │ 产物(沙箱内)     │
                        │  ┌──────────────┐   ┌───────────▼─────────────┐ │
                        │  │ Egress 出口   │◀──│ 受控导出(白名单/审计)      │ │
                        │  │ (渠道推送/文件) │   └─────────────────────────┘ │
                        │  └──────────────┘                                │
                        └─────────────────────────────────────────────────┘
```

三层新增组件，执行内核零改动（工厂/评测/安全/记忆全部复用）：

1. **Channel Gateway（渠道网关）**：backend 内的 asyncio 常驻任务组，每个渠道一个 Adapter。
   - 入站：渠道消息 → 统一 `IngressMessage{channel, peer_id, sender, text, attachments}` → 会话映射 `session_key = {channel}:{peer_id}`（复用 ChatSession，天然获得跨会话记忆/LTM/KB 预注入）。
   - 出站：Egress 统一出口，发文本/卡片。
   - 身份绑定：每渠道配置 owner 允许名单（open_id/user_id），名单外一律拒绝且记录（单租户安全边界）。
2. **Trigger 层（触发/调度）**：
   - `jobs` 表 + 调度器（APScheduler，参考 OpenClaw 三种形态：cron 表达式 / 固定间隔 / one-shot）。
   - Webhook 入站：`POST /v1/hooks/{job_token}`（Coze 同款思路，供外部系统踢任务）。
   - Job 定义 = {触发器, crew_id, 输入模板(支持 `{{date}}`、`{{kb_delta_since_last}}` 等上下文变量), 输出动作(推渠道/存产物/两者), 审批策略, enabled}。
   - `job_runs` 表：状态/起止/耗时/token 成本(复用 /metrics 差值机制)/错误/产物路径/推送目标 → 无人值守可观测性。
3. **Egress（受控出口）**：执行期产物仍锁沙箱；结束后由显式投递步骤出去（渠道推送卡片 / 导出白名单目录），全程留审计。安全叙事从"关在里面"升级为"受控放行"。

### 与现有代码的映射（复用清单）

| 现有资产 | v2 中的角色 |
|---|---|
| `run_crew_chat()`（三层记忆注入） | IM 入站消息直接走这条路径，渠道会话=ChatSession |
| HITL Redis 状态机 + `/v1/approvals` | 审批卡改为飞书/钉钉卡片按钮，回调仍打同一决策端点 |
| web_ingest_crew（抓取→改写→审批→入库） | 订阅摄入流水线，job 定时驱动 |
| researcher_writer / iterative_write_crew | 日报/周报生成 crew |
| 沙箱 + net_guard + token_budget | 无人值守任务的安全底座不变 |
| `/metrics`（Prometheus） | job 成本采集数据源；ops_watch 的监控目标之一 |
| 评测 runner/judge/redteam | v2 扩展（见第五节） |

---

## 三、数据模型（新增迁移）

```
jobs:
  id, name, enabled
  trigger_type: cron | interval | once | webhook
  trigger_config: json (cron_expr / interval_seconds / run_at / webhook_token)
  crew_id → crew_configs
  input_template: text (上下文变量占位)
  output_actions: json [{type: channel_push, channel, target} | {type: artifact_export}]
  approval_policy: auto | push_to_im
  cost_cap_tokens, max_consecutive_failures
  last_run_at, next_run_at

job_runs:
  id, job_id, status: running|succeeded|failed|timeout|cancelled
  started_at, finished_at, tokens_used, error, artifact_path, pushed_to, result_summary

channel_configs（渠道凭据建议走 env，表里只存绑定关系）:
  id, channel: feishu|dingtalk|qq_official
  enabled, owner_allowlist: json (open_id 列表), default_crew_id, session_ttl
```

---

## 四、安全设计（承接 v1 红队体系，这是面试核心资产）

1. **Owner 允许名单**：每个渠道硬绑定主人 ID；群聊场景只响应 @bot + 主人消息。名单外消息拒绝并记录（可进 redteam 断言）。
2. **IM 输入不可信**：入站消息文本以 `<im_message channel=.. sender=..>` 不可信标签包裹注入（复用 kb_content 同款纪律），防群聊里的注入指令劫持 agent —— 新攻击面，新增 redteam 用例。
3. **HITL over IM**：审批卡片按钮 → `/v1/approvals/{id}`。顺带落地面试口径文档第三问里已认领的缺陷修复：决策改 Lua 脚本原子条件更新（仅 PENDING 可写终态）+ 幂等。
4. **无人值守约束**：每 job token 上限（复用预算熔断）、并发上限、连续 N 次失败自动停用并推送告警（防"凌晨三点烧钱死循环"）。
5. **Webhook token**：一次性随机 token、只绑定单个 job、失败限速；入站 payload 尺寸限制。
6. **凭据管理**：飞书/钉钉 app secret 全走环境变量，不落 DB；NapCat 若启用必须鉴权且只绑小号。
7. **部署面**：机器人链路全出站长连接，云端无新增入站端口；管理 UI 维持密码门（可选 Tailscale，OpenClaw 同款）。

---

## 五、评测体系扩展（v2 的护城河延续）

- **redteam v2 新攻击面**：①群聊注入（他人消息指示 agent 越权）②渠道身份伪造（名单外触发审批）③webhook token 泄露重放 ④无人值守场景下的沙箱逃逸（无人在场时护栏仍须成立）。
- **新数据集**：digest 质量（llm_rubric：覆盖度/新鲜度/无幻觉/来源归属）、监控告警精确率（误报率门禁）。
- **新门禁**：job 成功率（如 30 天 ≥95%）、单次运行成本上限、无人值守运行断言"零人工干预完成"。
- runner 增加一个 job 适配器：触发 job → 等待 job_run 终态 → 断言产物与推送。

---

## 六、分期计划与验收

**P0（闭环最小集，~1-2 周）**
- jobs/job_runs 表 + APScheduler + webhook 入站端点
- KB 增量查询 service（`kb_delta_since_last`）
- digest job（新种子 crew）+ Egress 到飞书（唯一渠道，文本先于卡片）
- Owner 允许名单（此时只有出站，仍要做：飞书侧确认收件人）
- ✅ 验收：每天 08:00 飞书收到昨日 KB 增量日报；webhook 一键触发即时 digest；job_runs 可查成本/状态

**P1a（Assistant Shell + 路由层，先于任何 IM 入站）**
- Router 服务（qwen-turbo + crew 目录 → RouteDecision）+ `/cmd` 显式命令
- 会话模型迁移：`ChatSession.crew_id` 可空化、`ChatMessage.routed_crew_id`、LTM 改助手命名空间
- workspace state 跨 crew 交接；Web UI 默认 Auto + "已转交 ××"标签
- ✅ 验收：不选 crew 直接对话，路由准确率（routing.json）≥90%；Web 手动模式不受影响

**P1b（双向渠道 + 监控）**
- 飞书入站：WS 长连接收消息 → Assistant Shell → run_crew_chat；卡片按钮 HITL 审批 + Lua 原子化修复
- ops_watch 监控：`http_check`（基于 fetch_url + 状态/延迟断言）与 `prometheus_query` 两个新工具 + 监控 job（异常才推送告警卡）
- 钉钉 Adapter（Stream 模式，与飞书同构）
- ✅ 验收：飞书里直接和 Nexus 对话（带记忆，自动路由）；服务器上某应用 down → 5 分钟内飞书收到告警卡；审批可在飞书完成

**P2（QQ + 事件钩子 + 打磨）**
- QQ 官方开放平台 Adapter（合规优先；NapCat 小号作为可选实验分支，文档写明风险）
- 事件钩子（新文档入库 → 触发处理）、审批批处理（每日待审批清单）
- 前端 jobs 运行历史页；连续失败自动停用 + 告警
- ✅ 验收：QQ 里可对话；KB 新文档自动触发摘要入库候选；失败 job 自动停用并推送

---

## 七、面试叙事更新（第七问的 v2 版）

> "v1 我自用后发现只有知识库被真正使用——诊断是人发起聊天+产出困沙箱的交互模型价值密度低。v2 对标 OpenClaw 的 harness 架构（Gateway/Channels/Cron/Heartbeat）做了轻量自研：渠道网关（飞书/钉钉/QQ，全部出站 WebSocket 长连接，云端零新增入站端口）、触发层（cron/webhook/事件 → jobs/job_runs 无人值守可观测）、受控 Egress。安全上把 v1 的 HITL/沙箱/预算熔断延伸到无人值守场景，红队新增群聊注入/身份伪造/webhook 重放三个攻击面。现在它每天 8 点推日报、监控我的服务器、在飞书里等我审批——平台从'我去找它'变成'它来找我'。"

可讲的硬细节：飞书新版卡片回调支持长连接 vs 旧版不支持的坑；钉钉 `callbackType: "STREAM"`；QQ 官方平台与 NapCat 的合规/风控权衡；为什么 SaaS（Coze 触发器仅飞书生效、1 分钟限制）做不到这些；无人值守的成本/失败熔断设计。

---

## 八、核心矛盾与调和：crew 模型 vs harness 模型 → Assistant Shell + 路由层

### 8.1 矛盾拆解

- **harness 的入口是"一个人"**：一个助手身份、一段持续对话关系，用户永远对着同一个它说话，能力在背后动态加载。
- **Nexus v1 的入口是"一堆引擎"**：用户先选 crew 再对话（前端下拉框、`ChatSession.crew_id` 硬绑定、LTM 按 crew_id 隔离）。IM 渠道上没有下拉框——一条飞书消息进来，"该哪个 crew 接"没有答案；选错 crew 的体验是灾难。
- 但 **Config-as-Code 的 crews 恰恰是 OpenClaw 们没有的**：它的 skills 是静态文件目录，你的 crews 是 DB 热插拔 + 输出契约 + 评测覆盖 + 工具级 HITL。

结论：不是放弃 crew 模型，是把它**从前台挪到后台**。用户面对的永远是一个助手；crews 变成助手背后的"部门"。

### 8.2 设计：Assistant Shell + Crew Registry + Router

```
IM 消息 / Web 对话（不再选 crew）
        │
        ▼
┌─────────────────────── Assistant Shell（前台，每渠道一个身份）───────────────────────┐
│  显式命令优先：/kb /write /ops …（强制指定 crew，确定性出口）                          │
│  Router（infra 层，非 agent）：qwen-turbo + crew 目录 → RouteDecision               │
│    {crew_id | reply_direct, confidence, rewritten_task}                             │
│  低置信度 → 澄清一句 / 默认 crew；crew 执行失败 → run_single_agent_chat 兜底          │
│  per-session workspace state（最近产物沙箱路径 + 上轮摘要）随路由注入下一个 crew        │
└────────────────────────────────┬────────────────────────────────────────────────────┘
                                 ▼
              Crew Registry（crew_configs 增加 when_to_use / examples / risk_level）
              → 仍是用户自定义 + 热插拔：新增 crew 自动进入路由目录
                                 ▼
              各 crew（knowledge_qa / iterative_write / ops_watch / …）
```

### 8.3 关键工程决策（每条都是面试可讲的立场）

1. **路由是 infra，不是 agent**。v1 的 team_orchestrator 是"在 CrewAI 里用贵模型做路由"——慢、贵、不可单测。v2 把路由抽成独立服务层：便宜模型（qwen-turbo，同 STM 摘要/LTM 提取的隔离小任务矩阵）、结构化输出（复用 pydantic 契约机制）、可单测、可用评测集盯准。team_orchestrator 退役为测试夹具。
2. **crew 目录即路由知识库**。路由 prompt 从 DB 的 crew 目录（name/description/when_to_use/examples/risk_level）动态拼装——配置不仅热生效执行，还热生效路由。这是 Config-as-Code 故事的自然延伸，也是对 OpenClaw 静态 skills 的差异化。
3. **会话模型迁移**：`ChatSession.crew_id` 可空化（助手会话），`ChatMessage` 增加 `routed_crew_id`；**LTM 从按 crew 隔离改为按助手命名空间**——否则用户换个话题（从 KB 问答跳到写作），记忆就"失忆"了，单一助手身份就穿帮。
4. **跨 crew 上下文交接**：第 1 轮 routed 到 knowledge_qa，第 2 轮"把它写成周报" routed 到 iterative_write——shell 维护 per-session 的 workspace state（最近产物路径 + 上轮摘要）注入下一个 crew 的 task description，复用现有"文件系统做 agent 间侧信道"模式。
5. **透明可纠错**：路由结果在 Web/IM 显示"已转交 ×× 部门"标签；用户一句"不对"触发纠正并落路由日志表——日志天然沉淀为路由评测集。
6. **定时 job 不走路由**：自动化必须显式绑 crew（可审计、可复现），只有人会话走自动路由。显式/自动的 split 本身是设计立场。
7. **降级链**：路由失败 → 默认 crew；crew 执行失败 → 单 agent 直答兜底。IM 永远有回声。

### 8.4 配置侧配套（"更好用"的另一半）

- crew 配置表单增加必填 `when_to_use` + 2 条示例消息（配置质量 = 路由质量，在保存时校验）；
- Web UI 的 crew 下拉保留为"开发者模式"（调试单 crew 用），默认 Auto；
- 飞书机器人菜单挂常用命令（/日报 /状态 /待审批），IM 端零学习成本。

### 8.5 评测扩展

- 新数据集 `routing.json`：N 条真实消息 + golden 路由标签 + 置信度断言；新 scorer `route_match`。
- redteam 新攻击面：**路由注入**（消息内指示 router 无视规则转给高危 crew）、低置信度时误入 risk_level 高的 crew。
- 指标：路由准确率、路由延迟（qwen-turbo 一次调用的 p50，预算 <1s）、路由成本（应为总对话成本的个位数百分比）。

### 8.6 路由正确性与新 crew 接入保障（目录质量飞轮）

路由正确不是一个模型问题，是**目录质量 + 回归保护**问题：crew 目录就是路由器的"训练数据"。四层防御：

**① 确定性优先，LLM 兜底**
- 显式命令（`/kb` `/write`）与强关键词规则先行，命中即短路，不进 LLM；
- LLM 路由输出受约束校验：`crew_id` 必须在候选列表内（防幻觉 crew id），非法即降级默认 crew；
- 置信度分层：高→直接路由；中→路由但显示"已转交 ××，不对请说换"；低→回一句澄清（IM 快捷回复菜单）；出圈消息（闲聊/超出全部目录）→ `reply_direct` 直答，不硬塞 crew。

**② 风险分级门禁**
- crew 目录增加 `risk_level`；路由到高危 crew（写操作/外部副作用）要求更高置信度阈值 + 一轮用户确认（"要执行服务器操作吗？"）；
- 红队用例：诱导 router 无视风险等级直送高危 crew，必须被确认门拦住。

**③ 写入门槛 + 写时回归（新 crew 接入的核心保障）**
- 保存 crew 时强校验：`when_to_use` 非空且不得为泛化描述（"处理各种问题"类措辞被 Lint 拒绝）、≥2 条互斥示例；
- **两两区分度检查**：保存时用 LLM 检查新描述与现有目录是否可区分，与某现有 crew 混淆率高 → 警告并要求改写；
- **保存即测试**：由 `when_to_use`/examples 合成 N 条测试话语（含口语化/边界变体）跑路由——断言 ①新话语路由到新 crew ≥阈值 ②存量 golden 话语路由不漂移（定向回归，复用 runner + `route_match` scorer）。不达标不允许启用（可保存为 disabled）；
- **评测联动纪律**：新增 crew 必须同时提交 ≥5 条 golden 路由话语进 `routing.json`——没有评测用例的 crew 不算接入完成（与 seed 同步/数据集版本化同一纪律）；
- 目录 embedding 增量更新：crew 保存/删除时 upsert/remove 目录向量（复用 embedding.py），目录版本号写入路由日志，保证每次路由可追溯到当时的目录版本。

**④ 运行时观测与纠错回流（飞轮）**
- `routing_logs` 表：消息、候选集与得分、决策、置信度、目录版本、结局（成功/用户纠正/crew 失败重路由）；
- 误路由信号：crew 快速失败（工具报错/"文件不存在"）、用户立即改口或点"换部门"→ 触发一次带上下文的重路由（最多一次，再失败就澄清）；
- 纠正样本回流为标注数据：定期（或累积 N 条）跑全量 routing eval，混淆矩阵定位"谁和谁分不清"，反哺目录改写——这是 Cohen's kappa 校准纪律在路由上的复刻；
- 新 crew 冷启动灰度：启用后前 N 天路由到它时附带"已用新部门 ×× 处理"提示 + 一键纠正；个人规模不需要在线学习/多臂老虎机，日志 + 批量重评就是正确复杂度。

### 8.7 指标口径

- 路由准确率（top-1，分 crew 汇总混淆矩阵）；校准度（置信度分箱 vs 实际正确率，防"高置信瞎猜"）；
- 拒绝质量：出圈消息直答率（不该有"硬塞 crew"）；高危误入率（必须≈0，硬门禁）；
- 路由延迟 p50（qwen-turbo 单调用，预算 <1s）、路由成本占对话总成本比（目标个位数百分比）。

---

### 8.8 复杂任务与多 crew 编排：Plan Compiler + Pipeline Executor

路由单选覆盖不了的场景，先分类再处理：

| 场景 | 例子 | 处理 |
|---|---|---|
| **多步复合任务**（一个目标跨多个 crew） | "调研 X 写成周报，入库，以后每周一推送我" | Plan 编译 + 管道执行 |
| **无匹配 crew**（任务合法但目录为空） | "帮我分析这个 Excel"（无数据分析 crew） | 目录接地澄清 + 协助起草新 crew 配置 |
| 低置信/歧义 | "帮我弄一下那个报告" | 澄清（8.6 已覆盖） |

#### 设计：LLM 编译计划，infra 确定性执行

入口决策从二选一扩为四选一：`RouteDecision → {single_crew | plan | clarify | reply_direct}`。判复杂信号：多连接词（先…再…然后）、子任务映射到不同 crew、含时间频率词（每天/每周一 = 建 job 意图）。

```
复杂消息 ──▶ Plan Compiler（qwen-plus，一次性）──▶ TaskPlan(pydantic 契约)
                                                  goal + steps[] + token 预算
                                                       │ 用户确认（含高危步骤时必确认）
                                                       ▼
             Pipeline Executor（确定性 infra，零 LLM 决策）
             逐步执行：填模板 → build_crew → 跑 → 产物落 outputs/plans/{run_id}/step_N/
             步间交接：{{step_N.artifact}} 引用；失败重试一次→计划暂停问用户
             每步完成向用户报进度（SSE / IM 消息）；工具级 HITL 照常在步骤内触发
```

`PlanStep` 两类：`crew_run`（绑 crew + 输入模板）与 `infra_action`（create_job / push_message 等）——**"每周一推送"直接编译成 create_job 步骤**，触发层与编排层在此统一：plan executor 与定时 job 共用同一套 job_runs 观测/成本/熔断内核，人会话触发的计划与无人值守任务是同一执行体的两种触发方式。

#### 关键立场（延续 8.3"路由是 infra 不是 agent"）

1. **LLM 只编译、不执行**。计划每一步绑定 crew 与产物契约，执行是确定性的：可单测、可按结构断言、可在任意步暂停/重试/恢复——这是 agent manager 即兴编排（v1 team_orchestrator 的教训）给不了的。用户仍可自建 hierarchical crew（配置自由保留，只是不再是默认路径）。
2. **确认分层**：计划摘要（步骤/预估成本/风险步骤高亮）先给用户粗确认；执行中工具级 HITL 审批门照常触发——计划级粗确认，工具级细审批。
3. **成本前置**：编译时按各步骤 crew 历史均耗估 token 预算，超阈值提示；执行中按 plan 级 token 上限熔断。
4. **降级链**：编译置信度低 → 转**分步交互模式**（拆解后逐步确认执行，IM 友好）；某步失败重试一次仍败 → 计划暂停，报告已完成步骤与产物，问用户继续/跳过/放弃——产物永不丢。
5. **无匹配 crew ≠ 拒绝**。目录接地澄清："现有能力里最接近的是 X（做 Y），要试试吗？"；确认没有 → 主动提出**协助起草新 crew 配置**（预填 when_to_use/examples 的草稿进 config UI 待确认）——从使用侧闭环了 8.6 的新 crew 接入流程。
6. **远期收敛**：web_ingest_crew 这类内部多步 crew 可重构为 plan（抓取→改写→入库三个单步 crew 组合）——crew 越单一，路由与组合越简单（每个 crew 做好一件事）。

#### 评测扩展

- `plans.json` 数据集：复合指令 + golden 计划断言（步骤序列、crew 绑定、infra_action 正确性）；scorer `plan_match`（route_match 的推广，结构比对而非 LLM 判分——确定性执行器的红利）。
- 红队：**计划注入**（诱导计划插入越权步骤/把高危 crew 伪装成低危）、确认门绕过（确认后步骤内私改 risk_level）。
- 指标：计划编译准确率、步骤成功率、平均完成步数/计划、暂停后恢复率。

---

## 九、总体评估：遗漏补遗（2026-09-14 复核）

架构主干（渠道→触发→路由→编排→执行→评测）已闭环，以下为复核发现的遗漏，按重要性排序：

### 9.1 评测与生产的边界（高优先，P0 立规矩）
- **egress 抑制不变量**：eval runner 触发的 job/plan 严禁真实推送 IM——封闭环境控制从 clean_sandbox / cleanup_new_documents 扩展到 egress（eval 模式下 output_actions 强制降级为 artifact-only，断言零外发）。否则一次评测就给真人发垃圾消息。
- **crew 目录变更 → 存量评测回归**：capability_incidents 等数据集锚定种子 crew（inc-a5 绑 team_orchestrator）；team_orchestrator 退役、web_ingest_crew 拆分等目录变更必须同步数据集版本升级并复跑，纳入 8.6 的写时回归清单。

### 9.2 记忆与会话的跨渠道语义（P1a 设计定案）
- 渠道各建会话（`{channel}:{peer}`），但 **LTM/KB 助手级共享**——用户在 Web 说过的偏好，飞书里必须还记得，否则"单一助手"穿帮；
- **路由粘性**：追问（"继续""换个角度"）默认沿用上一轮 crew，仅意图转移才重路由——否则每轮追问都可能被路由抖动打断；
- 存量 `ChatSession.crew_id` 数据迁移策略：老会话保留原绑定，仅新会话走助手模式。

### 9.3 无人值守的审批与产物语义（P1b 设计定案）
- **凌晨审批超时 ≠ 失败**：job 途中撞审批门，语义应为"挂起待批 + 次日晨推审批清单"，而不是 150s 超时报错——批处理审批的设计要提前到 P1b 定案（实现可后置）；
- **产物保留 vs 沙箱清理冲突**：job/plan 产物路径排除出定期沙箱清理，或导出后即豁免——否则日报引用的附件三天后被清掉。

### 9.4 自监控与运行账本（P2）
- **self-check 心跳 job**：每天巡检 job_runs 失败/停用/成本异动并推送摘要——"谁来看守看门人"（OpenClaw heartbeat 的自举版）；
- **月度成本预算表**：P0 验收加一条——digest + 监控 + 路由的月成本实测预估，防云上账单惊喜；
- **kill switch**：路由可一键回退（全量走默认 crew / 手动模式）、渠道可独立停用，写进运维手册。

### 9.5 KB 治理（吸取 v1 #8 环境债教训）
- 自动摄入开启后 KB 无限增长：入库去重（来源 URL 哈希 + 语义相似度）、按来源的保留策略与陈旧度标记；
- 无人值守摄入的审批策略：来源白名单内自动入库 + 每日入库清单推送（可回滚），白名单外一律待批。

### 9.6 零散但真实（实现期注意）
- cron 时区显式配置（用户本地时区）；
- IM 推送格式适配（飞书卡片 / 钉钉 markdown 子集、长度截断、bot API 限频退避）；
- lark-oapi / dingtalk-stream 依赖进 requirements 并烧进正式镜像（吸取 #6 教训：不许只热部署）；
- ops 立场：v2 监控**只读**（http_check / prometheus_query），不引入 SSH 远程执行——高危能力留给未来单独评审；
- 路由器单测沿用假 LLM 注入模式（同 judge 假注入）；plan 确认卡片的前端 UI 别漏。

---

## 十、防"技术玩具"执行纪律（2026-09-14 定稿，与前文冲突时以本节为准）

前九节是架构完备性方案；但**按架构分层顺序实施会重蹈 v1 覆辙**——机制建全了、痛点一个没解决。本节是执行纪律。

### 10.1 细条优先（thin vertical slice）

每个阶段只做一条"端到端细条"：从真实痛点到手机/服务器上的真实结果，当周交付。禁止横向铺层（先把渠道层全建完再做场景）。细条清单按本人真实痛点排序：

1. **服务器巡检告警**（第一条，最小）：`http_check` 工具 + jobs 调度 + 飞书**群自定义机器人 webhook** 推送（一行 HTTP POST，无需自建应用/SDK/长连接）。验收：连续 7 天早 8 点收到巡检结果；人为停一个服务，下一巡检周期内收到告警。
2. **KB 每日日报**：同一个 webhook。验收：连续 7 天真实点开。
3. **手机问答**：飞书自建应用 + WS 长连接双向对话，路由 v0（见 10.2）。验收：一周 ≥10 次真实提问。
4. 之后**按需解锁**：LLM 路由（触发条件：≥4 个 crew 日常在用，或 /cmd 记忆负担成为真实痛点）；plan compiler（触发条件：同一复合任务第二次出现）；钉钉/QQ（触发条件：真实想用那个 IM）；订阅自动摄入（触发条件：手动入库成为负担）。

### 10.2 复杂度按需解锁（防玩具核心规则）

- **路由 v0 = 默认 crew + 显式 `/cmd` 前缀**：零 LLM、零评测集——就是 8.6① 确定性层的单独先行。LLM 路由等 routing_logs/纠正日志证明需要后再上，上即带 8.6 全套保障。
- **plan v0 = 硬编码 pipeline**：如"巡检→汇总→入库"直接写成一条 job 链；plan compiler 等复合需求真实复现再做。
- 每解锁一层复杂度，必须在本节记录触发证据（哪条日志、哪个痛点）。没有证据不建。

### 10.3 替代品检验

每条细条上线前必答："这件事用 UptimeRobot / 直接开 ChatGPT / 手动做，差在哪？"答不上来就不做。巡检告警的已知答案：**告警带初诊**——附 agent 第一时间查到的指标/日志上下文，且与私有 KB、后续处置同处一个闭环（告警卡→追问→agent 查因→回线程）——单点工具给不了。手机问答的答案：私有 KB + 记忆随身。

### 10.4 使用数据验收（唯一的"好用"标准）

- **7 天自用保留**：上线后连续 7 天真实使用才算交付；连续 7 天没用 = 砍掉或重做，不带情绪。
- 月度回顾各细条使用次数/成本，并入 9.4 运行账本。

---

## 参考来源
- OpenClaw 文档：[架构](https://docs.openclaw.ai/concepts/architecture) · [Automations/Cron](https://docs.openclaw.ai/automation/cron-jobs) · [Automation 概览](https://docs.openclaw.ai/automation) · [Heartbeat](https://docs.openclaw.ai/gateway/heartbeat) · [Telegram 渠道](https://docs.openclaw.ai/channels/telegram)
- 飞书开放平台：[长连接接收回调](https://open.feishu.cn/document/event-subscription-guide/callback-subscription/step-1-choose-a-subscription-mode/configure-callback-request-address?lang=zh-CN) · [回调概述](https://open.feishu.cn/document/event-subscription-guide/callback-subscription/callback-overview?lang=zh-CN) · [卡片回传交互](https://open.feishu.cn/document/feishu-cards/card-callback-communication?lang=zh-CN) · [openclaw-feishu 配置指南](https://github.com/AlexAnys/openclaw-feishu)
- 钉钉开放平台：[Stream 模式介绍](https://open.dingtalk.com/document/resourcedownload/introduction-to-stream-mode) · [卡片回调 Stream 教程](https://open.dingtalk.com/document/development/intelligent-assistant-with-interactive-card-use-tutorial) · [智能交互回调](https://open.dingtalk.com/document/development/intelligent-interaction-callback) · [官方卡片示例仓库](https://github.com/open-dingtalk/dingtalk-card-examples)
- QQ：[机器人开放平台](https://bot.qq.com/) · [开放平台（支持绑定 OpenClaw）](https://q.qq.com/) · [NapCat 官网](https://napneko.github.io/) · [海豹手册-QQ 平台风险](https://docs.sealdice.com/deploy/platform-qq.html) · [封号风险分析](https://blog.csdn.net/zhangyunchou2015/article/details/147113348)
- Coze：[触发器文档](https://docs.coze.cn/guides_task)
- 行业视角：[The New Stack: OpenClaw and Hermes — agent harness 之争](https://thenewstack.io/openclaw-hermes-agent-harness/)
