"""push_message 推送工具（v2 S2）。

安全不变量 1（执行计划 §3.2）：**无目标参数**——user_id / group_id /
webhook url 一律不许成为模型可填参数。目标唯一来源是 env QQ_ALERT_TARGET，
由 egress 服务组包；本工具只收消息内容。

发送链路：push_message 工具（worker 线程）→ egress.push_from_tool →
egress.push_to_qq（主循环）→ onebot_adapter.send_qq_message（全系统唯一
QQ 出口）。eval 抑制与断连降级在 egress 层统一处理。
"""
from __future__ import annotations

import logging
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger("tools.push_message")


class PushMessageInput(BaseModel):
    """注意：无任何目标参数（安全不变量 1）。"""

    content: str = Field(
        ...,
        description=(
            "要推送的消息文本（纯文本，无 markdown）。建议一条告警控制在几百字内，"
            "超过系统上限会自动截断。"
        ),
    )


class PushMessageTool(BaseTool):
    """把消息推送到用户的告警渠道（QQ）。目标由系统配置锁定，不可指定。"""

    name: str = "push_message"
    description: str = (
        "推送工具：把一条消息发送到主人的告警渠道（QQ 私聊/群，目标由系统配置锁定）。"
        "触发时机：巡检发现目标状态变化需要告警、生成日报、或任务明确要求推送时。\n"
        "注意：参数只有消息内容 content，没有接收人/URL 等目标参数；"
        "任务输入说明'无需推送'时严禁调用本工具。"
    )
    args_schema: type[BaseModel] = PushMessageInput

    def _run(self, content: str, **kwargs: Any) -> str:
        content = (content or "").strip()
        if not content:
            return "错误：content 不能为空，本次未发送。"
        # §5.3：发送内容长度截断（1500 字）
        limit = settings.QQ_MESSAGE_MAX_LEN
        truncated = False
        if len(content) > limit:
            content = content[:limit]
            truncated = True
        from app.services import egress

        result = egress.push_from_tool(content)
        if truncated:
            result += f"\n（注意：消息超过 {limit} 字已被截断，如需完整内容请精简后重发。）"
        return result
