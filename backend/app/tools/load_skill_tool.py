"""Skill 渐进式披露工具（Week 9）

Agent 挂载 >=5 skills 时，backstory 只注入摘要，
Agent 通过本工具按需加载完整 prompt_template。
"""
from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class LoadSkillInput(BaseModel):
    skill_name: str = Field(
        ...,
        description="要加载的技能名称（与 backstory 摘要中列出的名称一致）",
    )


class LoadSkillTool(BaseTool):
    """按需加载技能完整指令模板。

    构造时传入 skills_map={skill_name: prompt_template}，
    Agent 调用时返回对应技能的完整 prompt。
    """
    name: str = "load_skill"
    description: str = (
        "加载指定技能的完整指令模板。"
        "当任务涉及 backstory 中列出的某个技能时，"
        "先用本工具获取该技能的详细指导，再按指导执行任务。"
    )
    args_schema: Type[BaseModel] = LoadSkillInput
    skills_map: dict[str, str] = Field(default_factory=dict)

    def _run(self, skill_name: str, **kwargs: Any) -> str:
        template = self.skills_map.get(skill_name)
        if template is None:
            available = ", ".join(self.skills_map.keys()) or "(无)"
            return f"未找到技能 '{skill_name}'。可用技能: {available}"
        return f"[技能: {skill_name}]\n{template}"
