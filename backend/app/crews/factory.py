"""Crew 工厂 - 动态实例化 CrewAI Agent/Task/Crew

Week 1: 单 Agent 最小闭环
Week 2: 硬编码 Researcher+Writer 多 Agent
Week 3: **DB 驱动** — build_crew_from_db() 从 PostgreSQL 读取配置动态装配 Crew，
        每次请求新建 Crew 执行后销毁，实现配置热更新。
"""
import asyncio
import concurrent.futures
import logging
import os
import time

from crewai import Agent, Crew, Task
from crewai import __version__ as _crewai_version  # noqa: F401  仅用于 import 触发
from crewai.process import Process
from crewai.task import TaskOutput
from crewai.utilities.converter import ConverterError
from pydantic import BaseModel as PydanticBaseModel, Field, create_model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.events import AgentEvent
from app.crews.crewai_async_patch import apply_async_tool_patch
from app.crews.hook_registry import instantiate_hook
from app.crews.tool_events import wrap_tool_with_events
from app.crews.tool_hooks import BaseToolHook, wrap_tool_with_hooks
from app.crews.tool_registry import instantiate_tool
from app.services.memory_stm import build_history_context
from app.db.session import AsyncSessionLocal
from app.llm import AliyunLLM
from app.models import AgentConfig, CrewConfig, OutputSchemaConfig, TaskConfig, ToolConfig
from app.tools.human_approval_tool import HumanApprovalTool
from app.tools.load_skill_tool import LoadSkillTool

logger = logging.getLogger("crews")

# LLM 单例（避免重复构造）
_llm_instance: AliyunLLM | None = None


def get_llm() -> AliyunLLM:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = AliyunLLM(
            model=settings.LLM_MODEL,
            api_key=settings.QWEN_API_KEY,
            region=settings.LLM_REGION,
            temperature=settings.LLM_TEMPERATURE,
            timeout=settings.LLM_TIMEOUT,
        )
    return _llm_instance


def _make_step_callback(
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
    agent_names: list[str] | None = None,
    manager_role: str | None = None,
):
    """CrewAI step_callback：将 Agent 思考步骤推入 Queue。

    agent_names 按 Task 执行顺序排列的 agent 角色名。
    sequential 模式下，检测 AgentFinish 自动切换到下一个 agent。
    manager_role: hierarchical 模式下 manager 的角色名，其思考不路由到助手回答。

    同时设置 LLM 流式上下文，让 AliyunLLM.call() 在非 function-calling
    模式下将每个 token 实时推送为 thinking_token SSE 事件。
    route_to_answer=True 时（非 manager agent），同时推送 token 事件到助手气泡。
    """
    step_counter = {"n": 0}
    current_idx = {"n": 0}

    # 初始化流式上下文：CrewAI 在 thread pool 线程中运行，contextvars 自动继承
    from app.llm.aliyun_llm import _StreamContext, _stream_ctx
    initial_role = agent_names[0] if agent_names else "Agent"
    # 非 manager agent 的 token 同时路由到助手回答气泡
    is_manager = manager_role is not None and initial_role == manager_role
    ctx = _StreamContext(
        queue=queue,
        loop=loop,
        agent_role=initial_role,
        step_n=1,  # 从 1 开始，与首次 step_callback 的 step_counter["n"] 对齐
        route_to_answer=not is_manager,
    )
    _stream_ctx.set(ctx)

    def callback(partial_output):
        step_counter["n"] += 1
        text = partial_output if isinstance(partial_output, str) else str(partial_output)

        # 按 Task 执行顺序推断当前 agent
        if agent_names:
            idx = min(current_idx["n"], len(agent_names) - 1)
            agent_role = agent_names[idx]
        else:
            agent_role = _guess_agent_from_output(text)

        # 更新流式上下文：下一次 LLM 调用时生效（当前 callback 在 LLM 完成后才触发）
        ctx.agent_role = agent_role
        ctx.step_n = step_counter["n"] + 1
        # 非 manager agent 的 token 路由到助手气泡
        ctx.route_to_answer = not (manager_role is not None and agent_role == manager_role)

        evt = AgentEvent(
            type="agent_thinking",
            content=text,
            step=step_counter["n"],
            agent=agent_role,
        )
        loop.call_soon_threadsafe(queue.put_nowait, evt)

        # 检测 AgentFinish（当前 agent 完成），下次切换到下一个 agent
        if agent_names and current_idx["n"] < len(agent_names) - 1:
            text_head = text[:80]
            if "AgentFinish" in text_head or hasattr(partial_output, "return_values"):
                current_idx["n"] += 1
                # 流式上下文跟随切换到下一个 agent，同步更新 route_to_answer
                next_idx = min(current_idx["n"], len(agent_names) - 1)
                next_role = agent_names[next_idx]
                ctx.agent_role = next_role
                ctx.route_to_answer = not (manager_role is not None and next_role == manager_role)

    return callback


def _guess_agent_from_output(text: str) -> str | None:
    """根据思考文本推断当前 Agent 角色（fallback，当 agent_names 未提供时用）。"""
    if not text:
        return None
    lower = text.lower()
    if "研究" in text or "research" in lower or "搜索" in text or "检索" in text:
        return "研究员"
    if "撰写" in text or "writer" in lower or "写作" in text or "整理成" in text:
        return "撰稿人"
    return None


# ---------- DB 驱动的 Crew 装配 ----------


