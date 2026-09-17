# Nexus v2 定时任务与推送（S2）

> S2 把"常驻助理"补齐：定时/事件触发的任务自动跑 crew、产物按策略推 QQ。先做两个真实任务——服务器巡检告警与 KB 每日日报。技术验收 2026-09-16 全绿。更新至 2026-09-17。

## 一、调度器

- APScheduler（AsyncIOScheduler，memory jobstore），随 FastAPI lifespan 启动；任务配置存 DB（jobs 表），CRUD 后同步增删调度。
- 参数纪律：每 job 并发 = 1（不重叠）、coalesce 合并错过的触发、misfire 宽限 300 秒、显式时区（默认 Asia/Shanghai）。
- **三个默认任务**：
  1. 每 30 分钟巡检（interval）——输出策略 `state_change`：与上一次成功运行的状态快照对比，任何目标 up→down 或 down→up 才推送（天然节制，无噪音）；
  2. 每日 8:00 巡检日报——全部监控目标的一行状态；
  3. 每日 21:00 KB 增量日报——当日新入库文档的摘要（新增了什么、每条两三句、值得关注的一条），推送策略 always。

## 二、执行链（复用引擎，不另造执行器）

- `run_crew_job` 复用与对话相同的 crew 装配与 kickoff：事件收进本地队列落 job_runs 表（截断存储）；无会话上下文（job 一次性）。
- **巡检 crew（ops_patrol）**：单 agent，挂 http_check 与 push_message 两个工具。输入是结构化巡检结果，产出 = 一行状态 + **异常初诊**（哪个目标、从何时起、当时延迟/状态码、可能原因、建议动作）——"告警带初诊"是它与 UptimeRobot 类裸状态翻转通知的本质差异（替代品检验的答案）。
- **日报 crew（daily_digest）**：输入模板注入 `{{kb_delta}}`——按文档 created_at 增量取当日新文档 + 各自头部摘要，来源分组，以 SQL 为准。

## 三、安全设计（目标锁定与零外发）

1. **http_check 目标只来自环境变量 MONITOR_TARGETS**（JSON 数组：name/url/expect_status/timeout_s），工具参数只有可选的名称过滤、**无自由 URL 参数**——不打开 SSRF 面，不为巡检放松全局网络安全规则。
2. **push_message 工具无目标参数**（内容截断 1500 字）；推送目标只来自 env QQ_ALERT_TARGET，模型不可填任何目标（user_id/group_id/webhook 一律不可成为模型参数）。
3. **全系统唯一 QQ 出口**：对话回复与 job 推送都经同一个发送函数；推送前查渠道连接，断连时如实落账 `pushed_to=failed(channel_offline)` 并重试一次，不静默丢。
4. **eval 零外发（硬红线）**：评测/dry-run 触发的 job 状态记 eval，推送直接跳过并落账 `pushed_to=suppressed(eval)`；评测 job 适配器对零外发做硬断言——agent 真实调用推送工具也会被抑制。
5. **job 约束**：连续失败（默认 5 次）自动停用 job 并推送一条告警后静默；每 job 单 token 上限可配。
6. 落账字段：job_runs 记录状态、起止时间、token 消耗（全局指标差值法，注明近似）、结果快照、推送去向（qq / suppressed(eval) / failed(原因)）——排障与验收都以这张表为准。

## 四、钉钉告警备推（S3'，代码完成、按纪律休眠）

- 形态：用户自建钉钉群（只含自己）+ 群自定义机器人 webhook 加签推送（HMAC-SHA256，官方 demo 同款），零新依赖。
- 三态模式（env）：`backup`（缺省，仅 QQ 主通道 failed(...) 时兜底）/ `always`（双发）/ `off`。eval 零外发对 QQ 与钉钉两个出口同时成立（集成测试断言双出口零调用）。
- 现状：webhook 未配置 = 备推关闭 = 零行为差异；启用成本约 2 分钟（建群加机器人 → 填 env → 重建容器）。
- 搁置与解锁：用户裁定"做了也没意义，暂时搁置"（2026-09-17）；解锁条件 = QQ 渠道再发真实失联/风控事件造成告警丢失，或用户重启意愿。动机与改道过程详见《Nexus v2 开发纪律与运维实录》。

## 五、实测验证记录（节选）

- down→up 告警演练：人为停掉一个监控目标，下一巡检周期内收到 down 告警（带初诊与全局对照），恢复后收到 up 通知——两轮均调度自动触发、无人值守。
- 断连降级：渠道离线期间 job_runs 如实记录 failed(channel_offline)，恢复后自动重连续推，无静默丢失。
- 每日 21:00 KB 日报准点到达；8:00 巡检日报依赖宿主机开机（本机形态的已知边界，云端迁移后解除）。
