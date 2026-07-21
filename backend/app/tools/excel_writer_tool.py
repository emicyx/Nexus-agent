"""Excel 编写工具：生成 .xlsx 文件。"""
from typing import Any

from crewai.tools import BaseTool
from openpyxl import Workbook
from pydantic import BaseModel, Field

from app.tools._file_utils import resolve_output_path


class ExcelWriterInput(BaseModel):
    filename: str = Field(
        ...,
        description="文件名（如 data.xlsx）",
    )
    data: list[list] = Field(
        ...,
        description="二维数组，每行是一个列表。第一行通常作为表头。"
        "例如：[['姓名','年龄'],['张三',25],['李四',30]]",
    )
    sheet_name: str = Field(
        "Sheet1",
        description="工作表名称",
    )
    sub_dir: str = Field(
        "",
        description="可选子目录，不填则写到 outputs 根目录",
    )


class ExcelWriterTool(BaseTool):
    """生成 Excel .xlsx 文件。"""
    name: str = "write_excel_file"
    description: str = (
        "生成 Excel .xlsx 文件。输入二维数组数据，每行写入一行。"
        "触发时机：需要生成表格数据、报表、数据清单时使用。"
    )
    args_schema: type[BaseModel] = ExcelWriterInput

    def _run(
        self,
        filename: str = "",
        data: list | None = None,
        sheet_name: str = "Sheet1",
        sub_dir: str = "",
        **kwargs: Any,
    ) -> str:
        if not filename:
            return "错误：filename 不能为空"
        if not data:
            return "错误：data 不能为空"
        if not filename.endswith(".xlsx"):
            filename += ".xlsx"

        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name
        for row in data:
            ws.append(row)

        path = resolve_output_path(filename, sub_dir)
        wb.save(str(path))
        return f"Excel 文件已生成: {path}（{len(data)} 行数据）"