def _build_embedder_config() -> dict:
    """DashScope embedder 配置（OpenAI 兼容端点）。

    CrewAI 的 embedder 字段接受 ProviderSpec dict，
    用 provider="openai" + api_base 指向 DashScope 即可复用现有 Key。

    Week 11 性能优化：模块级单例，避免每次 build_crew_from_db 都重建 dict
    （CrewAI 内部仍会基于此 dict 新建 client，但 dict 本身可复用）。
    """
    global _embedder_config_cache
    if _embedder_config_cache is not None:
        return _embedder_config_cache
    _embedder_config_cache = {
        "provider": "openai",
        "config": {
            "api_key": settings.QWEN_API_KEY,
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model_name": settings.EMBEDDING_MODEL,
            "dimensions": settings.EMBEDDING_DIM,
        },
    }
    return _embedder_config_cache


_embedder_config_cache: dict | None = None


def _substitute_user_input(template: str, user_input: str) -> str:
    """安全替换 {user_input} 占位符。

    用 replace 而非 str.format，避免 description 中其他 {xxx} 误触发。
    """
    return template.replace("{user_input}", user_input)


# Skills 渐进式披露阈值
_SKILL_PROGRESSIVE_THRESHOLD = 5


def _inject_skills(
    backstory: str,
    skills: list,
    allow_progressive: bool = True,
) -> tuple[str, LoadSkillTool | None]:
    """根据 skill 数量决定全量注入 or 渐进式披露。

    >=5 skills 且 allow_progressive: 摘要注入 + 返回 LoadSkillTool
    其他: 全量注入（保持原行为），返回 None

    返回 (backstory, load_skill_tool_or_None)
    """
    if allow_progressive and len(skills) >= _SKILL_PROGRESSIVE_THRESHOLD:
        lines = [f"  - {s.name}: {s.description}" for s in skills]
        backstory += (
            "\n\n[可用技能]\n" + "\n".join(lines)
            + "\n\n当任务涉及上述技能时，先用 load_skill 工具加载对应技能的完整指令。"
        )
        skills_map = {s.name: s.prompt_template for s in skills}
        return backstory, LoadSkillTool(skills_map=skills_map)
    else:
        for s in skills:
            backstory += f"\n\n[技能] {s.name}:\n{s.prompt_template}"
        return backstory, None


