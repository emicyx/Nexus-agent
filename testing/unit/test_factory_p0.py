"""factory P0 修复回归测试（P0-5）。

1) delegation pydantic map 改 ContextVar 后，并发请求（各自 context）互不污染；
2) history 兜底初始化：session 查不到时不再 NameError。
"""
import contextvars

from app.crews.factory import _delegation_pydantic_map_ctx


def test_delegation_map_defaults_to_none():
    """未设置（非 hierarchical 请求 / 上下文丢失）时优雅降级为不注入。"""
    assert _delegation_pydantic_map_ctx.get() is None


def test_delegation_map_two_contexts_isolated():
    """两个并发请求各自 copy_context 设置各自的表，互不可见、外层不受影响。"""
    map_a = {"role-a": int}
    map_b = {"role-b": str}

    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()
    ctx_a.run(lambda: _delegation_pydantic_map_ctx.set(map_a))
    ctx_b.run(lambda: _delegation_pydantic_map_ctx.set(map_b))

    assert ctx_a.run(_delegation_pydantic_map_ctx.get) is map_a
    assert ctx_b.run(_delegation_pydantic_map_ctx.get) is map_b
    # 外层（其他请求）不受污染
    assert _delegation_pydantic_map_ctx.get() is None


async def test_delegation_map_asyncio_to_thread_inherits():
    """真实 asyncio.to_thread 语义（crewai_async_patch 的 offload 路径）。

    to_thread 会复制发起方的 contextvars 到工作线程：请求 task 里 set 的表，
    工具线程内 get() 能读到。token reset 避免污染 pytest 自身上下文。
    """
    import asyncio

    map_a = {"role-a": int}
    token = _delegation_pydantic_map_ctx.set(map_a)
    try:
        got = await asyncio.to_thread(_delegation_pydantic_map_ctx.get)
    finally:
        _delegation_pydantic_map_ctx.reset(token)
    assert got is map_a


def test_history_init_exists_in_source():
    """静态兜底：run_crew_chat 中 history 必须在 sess 分支外初始化。

    防止回归：sess is None 时 len(history) 触发 NameError。
    """
    import inspect

    from app.crews import factory

    src = inspect.getsource(factory.run_crew_chat)
    assert "history: list[dict[str, str]] = []" in src
    # 初始化必须出现在 if sess is not None 之前（缩进层级判定：初始化行在分支外）
    init_pos = src.index("history: list[dict[str, str]] = []")
    branch_pos = src.index("if sess is not None")
    assert init_pos < branch_pos, "history 初始化必须先于 sess 分支"
