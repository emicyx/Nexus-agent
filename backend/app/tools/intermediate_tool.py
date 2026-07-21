"""中间结果保存工具：在 Agent 执行过程中保存中间思考产物，支持慢思考模式。

支持任意类型输入（字符串、列表、字典等），自动转换为字符串格式。
"""
import json
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator


class IntermediateToolSchema(BaseModel):
    """Input for IntermediateTool."""

    intermediate_product: str = Field(
        ...,
        description=(
            "中间思考产物，需要保存的内容（字符串）。"
            "例如：'关键要点1：xxx；关键要点2：xxx'"
        ),
    )

    @field_validator('intermediate_product', mode='before')
    @classmethod
    def convert_to_string(cls, v: Any) -> str:
        """将任意类型的输入转换为字符串"""
        if isinstance(v, str):
            return v
        elif isinstance(v, list):
            return "\n".join(str(item) for item in v)
        elif isinstance(v, dict):
            try:
                return json.dumps(v, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                return str(v)
        else:
            return str(v)


class IntermediateTool(BaseTool):
    """中间结果保存工具，用于 Agent 分步骤思考时记录中间产物。"""
    name: str = "Save_Intermediate_Product_Tool"
    description: str = (
        "保存中间思考产物，用于在 Agent 执行过程中记录分步骤的思考要点。"
        "触发时机：当需要分步骤思考、记录关键要点、或保存结构化中间数据时使用。"
        "适用边界：仅用于保存思考产物辅助推理，不产生外部副作用。"
    )
    args_schema: type[BaseModel] = IntermediateToolSchema

    def _run(
        self,
        intermediate_product: str = "",
        **kwargs: Any,
    ) -> str:
        """保存中间思考产物，返回固定提示字符串。"""
        return "中间结果已保存， 可以进行下一步Thought"