async def build_crew_from_db(
    crew_id: int,
    user_input: str,
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
) -> Crew:
    """从 DB 读取 Crew 配置，动态装配 CrewAI Crew。

    每次调用新建 Crew，执行后即销毁，配置热更新天然成立。
    """
    t0 = time.perf_counter()
    async with AsyncSessionLocal() as session:
        crew = await _load_crew(session, crew_id)
        if crew is None:
            raise ValueError(f"Crew id={crew_id} 不存在")

        # 预加载所有相关 Agent（含 tools）和 Tasks
        agents_cfg: list[AgentConfig] = list(crew.agents)  # selectin 已加载
        tasks_cfg: list[TaskConfig] = list(crew.tasks)  # selectin 已加载，按 position 排序

    t_db = time.perf_counter()
    logger.info(
        "timing: build_crew_from_db db_load %.3fs (crew=%s agents=%d tasks=%d)",
        t_db - t0, crew.name, len(agents_cfg), len(tasks_cfg),
    )

    llm = get_llm()

    def _get_agent_llm(acfg_llm_model: str | None) -> AliyunLLM:
        """返回 agent 专用 LLM 实例，优先使用 acfg.llm_model，None 时用默认单例。"""
        if not acfg_llm_model or acfg_llm_model == settings.LLM_MODEL:
            return llm
        return AliyunLLM(
            model=acfg_llm_model,
            api_key=settings.QWEN_API_KEY,
            region=settings.LLM_REGION,
            temperature=settings.LLM_TEMPERATURE,
            timeout=settings.LLM_TIMEOUT,
        )

    # 构造 Agent 实例（id → Agent 映射，供 Task 引用）
    agent_map: dict[int, Agent] = {}
    t_agent_start = time.perf_counter()
    for acfg in agents_cfg:
        # Week 9: 先处理 skills（可能产生 LoadSkillTool），再实例化普通工具
        backstory, load_skill_tool = _inject_skills(
            acfg.backstory, list(acfg.skills), allow_progressive=True,
        )

        # 实例化工具并注入事件包装
        tools = []
        for tcfg in acfg.tools:
            t_tool0 = time.perf_counter()
            try:
                base_tool = instantiate_tool(tcfg.tool_key, tcfg.config_json)
            except KeyError:
                logger.warning(f"tool_key {tcfg.tool_key} 未注册，跳过")
                continue
            t_tool1 = time.perf_counter()
            if t_tool1 - t_tool0 > 0.5:
                logger.info(
                    "timing: instantiate_tool slow %.3fs tool_key=%s (likely first-import heavy dep)",
                    t_tool1 - t_tool0, tcfg.tool_key,
                )
            # HumanApprovalTool 需要注入事件队列（HITL approval_requested 事件）
            if isinstance(base_tool, HumanApprovalTool):
                base_tool.bind_event_emitter(queue, loop, agent_role=acfg.role)

            # 通用 Tool Hook 装配：从 config_json.hooks 读取 hook 声明并实例化
            # 装配顺序：tool._run → wrap_tool_with_hooks（业务 hooks，内层）
            #                            → wrap_tool_with_events（SSE 事件，外层）
            cfg = tcfg.config_json or {}
            hook_specs = cfg.get("hooks", []) or []
            hooks: list[BaseToolHook] = []
            for spec in hook_specs:
                if isinstance(spec, dict):
                    hk = spec.get("key")
                    hc = spec.get("config", {}) or {}
                else:
                    hk = spec
                    hc = {}
                if not hk:
                    logger.warning(f"hook spec 无 key，跳过: {spec}")
                    continue
                h = instantiate_hook(hk, hc, queue, loop, agent_role=acfg.role)
                if h is not None:
                    hooks.append(h)
            if hooks:
                base_tool = wrap_tool_with_hooks(
                    base_tool, hooks, queue, loop, agent_role=acfg.role,
                )

            tools.append(
                wrap_tool_with_events(base_tool, queue, loop, agent_role=acfg.role)
            )
        # LoadSkillTool 事件包装（渐进式披露时）
        if load_skill_tool is not None:
            tools.append(
                wrap_tool_with_events(load_skill_tool, queue, loop, agent_role=acfg.role)
            )

        # LLM 参数：Agent 配置优先，None 时用默认
        agent = Agent(
            role=acfg.role,
            goal=acfg.goal,
            backstory=backstory,
            llm=_get_agent_llm(acfg.llm_model),
            verbose=True,
            max_iter=acfg.max_iter,
            # Week 15：CrewAI 内置记忆默认关闭（项目用自带三层记忆），由总开关统一控制
            memory=acfg.memory and settings.CREWAI_NATIVE_MEMORY_ENABLED,
            tools=tools,
        )
        agent_map[acfg.id] = agent

    t_agents = time.perf_counter()
    logger.info(
        "timing: build_crew_from_db agents_construct %.3fs (count=%d)",
        t_agents - t_agent_start, len(agents_cfg),
    )

    is_hierarchical = crew.process_type == "hierarchical"

    # Week 6: hierarchical 模式构造 manager agent
    manager_agent: Agent | None = None
    if is_hierarchical:
        if crew.manager_agent is None:
            raise ValueError(
                f"hierarchical Crew '{crew.name}' 未配置 manager_agent，"
                "请在配置中心指定主 Agent"
            )
        mgr_cfg = crew.manager_agent  # selectin 已加载
        # Week 9: manager agent 全量注入 skills（不能挂载 tools，不走渐进式）
        mgr_backstory, _ = _inject_skills(
            mgr_cfg.backstory, list(mgr_cfg.skills), allow_progressive=False,
        )
        # CrewAI hierarchical: manager agent 不能挂载 tools（仅负责拆解和委派）
        manager_agent = Agent(
            role=mgr_cfg.role,
            goal=mgr_cfg.goal,
            backstory=mgr_backstory,
            llm=_get_agent_llm(mgr_cfg.llm_model),
            verbose=True,
            max_iter=mgr_cfg.max_iter,
            memory=mgr_cfg.memory and settings.CREWAI_NATIVE_MEMORY_ENABLED,
            allow_delegation=True,
        )

    # 构造 Task 实例（按 position 顺序，先建后建可引用为 context）
    task_map: dict[int, Task] = {}
    tasks: list[Task] = []
    agent_names_by_task: list[str] = []  # 按 task 执行顺序的 agent 角色名
    for tcfg in tasks_cfg:
        # hierarchical 模式下 agent_id 可为 None（由 manager 动态分配）
        agent = agent_map.get(tcfg.agent_id) if tcfg.agent_id else None
        if tcfg.agent_id is not None and agent is None:
            logger.warning(f"Task {tcfg.name} 引用的 agent_id={tcfg.agent_id} 不在 Crew 中，跳过")
            continue

        # context 依赖：解析 context_task_ids 为已构造 Task 列表
        context_tasks: list[Task] = []
        if tcfg.context_task_ids:
            for tid in tcfg.context_task_ids:
                ctx = task_map.get(int(tid))
                if ctx is not None:
                    context_tasks.append(ctx)

        # output_pydantic：动态生成 Pydantic 模型
        output_pydantic = None
        if tcfg.output_schema_id is not None and tcfg.output_schema is not None:
            output_pydantic = _build_pydantic_from_schema(tcfg.output_schema)
            logger.debug(
                "Task '%s': output_pydantic=%s (schema=%s, fields=%d)",
                tcfg.name,
                output_pydantic.__name__,
                tcfg.output_schema.name,
                len(tcfg.output_schema.schema_fields),
            )

        task = Task(
            description=_substitute_user_input(tcfg.description, user_input),
            expected_output=_substitute_user_input(tcfg.expected_output, user_input),
            agent=agent,
            context=context_tasks if context_tasks else None,
            output_pydantic=output_pydantic,
        )
        # agent 为 None 时（hierarchical），用 manager role 占位
        agent_names_by_task.append(agent.role if agent else (manager_agent.role if manager_agent else "Agent"))
        task_map[tcfg.id] = task
        tasks.append(task)

    # process 类型
    process = Process.hierarchical if is_hierarchical else Process.sequential

    # step_callback agent_names：hierarchical 时 manager 在前
    callback_agent_names = (
        [manager_agent.role] + agent_names_by_task if is_hierarchical and manager_agent
        else agent_names_by_task
    )

    crew_kwargs: dict = dict(
        agents=list(agent_map.values()),
        tasks=tasks,
        process=process,
        verbose=True,
        step_callback=_make_step_callback(
            queue, loop, callback_agent_names,
            manager_role=manager_agent.role if is_hierarchical and manager_agent else None,
        ),
        task_callback=_make_task_callback(queue, loop),
    )
    if is_hierarchical and manager_agent:
        crew_kwargs["manager_agent"] = manager_agent
        _wrap_delegate_tool_for_tracing(manager_agent, queue, loop)

        # ── hierarchical 委派 output_pydantic 注入 ──
        # 在 manager 委派任务给 sub-agent 时，CrewAI 的 BaseAgentTool._execute()
        # 会新建 Task(description=..., agent=...) 不带 output_pydantic。
        # 这里构建查找表并 monkey-patch _execute，让委派创建的 Task
        # 自动继承对应 agent role 在 DB 中配置的 output_schema。
        global _delegation_pydantic_map
        _delegation_pydantic_map = _build_delegation_pydantic_map(tasks_cfg, agents_cfg)
        _apply_delegation_pydantic_patch()

    # Week 8: 双层记忆 — 任意 agent 启用 memory 时，Crew 级别开启 memory + embedder
    # Week 15: 项目自带三层记忆，CrewAI 内置记忆由总开关 CREWAI_NATIVE_MEMORY_ENABLED 控制，默认关闭
    any_memory = any(acfg.memory for acfg in agents_cfg)
    if is_hierarchical and manager_agent and mgr_cfg.memory:
        any_memory = True
    crew_memory = settings.CREWAI_NATIVE_MEMORY_ENABLED and any_memory
    if crew_memory:
        crew_kwargs["memory"] = True
        crew_kwargs["embedder"] = _build_embedder_config()
        # 确保 CrewAI 存储目录存在
        storage_dir = settings.CREWAI_STORAGE_DIR
        os.makedirs(storage_dir, exist_ok=True)
        os.environ.setdefault("CREWAI_STORAGE_DIR", storage_dir)
    else:
        # 显式关闭，避免 CrewAI 任何内置记忆（STM/LTM/Entity/External）创建
        crew_kwargs["memory"] = False

    t_crew0 = time.perf_counter()
    crew_obj = Crew(**crew_kwargs)
    t_crew1 = time.perf_counter()
    logger.info(
        "timing: Crew(memory=%s) construct %.3fs (total build_crew %.3fs)",
        crew_memory, t_crew1 - t_crew0, t_crew1 - t0,
    )

    # Week 11 性能优化：LongTermMemory 评估控制
    # - CREWAI_LONG_TERM_MEMORY_ENABLED=True（默认）：保留 LongTermMemory，
    #   通过 _apply_ltm_async_patch() 让 TaskEvaluator 评估异步+小模型执行
    # - CREWAI_LONG_TERM_MEMORY_ENABLED=False：彻底禁用，回到 11s 无评估
    # （Week 15 起 crew_memory 默认 False，以下分支仅当 CREWAI_NATIVE_MEMORY_ENABLED=true 时可达）
    if crew_memory and not settings.CREWAI_LONG_TERM_MEMORY_ENABLED:
        crew_obj._long_term_memory = None
        logger.info("timing: LongTermMemory disabled (CREWAI_LONG_TERM_MEMORY_ENABLED=False)")
    elif any_memory:
        logger.info(
            "timing: LongTermMemory enabled (async+model=%s)",
            settings.CREWAI_EVALUATOR_LLM_MODEL,
        )

    return crew_obj


