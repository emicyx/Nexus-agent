# Nexus v2 QQ 渠道接入与渠道抽象（S1 + S4 阶段 A）

> QQ 是 v2 的主 IM 渠道：用户在 QQ 私聊里直接跟助手对话。本文覆盖接入拓扑、入站安全设计、会话与路由，以及 2026-09-17 完成的渠道无关管线抽象。更新至 2026-09-17。

## 一、接入拓扑（NapCat + OneBot 11）

- **NapCat** 是 QQ 协议端实现，作为 docker compose 第 5 个容器部署，与 backend 同 compose 内网；QQ 数据目录挂 volume 持久化登录态（配 ACCOUNT 快速登录，重启免扫码）。
- 通道是 **OneBot 11 反向 WebSocket**：NapCat 主动连接 backend 的 `/v1/channels/onebot/ws?token=...`（内网明文即可）。adapter 与部署位置无关——本机/云端同一代码路径，只是 URL 不同。
- 协议形态：NapCat 向上推事件（`post_type=="message"`，含 message_type/raw_message/user_id/sender 等）；Nexus 向下发 API 调用（`send_private_msg` / `send_group_msg`，带 echo 匹配，同连接收响应判成败）。
- **单连接模型**：新连接顶替旧连接（旧连接优雅关闭）——NapCat 断线重连时旧 TCP 可能半开，顶替保证状态永远指向最新活跃连接。NapCat 自带断线自动重连。
- **单实例铁律**：同一 QQ 账号只能跑一个 NapCat（双实例 = 互踢 + 高封号风险）。移动端 QQ 不受影响（NapCat 占用的是 PC 协议位）。

## 二、入站安全设计（安全不变量）

1. **连接鉴权**：WS 连接必须携带 token（env ONEBOT_WS_TOKEN），校验失败立即关闭；未配置 token = 拒绝一切连接（fail-closed）。
2. **owner 白名单**（env QQ_OWNER_IDS）：名单外消息静默不回（不给攻击者确认 bot 存在的信号），只记 WARNING 日志。
3. **不可信包裹**：入站正文进入 agent 上下文前以 `<im_content>` 标签包裹（照抄知识库内容 kb_content 的注入防护纪律）——IM 消息里的"忽略之前的指令"类文本只是消息内容本身，不得改变 agent 角色设定。
4. **回复走事件来源**（reply-to-source）：私聊回私聊、群聊回群——对话回复不引入任何新目标面；主动推送目标则只来自 env QQ_ALERT_TARGET，模型不可填。

## 三、会话映射与路由

- **会话键**：私聊 `qq:{用户QQ号}`（前端会话列表据此显示来源徽标）；命令路由（/kb 等）挂子会话 `qq:{QQ号}:/cmd`——因 ChatSession 与 crew 硬绑定，子会话避免不同链路的短期记忆互相污染。群聊不做会话化（@ 即答，无 STM 上下文）。
- **路由复用**：与 Web 端 Auto 模式同一个纯函数 route_v0——`/kb` `/write` `/ingest` 前缀路由到对应 crew（前缀剥离），无前缀走默认助手；未知命令返回可读错误。命令路由的回复带 `[已转交 {名称}]` 前缀，默认路由不标注（保持对话自然）。
- **HITL 定案 A**：IM 侧仅开放默认问答 + /kb（均不触发审批）；/write、/ingest 等会触发审批 hook 的命令固定回复引导到 Web 端使用（IM 内审批卡片属按需解锁组件）。
- **并发纪律**：同一发送者串行（处理中再来消息 FIFO 排队，上限 5，超限回"正在处理中"）；跨渠道天然不互锁；全局并发上限与 Web 端共享（超限回"服务繁忙"）。

## 四、im_pipeline 渠道无关抽象（S4 阶段 A，2026-09-17 交付）

为支持第二 IM 渠道接入而做的纯重构（行为不变、全量回归全绿）：

- **归一化结构 InboundMessage**：`{channel, sender_id, text, is_group, owners, reply}`——适配器把原生渠道事件解析成它；`reply` 是回复闭包，把渠道发送细节封闭在适配器内。
- **管线主入口 handle_inbound**（渠道无关）：owner 白名单 → 不可信包裹 → 路由 → HITL 检查 → 全局并发检查 → 发送者级串行排队（锁键 `{channel}:{sender_id}`）→ run_crew_chat 执行 → 回复。
- **渠道差异封闭清单**（留在各适配器内，验收审查对象）：传输层（连接/鉴权/重连/接收循环）、原生事件解析（群 @ 判定与剥离、群/单聊判定、字段容错）、owner 环境变量解析、回复发送 API、在线状态。
- **渠道注册表**（channels/registry.py）：渠道模块 import 时自注册；渠道状态 API、/metrics 指标、前端状态 chip 全部遍历注册表——第二渠道接入时消费方零改动。
- **抽象守卫单测**：扫描 im_pipeline 源码，断言零渠道特有引用（渠道名、渠道字段名、渠道发送函数）——防止渠道特例回流管线，抽象边界由测试长期锁住。
- 会话键与 run_id 前缀都从 `channel` 字段派生（如 `qq:{id}`、`qq-N`），跨渠道天然不冲突。

## 五、第二渠道（飞书）评估结论

2026-09-17 开工门裁定：不开工。开工门问题是"会真的每天在飞书里跟它说话吗"——没有肯定答案就不做（防玩具纪律，一周 ≥10 次真实提问的验收必不过）。解锁条件 = 真实自用场景出现；届时按存档设计实施（lark-oapi SDK WebSocket 长连接订阅消息事件，回复走 OpenAPI 需 access_token 管理——与钉钉 sessionWebhook 免 token 的关键差异），核心链路零改动。
