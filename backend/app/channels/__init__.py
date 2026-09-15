"""IM 渠道 adapter 层（v2 S1+）。

渠道差异（身份字段/消息格式/回复 API）全部封闭在各 adapter 内，
核心链路（白名单 → 会话映射 → 路由 v0 → run_crew_chat → 回复）零渠道特有分支。
"""