# ---------- Week 11 LongTermMemory 异步+小模型 monkey-patch ----------

# 后台评估线程池：fire-and-forget，不阻塞主回答
_ltm_eval_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="ltm-eval",
)
_ltm_evaluator_llm: AliyunLLM | None = None
_ltm_patch_applied = False


def _get_evaluator_llm() -> AliyunLLM:
    """评估专用 LLM（小模型，qwen-turbo 默认），与主回答 LLM 隔离。"""
    global _ltm_evaluator_llm
    if _ltm_evaluator_llm is None:
        _ltm_evaluator_llm = AliyunLLM(
            model=settings.CREWAI_EVALUATOR_LLM_MODEL,
            api_key=settings.QWEN_API_KEY,
            region=settings.LLM_REGION,
            temperature=0.3,
            timeout=60,
        )
    return _ltm_evaluator_llm


def _apply_ltm_async_patch() -> None:
    """monkey-patch CrewAgentExecutorMixin._create_long_term_memory，
    让 TaskEvaluator 评估在后台线程池执行（fire-and-forget），主回答不阻塞。

    同时把评估 LLM 替换为独立的小模型（默认 qwen-turbo）。
    幂等：多次调用只生效一次。
    """
    global _ltm_patch_applied
    if _ltm_patch_applied:
        return
    if not settings.CREWAI_LONG_TERM_MEMORY_ENABLED:
        # 用户彻底禁用 LTM，无需 patch
        return

    try:
        from crewai.agents.agent_builder.base_agent_executor_mixin import (
            CrewAgentExecutorMixin as _Mixin,
        )
        from crewai.memory.entity.entity_memory_item import EntityMemoryItem
        from crewai.memory.long_term.long_term_memory_item import (
            LongTermMemoryItem,
        )
        from crewai.utilities.evaluators.task_evaluator import (
            TaskEvaluator as _TaskEvaluator,
        )
    except ImportError as e:
        logger.warning(f"failed to patch LTM evaluation (import): {e}")
        return

    _orig_create = _Mixin._create_long_term_memory

    def _async_create_long_term_memory(self, output):
        """异步版：把整个评估+保存丢后台线程，主流程立即返回。"""
        # 复用原方法的 guard clause，避免无意义的后台提交
        if not (
            self.crew
            and getattr(self.crew, "_long_term_memory", None)
            and getattr(self.crew, "_entity_memory", None)
            and self.task
            and self.agent
        ):
            return
        # 抓取必要引用，提交后台线程
        _ltm_eval_executor.submit(
            _run_ltm_evaluation, self, output,
        )

    def _run_ltm_evaluation(executor_mixin, output):
        """后台线程：执行 TaskEvaluator + 保存 LTM/EM。失败仅记日志。"""
        t0 = time.perf_counter()
        try:
            ltm_agent = _TaskEvaluator(executor_mixin.agent)
            # 关键：替换 LLM 为小模型
            ltm_agent.llm = _get_evaluator_llm()
            evaluation = ltm_agent.evaluate(executor_mixin.task, output.text)
            if isinstance(evaluation, ConverterError):
                logger.warning(f"ltm async evaluation converter error: {evaluation}")
                return
            long_term_memory = LongTermMemoryItem(
                task=executor_mixin.task.description,
                agent=executor_mixin.agent.role,
                quality=evaluation.quality,
                datetime=str(time.time()),
                expected_output=executor_mixin.task.expected_output,
                metadata={
                    "suggestions": evaluation.suggestions,
                    "quality": evaluation.quality,
                },
            )
            executor_mixin.crew._long_term_memory.save(long_term_memory)
            entity_memories = [
                EntityMemoryItem(
                    name=e.name,
                    type=e.type,
                    description=e.description,
                    relationships="\n".join([f"- {r}" for r in e.relationships]),
                )
                for e in evaluation.entities
            ]
            if entity_memories:
                executor_mixin.crew._entity_memory.save(entity_memories)
            logger.info(
                "timing: ltm async evaluation %.3fs (model=%s, quality=%.1f, entities=%d)",
                time.perf_counter() - t0,
                settings.CREWAI_EVALUATOR_LLM_MODEL,
                evaluation.quality,
                len(entity_memories),
            )
        except Exception as e:
            logger.warning(f"ltm async evaluation failed: {e}")

    _Mixin._create_long_term_memory = _async_create_long_term_memory
    _ltm_patch_applied = True
    logger.info(
        "ltm async patch applied (model=%s, max_workers=2)",
        settings.CREWAI_EVALUATOR_LLM_MODEL,
    )


