"""Markdown 编写工具：将 Markdown 内容写入 .md 文件。"""
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.tools._file_utils import resolve_output_path


class MarkdownWriterInput(BaseModel):
    filename: str = Field(
        ...,
        description="文件名（如 notes.md, README.md）",
    )
    content: str = Field(
        ...,
        description="Markdown 内容",
    )
    sub_dir: str = Field(
        "",
        description="可选子目录，不填则写到 outputs 根目录",
    )


class MarkdownWriterTool(BaseTool):
    """将 Markdown 内容写入 .md 文件。"""
    name: str = "write_markdown_file"
    description: str = (
        "将 Markdown 内容写入 .md 文件。支持标题、列表、表格、代码块等 Markdown 语法。"
        "触发时机：需要生成文档、笔记、README、技术说明时使用。"
    )
    args_schema: type[BaseModel] = MarkdownWriterInput

    def _run(
        self,
        filename: str = "",
        content: str = "",
        sub_dir: str = "",
        **kwargs: Any,
    ) -> str:
        if not filename:
            return "错误：filename 不能为空"
        if not filename.endswith(".md"):
            filename += ".md"

        path = resolve_output_path(filename, sub_dir)
        path.write_text(content, encoding="utf-8")
        return f"Markdown 文件已写入: {path}"
