# 自定义 CrewAI Tool 开发指南

> Project Nexus — 如何为 CrewAI 开发自定义工具
> 版本 2.0 | 2026-08-21（对齐 HEAD=5ac4c4f：修正工具键名/超时/参数表单位置/注册表懒加载）

---

## 一、概述

Nexus 平台的工具体系基于 CrewAI 的 `BaseTool` 抽象。每个工具通过 `tool_registry` 注册后在配置中心挂载到任意 Agent。

**工具开发核心流程**：
1. 继承 `BaseTool`，定义 name/description/args_schema
2. 实现 `_run()` 方法（同步）
3. 在 `tool_registry.py` 的 `TOOL_REGISTRY` 注册 `(模块路径, 类名)` 元组
4. 在 `app/tools/__init__.py` 导出（供外部直接引用）
5. 在 `db/seed.py` 添加种子 ToolConfig（可选）
6. 前端 `components/config/tool-form.tsx` 的 `TOOL_PARAM_SCHEMA` 添加参数化表单（可选）

---

## 二、最小示例

```python
# backend/app/tools/my_tool.py
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class MyToolInput(BaseModel):
    """工具参数 schema"""
    query: str = Field(..., description="查询内容")


class MyTool(BaseTool):
    name: str = "my_tool"
    description: str = "一个自定义工具，执行特定操作"
    args_schema: type[BaseModel] = MyToolInput

    def _run(self, query: str) -> str:
        """同步执行工具逻辑，返回字符串结果。"""
        result = do_something(query)
        return f"结果: {result}"
```

---

## 三、注册到 ToolRegistry（懒加载）

注册表存 `(模块路径, 类名)` 元组，`instantiate_tool()` 被调用时才真正 import——避免启动时全量加载 playwright / python-docx / openpyxl 等重依赖：

```python
# backend/app/crews/tool_registry.py
TOOL_REGISTRY: dict[str, tuple[str, str]] = {
    "my_tool": ("app.tools.my_tool", "MyTool"),
    ...
}
```

`_resolve_class()` 会缓存已解析的类，同一 tool_key 只 import 一次。

---

## 四、参数化工具（config_json）

ToolConfig 表的 `config_json` 字段存储工具的持久化配置，`instantiate_tool()` 消费：

```python
def instantiate_tool(tool_key: str, config_json: Any | None = None) -> BaseTool:
    cls = _resolve_class(tool_key)
    cfg = config_json or {}

    if tool_key == "rag_search":
        top_k = cfg.get("top_k", 10)
        return cls(top_k_default=top_k)
    elif tool_key == "baidu_search":
        max_results = cfg.get("max_results", 20)
        return cls(max_results=max_results)
    else:
        return cls()  # 无参工具
```

工具类用 Pydantic model field 接收参数：

```python
class RagSearchTool(BaseTool):
    name: str = "rag_search"
    top_k_default: int = 10  # ← Pydantic field，通过 cls(top_k_default=3) 传入

    def _run(self, query: str, top_k: int = _SCHEMA_TOP_K_DEFAULT) -> str:
        # schema 默认值 = LLM 未显式传参（Pydantic 自动填充）→ 用配置的默认值
        # （2026-08-26 修复：此前签名默认值直接使用，config_json 的 top_k 永远不生效）
        if top_k == _SCHEMA_TOP_K_DEFAULT:
            top_k = self.top_k_default
        ...
```

当前仅 `rag_search`（top_k，默认 10）和 `baidu_search`（max_results，默认 20）支持参数化，其余为无参构造。

---

## 五、核心约束：同步 DB / Redis + 事件循环 offload + 文件沙箱

### 文件读写必须走沙箱辅助函数（P0-1）

所有文件读写必须经 `_file_utils.py` 的 `resolve_output_path` / `resolve_read_path`：
写仅允许 `{SANDBOX_DATA_DIR}/outputs/**`，读限 `SANDBOX_DATA_DIR/**`（+ `SANDBOX_EXTRA_READ_DIRS` 白名单）。
越界抛 `SandboxViolation(PermissionError)` —— 工具 `_run` 里捕获并返回错误字符串，不要让异常抛出。

### 工具必须写成同步的

CrewAI 的执行路径在工具侧只认同步 `_run`（原生路径加 `_arun` 无效且会因 `BaseTool.run` 内 `asyncio.run` 崩溃）。工具内部访问 DB/Redis 用**同步客户端**：

```python
# ✅ 正确：同步 engine（psycopg2）
from sqlalchemy import create_engine, text
from app.config import settings

_sync_engine = create_engine(
    settings.POSTGRES_DSN.replace("postgresql://", "postgresql+psycopg2://")
)

def _search_sync(query_vec):
    with _sync_engine.connect() as conn:
        return conn.execute(text("SELECT ..."), {"vec": query_vec}).fetchall()
```

```python
# ✅ 正确：同步 Redis
from app.db.redis import get_sync_redis
r = get_sync_redis()
```

### 阻塞由 crewai_async_patch 兜底

CrewAI 1.9.3 会在事件循环内同步内联调用工具。`crews/crewai_async_patch.py` 把 `_handle_native_tool_calls` 整体丢进 worker 线程，同步工具阻塞（HITL 忙等、Playwright 渲染）不再冻结 FastAPI 事件循环。