# 模块加载时自动应用 patch
_apply_ltm_async_patch()
apply_async_tool_patch()


# ---------- 辅助：动态 Pydantic 模型生成 ----------

_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list[str]": list[str],
    "list[int]": list[int],
    "list[float]": list[float],
}


def _resolve_field_type(type_name: str) -> type:
    py_type = _TYPE_MAP.get(type_name)
    if py_type is None:
        logger.warning(f"output_schema: unknown type '{type_name}', falling back to str")
        return str
    return py_type


def _build_pydantic_from_schema(schema_cfg: OutputSchemaConfig) -> type[PydanticBaseModel]:
    """从 OutputSchemaConfig.schema_fields 动态生成 Pydantic model。"""
    fields: dict[str, tuple] = {}
    for f in schema_cfg.schema_fields:
        name = f.get("name", "")
        if not name:
            continue
        py_type = _resolve_field_type(f.get("type", "str"))
        required = f.get("required", True)
        desc = f.get("description", "")
        default = ... if required else None
        fields[name] = (py_type, Field(description=desc, default=default))
    model_name = schema_cfg.name.replace(" ", "_")
    return create_model(model_name, **fields)


# ---------- Tracing：task_callback SSE 事件 ----------


def _make_task_callback(
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
):
    """CrewAI task_callback：每个 Task 完成后推送结构化事件到 SSE。"""

    def callback(task_output: TaskOutput) -> None:
        evt = AgentEvent(
            type="task_completed",
            agent=task_output.agent,
            content=f"Task '{task_output.name}' 完成",
            output={
                "task_name": task_output.name,
                "agent": task_output.agent,
                "output_format": str(task_output.output_format),
                "pydantic_valid": task_output.pydantic is not None,
                "raw_preview": str(task_output.raw)[:300],
            },
        )
        loop.call_soon_threadsafe(queue.put_nowait, evt)

    return callback


# ---------- Tracing：delegation 事件包装 ----------


def _wrap_delegate_tool_for_tracing(
    manager_agent: Agent,
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
) -> None:
    """包装 manager agent 的 DelegateWorkTool，推送 delegation SSE 事件。"""
    try:
        agent_tools = getattr(manager_agent, "agent_tools", None)
        if agent_tools is None:
            return
        for tool_wrapper in agent_tools:
            tool = getattr(tool_wrapper, "tool", tool_wrapper)
            tool_name = getattr(tool, "name", "")
            if "delegate" not in tool_name.lower():
                continue
            original_run = tool._run

            def wrapped_run(*args, task=None, context=None, coworker=None, **kwargs):
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    AgentEvent(
                        type="delegation",
                        agent=manager_agent.role,
                        content=f"委派任务给 {coworker or '?'}",
                        input={
                            "task": (task or "")[:200],
                            "context": (context or "")[:200],
                            "coworker": coworker,
                        },
                    ),
                )
                return original_run(*args, task=task, context=context, coworker=coworker, **kwargs)

            tool._run = wrapped_run  # type: ignore[method-assign]
            logger.info("delegation tracing: wrapped DelegateWorkTool for manager=%s", manager_agent.role)
            return
    except Exception as e:
        logger.warning(f"delegation tracing: failed to wrap DelegateWorkTool: {e}")


