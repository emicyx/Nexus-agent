"""代码编写工具：将代码内容写入文件。"""
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.tools._file_utils import resolve_output_path


class CodeWriterInput(BaseModel):
    filename: str = Field(
        ...,
        description="文件名（如 hello.py, main.js, app.go）",
    )
    content: str = Field(
        ...,
        description="代码内容",
    )
    sub_dir: str = Field(
        "",
        description="可选子目录（如 src/, utils/），不填则写到 outputs 根目录",
    )


class CodeWriterTool(BaseTool):
    """将代码写入文件，支持各种编程语言。"""
    name: str = "write_code_file"
    description: str = (
        "将代码写入文件。支持 .py/.js/.ts/.java/.go/.rs/.sh/.sql/.html/.css 等格式。"
        "触发时机：需要生成代码文件、脚本、配置文件时使用。"
    )
    args_schema: type[BaseModel] = CodeWriterInput

    def _run(
        self,
        filename: str = "",
        content: str = "",
        sub_dir: str = "",
        **kwargs: Any,
    ) -> str:
        if not filename:
            return "错误：filename 不能为空"
        path = resolve_output_path(filename, sub_dir)
        path.write_text(content, encoding="utf-8")
        return f"代码已写入: {path}"