**注意**：patch 带版本守卫，仅 crewai==1.9.3 且源码 sentinel 匹配时生效，否则**跳过并告警**。升级 crewai 后必须验证后端日志无 "patch skipped" 告警，否则所有阻塞工具会重新冻结事件循环。

---

## 六、事件包装（SSE 推送工具调用）

`factory.py` 通过 `wrap_tool_with_events()` 包装每个工具，自动推送 `tool_call` / `tool_result` SSE 事件（输入/输出截断，避免 SSE 消息过大），前端步骤流自动渲染工具卡片。新工具无需做任何额外工作即可获得事件。

---

## 七、HITL 工具特殊处理

`HumanApprovalTool` 额外注入事件队列以推送 `approval_requested` 事件（factory.py 自动 `bind_event_emitter`）。工具内部用同步 Redis 轮询审批状态，**超时 60s（DEFAULT_TIMEOUT）自动写 TIMEOUT 并拒绝**；Redis key 另有 TTL 兜底防残留。

---

## 八、前端参数化表单

`TOOL_PARAM_SCHEMA` 位于 **`frontend/src/components/config/tool-form.tsx`**（注意：不在 api-client.ts）：

```typescript
const TOOL_PARAM_SCHEMA: Record<
  string,
  { key: string; label: string; default: number; min: number; max: number }
> = {
  rag_search: { key: "top_k", label: "检索结果数量", default: 10, min: 1, max: 50 },
  baidu_search: { key: "max_results", label: "最大搜索结果数", default: 20, min: 1, max: 50 },
  // my_tool: { key: "my_param", label: "参数说明", default: 1, min: 0, max: 10 },
};
```

ToolForm 按 tool_key 自动渲染对应数字输入框；无条目的工具显示"无参数化配置项"。

---

## 九、已有工具一览（22 个，键名以 TOOL_REGISTRY 为准）

| 类别 | tool_key | 工具类 | 说明 |
|---|---|---|---|
| 搜索 | `baidu_search` | BaiduSearchTool | 百度搜索（max_results 默认 20） |
| 中间产物 | `intermediate` | IntermediateTool | 中间思考产物记录 |
| 多模态 | `add_image_local` | AddImageToolLocal | 本地图片注入（qwen3-vl-plus） |
| 文件读取 | `fixed_directory_read` | FixedDirectoryReadTool | 目录文件浏览 |
| RAG | `rag_search` | RagSearchTool | 混合检索（向量+关键词 RRF，top_k 默认 10，结果带 document_id 支持二段检索） |
| HITL | `human_approval` | HumanApprovalTool | 人类审批（60s 超时） |
| 浏览器 | `navigate` | NavigateTool | 打开 URL |
| 浏览器 | `click_element` | ClickElementTool | 点击元素 |
| 浏览器 | `input_text` | InputTextTool | 输入文本 |
| 浏览器 | `get_element_text` | GetElementTextTool | 读取元素文本 |
| 浏览器 | `screenshot` | ScreenshotTool | 截图 |
| 浏览器 | `wait_for_element` | WaitForElementTool | 等待元素 |
| 浏览器 | `select_option` | SelectOptionTool | 下拉选择 |
| 浏览器 | `press_key` | PressKeyTool | 按键 |
| 浏览器 | `get_page_info` | GetPageInfoTool | 页面信息 |
| 文件写出 | `write_code` | CodeWriterTool | 写代码文件 |
| 文件写出 | `write_word` | WordWriterTool | 写 Word |
| 文件写出 | `write_excel` | ExcelWriterTool | 写 Excel |
| 文件写出 | `write_markdown` | MarkdownWriterTool | 写 Markdown |
| 文件读取 | `view_file` | FileViewerTool | 查看文件内容 |
| 网页抓取 | `fetch_url` | FetchUrlTool | URL→markdown（失败降级 Playwright） |
| 知识库 | `kb_ingest` | KbIngestTool | 内容切块入库 |

> 注意键名：搜索是 `baidu_search`（非 search_web），中间产物是 `intermediate`（非 intermediate_save）。`skill_loader` / `load_skill` 存在源文件但**未注册**进 TOOL_REGISTRY。
> 截图目录：2026-08 P0 加固后从 `/app/screenshots` 移到文件沙箱内 `{SANDBOX_DATA_DIR}/screenshots`，filename 由工具消毒（去路径分隔符）。

---

## 十、调试技巧

1. **本地导入验证**：`PYTHONPATH=backend python -c "from app.tools.my_tool import MyTool; print(MyTool())"`
2. **TOOL_REGISTRY 验证**：`PYTHONPATH=backend python -c "from app.crews.tool_registry import instantiate_tool; print(instantiate_tool('my_tool'))"`
3. **Docker 验证**：`make rebuild` 后在 `/config` 页面挂载工具到 Agent，在 `/chat` 测试调用
4. **SSE 日志**：后端日志打印每个 `tool_call` / `tool_result` 事件
5. **patch 生效检查**：启动日志确认 crewai_async_patch 已应用（无 "skipped" 告警）