# ---------- Tracing：hierarchical 委派 output_pydantic 注入 ----------

# 模块级查找表：sanitized_agent_role → Pydantic model
# 由 build_crew_from_db() 设置，BaseAgentTool._execute patch 消费
_delegation_pydantic_map: dict[str, type[PydanticBaseModel]] | None = None
_delegation_pydantic_patch_applied = False


def _build_delegation_pydantic_map(
    task_configs: list[TaskConfig],
    agents_cfg: list[AgentConfig],
) -> dict[str, type[PydanticBaseModel]]:
    """从 TaskConfig 列表构建 sanitized_role → Pydantic model 的查找表。

    只包含配置了 output_schema 且有固定 agent_id 的 task。
    匹配规则与 BaseAgentTool.sanitize_agent_name() 完全一致。
    """
    # 先建 agent_id → role 映射
    id_to_role: dict[int, str] = {a.id: a.role for a in agents_cfg}
    role_map: dict[str, type[PydanticBaseModel]] = {}
    for tcfg in task_configs:
        if tcfg.output_schema is None or tcfg.agent_id is None:
            continue
        agent_role = id_to_role.get(tcfg.agent_id)
        if agent_role is None:
            continue
        pydantic_model = _build_pydantic_from_schema(tcfg.output_schema)
        # 对 agent role 做与 CrewAI 一致的 sanitize：normalize whitespace + lowercase + remove quotes
        normalized = " ".join(agent_role.split())
        sanitized = normalized.replace('"', "").casefold()
        role_map[sanitized] = pydantic_model
        logger.debug(
            "delegation pydantic map: role='%s' sanitized='%s' → pydantic=%s",
            agent_role, sanitized, pydantic_model.__name__,
        )
    return role_map


def _apply_delegation_pydantic_patch() -> None:
    """Monkey-patch BaseAgentTool._execute，在委派创建 Task 时注入 output_pydantic。

    解决了 hierarchical 模式下 manager 委派时，
    CrewAI 在 BaseAgentTool._execute() 中新建 Task 不带 output_pydantic 的问题。

    幂等：多次调用只生效一次。
    """
    global _delegation_pydantic_patch_applied
    if _delegation_pydantic_patch_applied:
        return

    try:
        from crewai.tools.agent_tools.base_agent_tools import (
            BaseAgentTool as _BAT,
        )
        from crewai.task import Task as _CrewTask
    except ImportError as e:
        logger.warning(f"failed to patch delegation output_pydantic (import): {e}")
        return

    _orig_execute = _BAT._execute

    def patched_execute(
        self,
        agent_name: str | None,
        task: str,
        context: str | None = None,
    ) -> str:
        """带 output_pydantic 注入的 _execute。

        与原方法行为完全一致，唯一区别：在创建 task_with_assigned_agent 时，
        从 _delegation_pydantic_map 查找匹配的 Pydantic model 并注入。
        """
        try:
            if agent_name is None:
                agent_name = ""
            sanitized_name = self.sanitize_agent_name(agent_name)

            agent = [
                a for a in self.agents
                if self.sanitize_agent_name(a.role) == sanitized_name
            ]
        except (AttributeError, ValueError) as e:
            return self.i18n.errors("agent_tool_unexisting_coworker").format(
                coworkers="\n".join([
                    f"- {self.sanitize_agent_name(a.role)}"
                    for a in self.agents
                ]),
                error=str(e),
            )

        if not agent:
            return self.i18n.errors("agent_tool_unexisting_coworker").format(
                coworkers="\n".join([
                    f"- {self.sanitize_agent_name(a.role)}"
                    for a in self.agents
                ]),
                error=f"No agent found with role '{sanitized_name}'",
            )

        selected_agent = agent[0]

        # ── 注入 output_pydantic ──
        output_pydantic = None
        if _delegation_pydantic_map is not None:
            selected_sanitized = self.sanitize_agent_name(selected_agent.role)
            output_pydantic = _delegation_pydantic_map.get(selected_sanitized)
            if output_pydantic is not None:
                logger.debug(
                    "delegation pydantic injected: agent=%s schema=%s",
                    selected_agent.role, output_pydantic.__name__,
                )

        try:
            task_with_assigned_agent = _CrewTask(
                description=task,
                agent=selected_agent,
                expected_output=selected_agent.i18n.slice("manager_request"),
                output_pydantic=output_pydantic,
                i18n=selected_agent.i18n,
            )
            return selected_agent.execute_task(
                task_with_assigned_agent, context
            )
        except Exception as e:
            return self.i18n.errors("agent_tool_execution_error").format(
                agent_role=self.sanitize_agent_name(selected_agent.role),
                error=str(e),
            )

    # 保留原方法的引用，供需要时回退/检查
    patched_execute._orig_execute = _orig_execute  # type: ignore[attr-defined]
    _BAT._execute = patched_execute
    _delegation_pydantic_patch_applied = True
    logger.info(
        "delegation output_pydantic patch applied: mapped %d roles",
        len(_delegation_pydantic_map) if _delegation_pydantic_map else 0,
    )


