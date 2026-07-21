"""Tools 模块 - CrewAI 自定义工具集

Week 11 性能优化：__init__ 不再顶层 import 子模块，避免加载任一工具时
连带触发 playwright / python-docx / openpyxl 等重型依赖。
直接从子模块导入即可，例如：
    from app.tools.human_approval_tool import HumanApprovalTool
    from app.tools.rag_search_tool import RagSearchTool
"""
