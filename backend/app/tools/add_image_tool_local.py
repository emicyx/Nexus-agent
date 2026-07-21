"""本地图片加载工具：读取本地图片文件并转为 Base64 data URL，供多模态 LLM 使用。"""
import base64
import logging
from typing import Any
from pathlib import Path

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger("add_image")


class AddImageToolLocalSchema(BaseModel):
    """与 CrewAI AddImageTool 的 schema 保持一致。"""
    image_url: str = Field(
        ...,
        description="The URL or path of the image to add",
    )


def _local_path_to_base64_data_url(image_url: str) -> str | None:
    """若 image_url 为本地文件路径，则读取并转为 data URL；否则返回 None。"""
    path = Path(image_url).expanduser().resolve()
    if not path.is_file():
        logger.warning("path is not a file: %s", path)
        return None
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    suffix = path.suffix.lower()
    mime = "image/jpeg"
    if suffix == ".png":
        mime = "image/png"
    elif suffix == ".gif":
        mime = "image/gif"
    elif suffix == ".webp":
        mime = "image/webp"
    elif suffix == ".bmp":
        mime = "image/bmp"
    return f"data:{mime};base64,{b64}"


class AddImageToolLocal(BaseTool):
    """将本地图片加入上下文：读取本地文件并转为 Base64 data URL 后返回。"""

    name: str = "Add image to content Local"
    description: str = (
        "加载本地图片文件并转换为 Base64 data URL 格式，供多模态模型分析。"
        "触发时机：当需要让 Agent 查看本地图片（如截图、本地存储的图片）时使用。"
        "适用边界：仅处理本地文件路径和 http(s) URL，不处理其他协议。"
    )
    args_schema: type[BaseModel] = AddImageToolLocalSchema

    def _run(
        self,
        image_url: str,
        **kwargs: Any,
    ) -> str:
        url = image_url.strip()

        # 已是 http(s) URL 则直接使用
        if url.startswith("http://") or url.startswith("https://"):
            return url

        data_url = _local_path_to_base64_data_url(url)
        if data_url is not None:
            return data_url
        return f"图片文件不存在或无法读取: {image_url}"