async def _load_crew(session: AsyncSession, crew_id: int) -> CrewConfig | None:
    """加载 Crew 及关联数据（selectin 关系自动加载）。"""
    stmt = select(CrewConfig).where(CrewConfig.id == crew_id)
    return (await session.execute(stmt)).scalar_one_or_none()


# 默认 crew_id 缓存（避免每次 chat 都查 DB，TTL=5 分钟）
_DEFAULT_CREW_ID_TTL = 300.0
_default_crew_id_cache: tuple[int | None, float] | None = None


async def get_default_crew_id() -> int | None:
    """获取默认种子 Crew 的 id（researcher_writer），带 5 分钟 TTL 缓存。"""
    global _default_crew_id_cache
    now = time.perf_counter()
    if _default_crew_id_cache is not None:
        cached_id, cached_at = _default_crew_id_cache
        if now - cached_at < _DEFAULT_CREW_ID_TTL:
            return cached_id
    async with AsyncSessionLocal() as session:
        stmt = select(CrewConfig).where(CrewConfig.name == "researcher_writer")
        crew = (await session.execute(stmt)).scalar_one_or_none()
        crew_id = crew.id if crew else None
    _default_crew_id_cache = (crew_id, now)
    return crew_id


def invalidate_default_crew_id_cache() -> None:
    """Crew CRUD API 更新/删除默认 Crew 时调用，使缓存失效。"""
    global _default_crew_id_cache
    _default_crew_id_cache = None


