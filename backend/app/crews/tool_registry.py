"""工具注册表 - tool_key → 工具类的映射

CrewFactory 根据 ToolConfig.tool_key 从此处实例化工具。
新增工具时：1) 实现工具类 2) 在 TOOL_REGISTRY 注册（填模块路径+类名）
3) 在 app.tools.__init__ 导出（保留给外部直接引用）

Week 11 性能优化：tool_registry 改为按需 lazy import，避免 backend 启动时
全量加载 playwright/python-docx/openpyxl 等重型依赖。仅在 instantiate_tool
真正被调用时才 import 对应模块。
"""
import importlib
import logging
from typing import Any, Type

from crewai.tools import BaseTool

logger = logging.getLogger("tool_registry")

# tool_key → (模块路径, 类名)
TOOL_REGISTRY: dict[str, tuple[str, str]] = {
    "baidu_search": ("app.tools.baidu_search", "BaiduSearchTool"),
    "intermediate": ("app.tools.intermediate_tool", "IntermediateTool"),
    "add_image_local": ("app.tools.add_image_tool_local", "AddImageToolLocal"),
    "fixed_directory_read": ("app.tools.fixed_directory_read_tool", "FixedDirectoryReadTool"),
    "rag_search": ("app.tools.rag_search_tool", "RagSearchTool"),
    "human_approval": ("app.tools.human_approval_tool", "HumanApprovalTool"),
    # Playwright 浏览器工具（重依赖，仅在实际用到时 import）
    "navigate": ("app.tools.playwright_tools", "NavigateTool"),
    "click_element": ("app.tools.playwright_tools", "ClickElementTool"),
    "input_text": ("app.tools.playwright_tools", "InputTextTool"),
    "get_element_text": ("app.tools.playwright_tools", "GetElementTextTool"),
    "screenshot": ("app.tools.playwright_tools", "ScreenshotTool"),
    "wait_for_element": ("app.tools.playwright_tools", "WaitForElementTool"),
    "select_option": ("app.tools.playwright_tools", "SelectOptionTool"),
    "press_key": ("app.tools.playwright_tools", "PressKeyTool"),
    "get_page_info": ("app.tools.playwright_tools", "GetPageInfoTool"),
    # 文件工具（python-docx / openpyxl 等重依赖，懒加载）
    "write_code": ("app.tools.code_writer_tool", "CodeWriterTool"),
    "write_word": ("app.tools.word_writer_tool", "WordWriterTool"),
    "write_excel": ("app.tools.excel_writer_tool", "ExcelWriterTool"),
    "write_markdown": ("app.tools.markdown_writer_tool", "MarkdownWriterTool"),
    "view_file": ("app.tools.file_viewer_tool", "FileViewerTool"),
    # 网页内容入库工具
    "fetch_url": ("app.tools.fetch_url_tool", "FetchUrlTool"),
    "kb_ingest": ("app.tools.kb_ingest_tool", "KbIngestTool"),
}

# 可选项：供前端下拉选择（只用 key 列表，不触发 import）
TOOL_OPTIONS = [{"key": k, "label": k} for k in sorted(TOOL_REGISTRY.keys())]

# 类缓存：tool_key → 已解析的类对象（避免重复 importlib）
_resolved_classes: dict[str, Type[BaseTool]] = {}


def _resolve_class(tool_key: str) -> Type[BaseTool]:
    """按需 import 模块并返回工具类，已解析的缓存。"""
    cls = _resolved_classes.get(tool_key)
    if cls is not None:
        return cls
    entry = TOOL_REGISTRY.get(tool_key)
    if entry is None:
        raise KeyError(f"tool_key '{tool_key}' 未在 TOOL_REGISTRY 注册")
    module_path, class_name = entry
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if cls is None:
        raise KeyError(f"模块 {module_path} 中未找到类 {class_name}")
    _resolved_classes[tool_key] = cls
    return cls


def instantiate_tool(tool_key: str, config_json: Any | None = None) -> BaseTool:
    """根据 tool_key 实例化工具，支持 config_json 参数化。

    rag_search: top_k (默认 5)
    baidu_search: max_results (默认 20)
    其他工具: 无参构造
    """
    cls = _resolve_class(tool_key)
    cfg = config_json or {}

    if tool_key == "rag_search":
        top_k = cfg.get("top_k", 5)
        return cls(top_k_default=top_k)
    elif tool_key == "baidu_search":
        max_results = cfg.get("max_results", 20)
        return cls(max_results=max_results)
    else:
        return cls()
