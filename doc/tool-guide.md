# 自定义 CrewAI Tool 开发指南

> Project Nexus — 如何为 CrewAI 开发自定义工具
> 版本 1.0 | 2026-07-12

---

## 一、概述

Nexus 平台的工具体系基于 CrewAI 的 `BaseTool` 抽象。每个工具是一个 Pydantic model 子类，通过 `tool_registry` 注册后可在前端配置中心挂载到任意 Agent。

**工具开发核心流程**：
1. 继承 `BaseTool`，定义 name/description/args_schema
2. 实现 `_run()` 方法（同步）
3. 在 `tool_registry.py` 注册 tool_key
4. 在 `seed.py` 添加种子 ToolConfig（可选）
5. 前端 `TOOL_PARAM_SCHEMA` 添加参数化表单（可选）

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

## 三、注册到 ToolRegistry

```python
# backend/app/crews/tool_registry.py
from app.tools.my_tool import MyTool

TOOL_REGISTRY: dict[str, type[BaseTool]] = {
    "my_tool": MyTool,
    # ... 其他工具
}

def instantiate_tool(tool_key: str, config_json: dict | None = None) -> BaseTool:
    cls = TOOL_REGISTRY.get(tool_key)
    if cls is None:
        raise KeyError(f"未注册工具: {tool_key}")
    # 无参工具直接实例化
    return cls()
```

---

## 四、参数化工具（config_json）

ToolConfig 表的 `config_json` 字段存储工具的持久化配置。`instantiate_tool()` 会消费它：

```python
def instantiate_tool(tool_key: str, config_json: dict | None = None) -> BaseTool:
    cls = TOOL_REGISTRY.get(tool_key)
    if tool_key == "rag_search":
        top_k = (config_json or {}).get("top_k", 5)
        return cls(top_k_default=top_k)
    elif tool_key == "search_web":
        max_results = (config_json or {}).get("max_results", 5)
        return cls(max_results=max_results)
    else:
        return cls()  # 无参工具
```

工具类用 Pydantic model field 接收参数：

```python
class RagSearchTool(BaseTool):
    name: str = "rag_search"
    top_k_default: int = 5  # ← Pydantic field，通过 cls(top_k_default=3) 传入

    def _run(self, query: str, top_k: int = 20) -> str:
        if top_k == 20:  # 未显式指定时用默认值
            top_k = self.top_k_default
        ...
```

---

## 五、核心踩坑：同步 DB / Redis

### 问题
CrewAI `akickoff()` 在主事件循环中调用工具 `_run()`。此时 `asyncio.run()` 会报错：
```
"Cannot run the event loop while another loop is running"
```

### 解决方案
工具内部访问 DB 必须用**同步 SQLAlchemy engine**（psycopg2），不能用 AsyncSessionLocal。

```python
# ✅ 正确：同步 engine
from sqlalchemy import create_engine, text
from app.config import settings

_sync_engine = create_engine(
    settings.POSTGRES_DSN.replace("postgresql://", "postgresql+psycopg2://")
)

def _search_sync(query_vec):
    with _sync_engine.connect() as conn:
        result = conn.execute(text("SELECT ..."), {"vec": query_vec})
        return result.fetchall()
```

```python
# ❌ 错误：async session 会报错
async def _search(query_vec):
    async with AsyncSessionLocal() as session:
        ...  # RuntimeError: Cannot run the event loop
```

同理，访问 Redis 必须用**同步 redis.Redis**（非 redis.asyncio）：

```python
from app.db.redis import get_sync_redis

r = get_sync_redis()
r.set("key", "value")
```

---

## 六、事件包装（SSE 推送工具调用）

通过 `wrap_tool_with_events()` 包装工具，自动推送 `tool_call` / `tool_result` SSE 事件：

```python
from app.crews.tool_events import wrap_tool_with_events

# 在 factory.py 中
tools.append(
    wrap_tool_with_events(base_tool, queue, loop, agent_role=acfg.role)
)
```

这会在工具执行前后推送事件，前端步骤流自动渲染工具调用卡片。

---

## 七、HITL 工具特殊处理

`HumanApprovalTool` 需要额外注入事件队列以推送 `approval_requested` 事件：

```python
# 在 factory.py 中
if isinstance(base_tool, HumanApprovalTool):
    base_tool.bind_event_emitter(queue, loop, agent_role=acfg.role)
```

工具内部用同步 Redis 轮询审批状态（每 1s），超时 150s 自动拒绝。

---

## 八、前端参数化表单

在 `frontend/src/lib/api-client.ts` 的 `TOOL_PARAM_SCHEMA` 中添加参数定义：

```typescript
const TOOL_PARAM_SCHEMA: Record<string, { field: string; label: string; type: "number" }[]> = {
  rag_search: [{ field: "top_k", label: "检索结果数量", type: "number" }],
  search_web: [{ field: "max_results", label: "最大搜索结果数", type: "number" }],
  // my_tool: [{ field: "my_param", label: "参数说明", type: "number" }],
};
```

前端 ToolForm 会根据 tool_key 自动渲染对应参数输入框。

---

## 九、已有工具一览

| tool_key | 工具类 | 说明 | 参数化 |
|---|---|---|---|
| `search_web` | BaiduSearchTool | 百度搜索 | max_results |
| `intermediate_save` | IntermediateTool | 中间结果保存 | 无 |
| `rag_search` | RagSearchTool | pgvector 知识库检索 | top_k |
| `human_approval` | HumanApprovalTool | HITL 人类审批 | 无 |
| `skill_loader` | SkillLoaderTool | 技能文件加载 | 无 |

---

## 十、调试技巧

1. **本地导入验证**：`PYTHONPATH=backend python -c "from app.tools.my_tool import MyTool; print(MyTool())"`
2. **TOOL_REGISTRY 验证**：检查 `instantiate_tool("my_tool")` 返回正确实例
3. **Docker 验证**：`make rebuild` 后在 `/config` 页面挂载工具到 Agent，在 `/chat` 测试调用
4. **SSE 日志**：后端日志会打印每个 `tool_call` / `tool_result` 事件