async def run_crew_chat(
    crew_id: int,
    user_input: str,
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
    session_id: str | None = None,
) -> str:
    """运行 DB 驱动的 Crew，将事件推入 queue，返回最终回答文本。

    三层记忆系统：
    - Layer 1 STM：从 DB chat_messages 读取历史 → compress_history 压缩
    - Layer 2 LTM：embed(user_input) → 检索 user_memories top-3（跨会话用户偏好）
    - Layer 3 KB：embed(user_input) → 检索 document_chunks top-2（高置信知识库片段）
    写路径：
    - Layer 1：append_message(user + assistant) → chat_messages 表
    - Layer 2：后台 ThreadPoolExecutor → qwen-turbo 提取偏好 → embed → user_memories 表
    """
    # ---------- 三层记忆读路径 ----------
    history_context = ""
    ltm_prefix = ""
    kb_prefix = ""
    db_session_id: int | None = None

    # 共享 query_vec：一次 embed，LTM 和 KB 检索共用
    query_vec: list[float] | None = None
    if session_id and settings.LTM_USER_MEMORY_ENABLED:
        try:
            from app.llm.embedding import embed_query
            t_embed0 = time.perf_counter()
            query_vec = await embed_query(user_input)
            logger.info(
                "timing: embed_query(user_input) %.3fs (dim=%d)",
                time.perf_counter() - t_embed0, len(query_vec) if query_vec else 0,
            )
        except Exception as e:
            logger.warning(f"embed_query failed (LTM/KB 检索将跳过): {e}")

    # Layer 1 STM：从 DB 读取历史 → 压缩剪枝 + 滚动摘要
    if session_id:
        t_hist0 = time.perf_counter()
        summary_text = ""
        async with AsyncSessionLocal() as db:
            from app.services.chat_service import get_session_by_uuid
            sess = await get_session_by_uuid(db, session_id)
            if sess is not None:
                db_session_id = sess.id
                # 滚动摘要（滑出窗口的旧消息摘要，如已生成）
                if settings.STM_SUMMARY_ENABLED:
                    try:
                        from app.models import ChatSessionSummary
                        from sqlalchemy import select as sa_select
                        summ_row = (
                            await db.execute(
                                sa_select(ChatSessionSummary).where(
                                    ChatSessionSummary.session_id == db_session_id
                                )
                            )
                        ).scalar_one_or_none()
                        if summ_row and summ_row.summary:
                            summary_text = summ_row.summary
                    except Exception as e:
                        logger.warning(f"stm summary load failed: {e}")
                # messages 已 selectin 加载，按 id 升序
                history = [
                    {"role": m.role, "content": m.content}
                    for m in sess.messages
                ]
                history_context = build_history_context(history, summary=summary_text)
        logger.info(
            "timing: get_chat_history(db) %.3fs (session_uuid=%s, msgs=%d, summary_chars=%d, stm_chars=%d)",
            time.perf_counter() - t_hist0, session_id,
            len(history) if session_id else 0,
            len(summary_text), len(history_context),
        )

    # Layer 2 LTM：语义检索用户偏好/经验
    if session_id and query_vec and settings.LTM_USER_MEMORY_ENABLED:
        try:
            from app.services.memory_ltm import search_relevant_memories, build_ltm_prefix
            t_ltm0 = time.perf_counter()
            memories = await search_relevant_memories(crew_id, query_vec, top_k=3)
            ltm_prefix = build_ltm_prefix(memories)
            logger.info(
                "timing: ltm_retrieve %.3fs (memories=%d, chars=%d)",
                time.perf_counter() - t_ltm0, len(memories), len(ltm_prefix),
            )
        except Exception as e:
            logger.warning(f"ltm_retrieve failed: {e}")

    # Layer 3 KB：高置信知识库片段预注入
    if query_vec and settings.KB_PREINJECT_ENABLED:
        try:
            from app.services.document_service import search_kb_high_confidence
            t_kb0 = time.perf_counter()
            kb_hits = await search_kb_high_confidence(
                query_vec,
                top_k=settings.KB_PREINJECT_TOP_K,
                score_threshold=settings.KB_PREINJECT_THRESHOLD,
            )
            if kb_hits:
                lines = ["以下是相关知识库片段，可作为参考：\n"]
                for i, h in enumerate(kb_hits, 1):
                    lines.append(f"[{i}] 来源={h['document_name']}（相似度={h['score']:.2f}）\n{h['content']}")
                lines.append("\n--- 以上为知识库参考 ---\n")
                kb_prefix = "\n".join(lines)
            logger.info(
                "timing: kb_preinject %.3fs (hits=%d, chars=%d)",
                time.perf_counter() - t_kb0, len(kb_hits), len(kb_prefix),
            )
        except Exception as e:
            logger.warning(f"kb_preinject failed: {e}")

    # 组装 effective_input：LTM + KB + STM + 原始问题
    parts = []
    if ltm_prefix:
        parts.append(ltm_prefix)
    if kb_prefix:
        parts.append(kb_prefix)
    if history_context:
        parts.append(history_context)
    parts.append(user_input)
    effective_input = "\n".join(parts)

    t_build0 = time.perf_counter()
    crew = await build_crew_from_db(crew_id, effective_input, queue, loop)
    logger.info("timing: build_crew_from_db total %.3fs", time.perf_counter() - t_build0)

    await queue.put(
        AgentEvent(
            type="agent_thinking",
            content=(
                f"Crew 启动（id={crew_id}）：DB 驱动装配，"
                f"{'层级编排（manager agent 编排）' if crew.process == Process.hierarchical else '顺序协作'}"
                f"{f'，LTM={len(ltm_prefix)}字' if ltm_prefix else ''}"
                f"{f'，KB={len(kb_prefix)}字' if kb_prefix else ''}"
                f"{f'，STM={len(history_context)}字' if history_context else ''}"
            ),
            step=0,
            agent="Crew",
        )
    )

    first_step_marker = {"t": None}
    t_kickoff0 = time.perf_counter()
    # 包装 crew.step_callback，捕获首个 step_callback 触发时间（Phase 1 诊断）
    _orig_cb = crew.step_callback
    if _orig_cb is not None:
        def _wrap_cb(partial_output, _orig=_orig_cb, _marker=first_step_marker):
            if _marker.get("t") is None:
                _marker["t"] = time.perf_counter()
                logger.info(
                    "timing: kickoff→first_step_callback %.3fs",
                    _marker["t"] - t_kickoff0,
                )
            return _orig(partial_output)
        crew.step_callback = _wrap_cb

    result = await crew.akickoff()
    t_kickoff1 = time.perf_counter()
    first_delay = (first_step_marker["t"] - t_kickoff0) if first_step_marker.get("t") else None
    logger.info(
        "timing: crew.akickoff() total %.3fs (first_step_delay=%s)",
        t_kickoff1 - t_kickoff0,
        f"{first_delay:.3f}s" if first_delay is not None else "N/A",
    )

    final_text = str(result.raw) if hasattr(result, "raw") else str(result)

    # Week 11+: 把本轮 user/assistant 消息持久化到 DB
    if session_id and db_session_id is not None:
        try:
            async with AsyncSessionLocal() as db:
                from app.services.chat_service import append_message
                await append_message(db, db_session_id, "user", user_input)
                await append_message(db, db_session_id, "assistant", final_text)
        except Exception as e:
            logger.warning(f"persist chat messages failed: {e}")

    # Layer 2 LTM 写：后台提取用户偏好/经验 → embed → 入库（fire-and-forget）
    if session_id and db_session_id is not None and settings.LTM_USER_MEMORY_ENABLED:
        try:
            from app.services.memory_ltm import extract_memories_async
            extract_memories_async(crew_id, db_session_id, user_input, final_text)
        except Exception as e:
            logger.warning(f"extract memories submit failed: {e}")

    # Layer 1 STM 写：后台增量滚动摘要（滑出窗口的旧消息 → qwen-turbo merge，fire-and-forget）
    if session_id and db_session_id is not None and settings.STM_SUMMARY_ENABLED:
        try:
            from app.services.memory_stm import summarize_session_async
            summarize_session_async(db_session_id)
        except Exception as e:
            logger.warning(f"stm summarize submit failed: {e}")

    # 推送最终回答（CrewAI 已提取干净的 Final Answer，替换流式 token 中的 ReAct 格式内容）
    await queue.put(AgentEvent(type="final_answer", content=final_text))
    return final_text


# ---------- 回退：单 Agent（无 DB 依赖）----------


async def run_single_agent_chat(
    user_input: str,
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
) -> str:
    """单 Agent 对话（无 DB 依赖，用于早期回退/调试）。"""
    llm = get_llm()

    agent = Agent(
        role="通用助手",
        goal="准确、简洁地回答用户的问题",
        backstory="你是一个直接、专业的中文助手。你会用清晰的中文回答问题，不啰嗦。",
        llm=llm,
        verbose=True,
        max_iter=8,
        memory=False,
    )

    task = Task(
        description=f"回答用户的以下问题：\n\n{user_input}",
        expected_output="一段简洁、准确的中文回答。",
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=True,
        step_callback=_make_step_callback(queue, loop),
    )

    result = await crew.akickoff()
    final_text = str(result.raw) if hasattr(result, "raw") else str(result)

    # 推送最终回答
    await queue.put(AgentEvent(type="final_answer", content=final_text))
    return final_text
