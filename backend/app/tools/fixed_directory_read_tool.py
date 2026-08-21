"""目录读取工具：递归列出指定目录下的所有文件路径。

修复了原 CrewAI DirectoryReadTool 中当 directory 为 "." 时，
文件名中的点号被错误替换的问题。
"""
import os
from typing import Any
from pathlib import Path

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.tools._file_utils import SandboxViolation, ensure_dir_within


class FixedDirectoryReadToolSchema(BaseModel):
    """Input for FixedDirectoryReadTool."""


class DirectoryReadToolSchema(FixedDirectoryReadToolSchema):
    """Input for FixedDirectoryReadTool."""

    directory: str = Field(
        ...,
        description="Directory to list content（沙箱限制：仅 /app/data/ 内目录，如 'outputs/'）",
    )


class FixedDirectoryReadTool(BaseTool):
    """修复版本的目录读取工具，正确处理当前目录（"."）的路径。"""
    name: str = "List files in directory"
    description: str = (
        "递归列出指定目录下的所有文件路径。"
        "触发时机：当需要了解某个目录包含哪些文件时使用，例如查看项目结构、确认文件是否存在。"
        "适用边界：仅列出文件路径，不读取文件内容。"
    )
    args_schema: type[BaseModel] = DirectoryReadToolSchema
    directory: str | None = None

    def __init__(self, directory: str | None = None, **kwargs):
        super().__init__(**kwargs)
        if directory is not None:
            self.directory = directory
            self.description = f"A tool that can be used to list {directory}'s content."
            self.args_schema = FixedDirectoryReadToolSchema
            self._generate_description()

    def _run(
        self,
        **kwargs: Any,
    ) -> Any:
        directory: str | None = kwargs.get("directory", self.directory)
        if directory is None:
            raise ValueError("Directory must be provided.")

        # 沙箱校验：目录必须限制在读取白名单（/app/data/ 等）内
        try:
            safe_dir = ensure_dir_within(directory)
        except SandboxViolation as e:
            return f"拒绝访问：{e}"

        directory = str(safe_dir)

        # 规范化目录路径
        directory = os.path.normpath(directory)
        if directory.endswith("/"):
            directory = directory[:-1]

        abs_directory = os.path.abspath(directory)

        files_list = []
        for root, dirs, files in os.walk(directory):
            for filename in files:
                full_path = os.path.join(root, filename)
                abs_full_path = os.path.abspath(full_path)
                rel_path = os.path.relpath(abs_full_path, abs_directory)

                if directory != "." and directory != os.path.curdir:
                    file_path = os.path.join(directory, rel_path).replace(os.path.sep, "/")
                else:
                    file_path = rel_path.replace(os.path.sep, "/")

                files_list.append(file_path)

        files = "\n- ".join(files_list)
        return f"File paths: \n- {files}"
