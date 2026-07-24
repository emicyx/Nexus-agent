"""种子数据：写入默认 Researcher + Writer Crew + 工具 + Skills

幂等：按 name 去重，已存在则跳过。启动时调用 ensure_seed()。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AgentConfig, CrewConfig, OutputSchemaConfig, SkillConfig, TaskConfig, ToolConfig
from app.models.association import CrewAgent

logger = logging.getLogger("seed")


async def _get_or_create_tool(
    session: AsyncSession,
    name: str,
    tool_key: str,
    description: str,
    config_json: dict | None = None,
) -> ToolConfig:
    stmt = select(ToolConfig).where(ToolConfig.name == name)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        # 幂等：已存在且 config_json 为空时才更新，避免重置已有配置
        if config_json and not existing.config_json:
            existing.config_json = config_json
            await session.flush()
            logger.info(f"seed: updated tool {name} config_json")
        return existing
    tool = ToolConfig(
        name=name,
        tool_key=tool_key,
        description=description,
        config_json=config_json or {},
    )
    session.add(tool)
    await session.flush()
    logger.info(f"seed: created tool {name}")
    return tool


async def _get_or_create_skill(
    session: AsyncSession,
    name: str,
    description: str,
    prompt_template: str,
    skill_key: str | None = None,
) -> SkillConfig:
    stmt = select(SkillConfig).where(SkillConfig.name == name)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        # 幂等同步 prompt_template/description
        changed = False
        if existing.prompt_template != prompt_template:
            existing.prompt_template = prompt_template; changed = True
        if existing.description != description:
            existing.description = description; changed = True
        if changed:
            await session.flush()
            logger.info(f"seed: synced skill {name}")
        return existing
    skill = SkillConfig(
        name=name,
        description=description,
        prompt_template=prompt_template,
        skill_key=skill_key,
    )
    session.add(skill)
    await session.flush()
    logger.info(f"seed: created skill {name}")
    return skill


async def _get_or_create_agent(
    session: AsyncSession,
    name: str,
    role: str,
    goal: str,
    backstory: str,
    tools: list[ToolConfig] | None = None,
    skills: list[SkillConfig] | None = None,
    max_iter: int = 8,
    memory: bool = False,
    llm_model: str | None = None,
) -> AgentConfig:
    stmt = select(AgentConfig).where(AgentConfig.name == name)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        # 幂等但同步 tools（已存在 agent 的工具列表可能需要更新）
        if tools is not None:
            existing_ids = {t.id for t in existing.tools}
            new_ids = {t.id for t in tools}
            if existing_ids != new_ids:
                existing.tools = list(tools)
                await session.flush()
                logger.info(f"seed: synced tools for agent {name}: {[t.name for t in tools]}")
        # 幂等同步 skills
        if skills is not None:
            existing_sids = {s.id for s in existing.skills}
            new_sids = {s.id for s in skills}
            if existing_sids != new_sids:
                existing.skills = list(skills)
                await session.flush()
                logger.info(f"seed: synced skills for agent {name}: {[s.name for s in skills]}")
        # 同步 role/goal/backstory（让种子更新生效）
        changed = False
        if existing.role != role:
            existing.role = role; changed = True
        if existing.goal != goal:
            existing.goal = goal; changed = True
        if existing.backstory != backstory:
            existing.backstory = backstory; changed = True
        if existing.memory != memory:
            existing.memory = memory; changed = True
        if existing.llm_model != llm_model:
            existing.llm_model = llm_model; changed = True
        if changed:
            await session.flush()
            logger.info(f"seed: synced profile for agent {name} (memory={memory})")
        return existing
    agent = AgentConfig(
        name=name,
        role=role,
        goal=goal,
        backstory=backstory,
        max_iter=max_iter,
        memory=memory,
        llm_model=llm_model,
        tools=tools or [],
        skills=skills or [],
    )
    session.add(agent)
    await session.flush()
    logger.info(f"seed: created agent {name}")
    return agent


async def _get_or_create_crew(
    session: AsyncSession,
    name: str,
    description: str,
    agents: list[AgentConfig],
    process_type: str = "sequential",
    manager_agent: AgentConfig | None = None,
) -> CrewConfig:
    stmt = select(CrewConfig).where(CrewConfig.name == name)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    crew = CrewConfig(
        name=name,
        description=description,
        process_type=process_type,
        manager_agent_id=manager_agent.id if manager_agent else None,
    )
    session.add(crew)
    await session.flush()
    # 显式设置 position（不用 agents= 关系，避免 M2M 双重插入）
    for idx, agent in enumerate(agents):
        await session.execute(
            CrewAgent.insert().values(
                crew_id=crew.id, agent_id=agent.id, position=idx
            )
        )
    logger.info(f"seed: created crew {name}")
    return crew


async def _get_or_create_task(
    session: AsyncSession,
    crew: CrewConfig,
    name: str,
    description: str,
    expected_output: str,
    position: int,
    agent: AgentConfig | None = None,
    context_task_ids: list[int] | None = None,
    output_schema_id: int | None = None,
) -> TaskConfig:
    stmt = select(TaskConfig).where(
        TaskConfig.crew_id == crew.id, TaskConfig.name == name
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        # 幂等同步 description/expected_output/position/context_task_ids/output_schema_id
        # （对齐 _get_or_create_skill 的同步策略，避免 seed 改文案不生效）
        changed = False
        if existing.description != description:
            existing.description = description
            changed = True
        if existing.expected_output != expected_output:
            existing.expected_output = expected_output
            changed = True
        if existing.position != position:
            existing.position = position
            changed = True
        if (existing.context_task_ids or []) != (context_task_ids or []):
            existing.context_task_ids = context_task_ids
            changed = True
        if existing.output_schema_id != output_schema_id:
            existing.output_schema_id = output_schema_id
            changed = True
        if agent is not None:
            new_agent_id = agent.id if agent else None
            if existing.agent_id != new_agent_id:
                existing.agent_id = new_agent_id
                changed = True
        if changed:
            await session.flush()
            logger.info(f"seed: updated task {name}")
        return existing
    task = TaskConfig(
        crew_id=crew.id,
        agent_id=agent.id if agent else None,
        name=name,
        description=description,
        expected_output=expected_output,
        position=position,
        context_task_ids=context_task_ids,
        output_schema_id=output_schema_id,
    )
    session.add(task)
    await session.flush()
    logger.info(f"seed: created task {name}")
    return task


async def _get_or_create_schema(
    session: AsyncSession,
    name: str,
    description: str,
    schema_fields: list[dict],
) -> OutputSchemaConfig:
    stmt = select(OutputSchemaConfig).where(OutputSchemaConfig.name == name)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        changed = False
        if existing.description != description:
            existing.description = description; changed = True
        if existing.schema_fields != schema_fields:
            existing.schema_fields = schema_fields; changed = True
        if changed:
            await session.flush()
            logger.info(f"seed: synced output schema {name}")
        return existing
    schema_ = OutputSchemaConfig(
        name=name, description=description, schema_fields=schema_fields,
    )
    session.add(schema_)
    await session.flush()
    logger.info(f"seed: created output schema {name}")
    return schema_


async def ensure_seed() -> None:
    """写入默认 Researcher+Writer Crew（幂等）。"""
    async with AsyncSessionLocal() as session:
        # ── OutputSchema 模板（Week 13+）──────
        research_material_schema = await _get_or_create_schema(
            session,
            name="ResearchMaterial",
            description="结构化研究素材：包含主题、关键事实、来源和摘要",
            schema_fields=[
                {"name": "title", "type": "str", "required": True, "description": "研究主题"},
                {"name": "key_facts", "type": "list[str]", "required": True, "description": "关键事实列表"},
                {"name": "sources", "type": "list[str]", "required": False, "description": "信息来源"},
                {"name": "summary", "type": "str", "required": True, "description": "素材摘要"},
            ],
        )
        markdown_doc_schema = await _get_or_create_schema(
            session,
            name="MarkdownDocument",
            description="结构化 Markdown 文档：标题、正文和章节",
            schema_fields=[
                {"name": "title", "type": "str", "required": True, "description": "文档标题"},
                {"name": "content", "type": "str", "required": True, "description": "完整 markdown 正文"},
                {"name": "sections", "type": "list[str]", "required": False, "description": "章节标题列表"},
            ],
        )
        approval_decision_schema = await _get_or_create_schema(
            session,
            name="ApprovalDecision",
            description="审批决策：是否批准、反馈意见和质量评分",
            schema_fields=[
                {"name": "approved", "type": "bool", "required": True, "description": "是否批准"},
                {"name": "feedback", "type": "str", "required": False, "description": "审批反馈意见"},
                {"name": "quality_score", "type": "float", "required": False, "description": "质量评分 0-10"},
            ],
        )
        url_content_schema = await _get_or_create_schema(
            session,
            name="URLContent",
            description="网页抓取内容：URL、标题、正文和元数据",
            schema_fields=[
                {"name": "url", "type": "str", "required": True, "description": "网页 URL"},
                {"name": "title", "type": "str", "required": True, "description": "网页标题"},
                {"name": "main_content", "type": "str", "required": True, "description": "正文 markdown"},
                {"name": "metadata", "type": "str", "required": False, "description": "元数据（作者/日期等）"},
            ],
        )
        kb_ingest_result_schema = await _get_or_create_schema(
            session,
            name="KBIngestResult",
            description="知识库入库结果：文档 ID、分块数和来源类型",
            schema_fields=[
                {"name": "doc_id", "type": "int", "required": True, "description": "入库文档 ID"},
                {"name": "chunk_count", "type": "int", "required": True, "description": "切块数量"},
                {"name": "source_type", "type": "str", "required": True, "description": "来源类型（web/upload/manual）"},
            ],
        )
        # ── 工具 ──────
        search_tool = await _get_or_create_tool(
            session,
            name="baidu_search",
            tool_key="baidu_search",
            description="百度搜索引擎，查找网络上的信息",
        )
        intermediate_tool = await _get_or_create_tool(
            session,
            name="intermediate_save",
            tool_key="intermediate",
            description="中间结果保存工具，用于记录思考要点",
        )
        rag_tool = await _get_or_create_tool(
            session,
            name="rag_search",
            tool_key="rag_search",
            description="知识库语义检索工具，在已上传的私有文档中查找相关内容",
        )
        approval_tool = await _get_or_create_tool(
            session,
            name="human_approval",
            tool_key="human_approval",
            description="人类在环审批工具，高危操作前请求人类批准",
        )

        # Week 7: Skills
        code_review_skill = await _get_or_create_skill(
            session,
            name="代码审查",
            description="代码审查技能，让 agent 以专业代码审查者视角分析代码质量",
            prompt_template=(
                "你具备专业的代码审查能力。在审查代码时，请关注以下维度：\n"
                "1. 代码风格：命名规范、缩进、注释是否完整\n"
                "2. 潜在 Bug：空指针、越界、未处理异常、资源泄漏\n"
                "3. 性能问题：不必要的循环嵌套、N+1 查询、大对象拷贝\n"
                "4. 安全风险：SQL 注入、XSS、硬编码密钥\n"
                "5. 可维护性：函数职责单一、模块解耦、复用性\n"
                "输出格式：按严重程度分级（严重/建议/优化），每条附行号和建议方案。"
            ),
            skill_key="code_review",
        )

        # Agent
        researcher = await _get_or_create_agent(
            session,
            name="researcher",
            role="研究员",
            goal="针对用户问题，使用搜索工具或知识库检索工具获取准确、可信的信息，为撰稿人提供素材",
            backstory=(
                "你是一位严谨的信息研究员，擅长根据问题性质选择合适的检索方式："
                "涉及私有/内部资料时优先用知识库检索工具（rag_search），"
                "涉及公开网络信息时用百度搜索（search_web）。"
                "你会先用合适的工具查询关键信息，再用中间结果保存工具记录要点，"
                "最后把整理好的素材交给撰稿人。"
            ),
            tools=[search_tool, intermediate_tool, rag_tool],
            max_iter=8,
            memory=True,
        )
        writer = await _get_or_create_agent(
            session,
            name="writer",
            role="撰稿人",
            goal="基于研究员提供的素材，撰写结构清晰、语言流畅的中文回答",
            backstory=(
                "你是一位专业的中文撰稿人，擅长把零散信息整合为条理清晰、"
                "易于阅读的回答。你会根据研究员的素材组织答案，必要时分点阐述。"
            ),
            skills=[code_review_skill],
            max_iter=6,
            memory=True,
        )
        # Week 4: 纯知识库问答 Agent（仅挂 RAG 工具，用于对照测试）
        kb_agent = await _get_or_create_agent(
            session,
            name="knowledge_agent",
            role="知识库问答助手",
            goal="仅基于知识库检索结果回答用户问题，不联网",
            backstory=(
                "你是一个严格基于知识库的问答助手。"
                "收到用户问题后，先用 rag_search 工具检索知识库，"
                "再根据检索到的内容组织答案。"
                "若知识库无相关内容，明确告知用户而非编造。"
            ),
            tools=[rag_tool],
            max_iter=6,
        )
        # Week 5: 安全操作员 Agent（挂 human_approval，用于 HITL 测试）
        safety_agent = await _get_or_create_agent(
            session,
            name="safety_agent",
            role="安全操作员",
            goal="在执行高危操作前，主动调用 human_approval 工具请求人类审批",
            backstory=(
                "你是一名严谨的安全操作员。"
                "当用户要求执行可能造成数据丢失、发送外部消息、"
                "修改重要配置等不可逆操作时，"
                "你必须先调用 human_approval 工具，"
                "描述操作内容、风险等级和理由，等待人类审批后再继续。"
                "若被拒绝，向用户说明并建议替代方案。"
                "若操作是安全只读的，可直接执行。"
            ),
            tools=[approval_tool],
            max_iter=6,
        )

        # Crew
        crew = await _get_or_create_crew(
            session,
            name="researcher_writer",
            description="研究员检索 → 撰稿人撰写的顺序协作 Crew",
            agents=[researcher, writer],
        )
        kb_crew = await _get_or_create_crew(
            session,
            name="knowledge_qa",
            description="纯知识库问答 Crew（仅 RAG，不联网），用于 Week 4 对照测试",
            agents=[kb_agent],
        )
        safety_crew = await _get_or_create_crew(
            session,
            name="safety_check",
            description="HITL 安全审批 Crew（Week 5 测试），高危操作前请求人类批准",
            agents=[safety_agent],
        )

        # Tasks
        research_task = await _get_or_create_task(
            session,
            crew=crew,
            agent=researcher,
            name="research",
            description=(
                "针对用户问题进行信息检索：\n\n{user_input}\n\n"
                "要求：\n"
                "1. 判断问题属于私有知识库内容还是公开网络信息\n"
                "2. 私有内容用 rag_search 检索知识库，公开内容用 search_web 联网搜索\n"
                "3. 用中间结果保存工具记录要点\n"
                "4. 输出整理后的素材摘要，供撰稿人使用"
            ),
            expected_output="一份结构化的素材摘要，包含关键事实和来源",
            position=0,
            output_schema_id=research_material_schema.id,
        )
        await _get_or_create_task(
            session,
            crew=crew,
            agent=writer,
            name="write",
            description=(
                "基于研究员提供的素材，针对用户问题撰写最终回答：\n\n"
                "原始问题：{user_input}\n\n"
                "要求：\n"
                "1. 语言流畅、条理清晰\n"
                "2. 如有多种观点请客观呈现\n"
                "3. 用中文回答"
            ),
            expected_output="一段简洁、准确、结构清晰的中文回答",
            position=1,
            context_task_ids=[research_task.id],
        )

        # Week 4: knowledge_qa Crew 的单 Task
        await _get_or_create_task(
            session,
            crew=kb_crew,
            agent=kb_agent,
            name="kb_answer",
            description=(
                "基于知识库回答用户问题：\n\n{user_input}\n\n"
                "要求：\n"
                "1. 先调用 rag_search 工具检索知识库\n"
                "2. 仅依据检索到的内容作答，不得编造\n"
                "3. 若知识库无相关内容，明确说明\n"
                "4. 用中文回答"
            ),
            expected_output="基于知识库的准确回答，或明确告知知识库无相关内容",
            position=0,
        )

        # Week 5: safety_check Crew 的单 Task
        await _get_or_create_task(
            session,
            crew=safety_crew,
            agent=safety_agent,
            name="safety_review",
            description=(
                "处理用户请求，必要时请求人类审批：\n\n{user_input}\n\n"
                "要求：\n"
                "1. 判断操作是否属于高危（数据删除、发送外部消息、不可逆修改）\n"
                "2. 若高危，调用 human_approval 工具，说明操作内容、风险等级和理由\n"
                "3. 若被批准，向用户确认已获授权；若被拒绝，说明原因并建议替代方案\n"
                "4. 若操作安全，直接处理\n"
                "5. 用中文回答"
            ),
            expected_output="操作已获人类批准并执行，或被拒绝的说明，或安全操作的直接结果",
            position=0,
        )

        # Week 6: hierarchical 编排 Crew
        orchestrator = await _get_or_create_agent(
            session,
            name="orchestrator",
            role="团队主管",
            goal="分析用户意图，将复杂问题拆解为子任务，分配给最合适的子 agent（研究员/撰稿人/知识库助手）协作出完整回答",
            backstory=(
                "你是一位经验丰富的团队主管。收到用户问题后，你会先分析问题涉及哪些领域"
                "（公开网络信息、私有知识库、需要撰写整理），"
                "然后把检索、写作等子任务分配给对应的子 agent。"
                "你会汇总各子 agent 的产出，形成最终回答。"
            ),
            tools=[],
            max_iter=10,
        )
        team_crew = await _get_or_create_crew(
            session,
            name="team_orchestrator",
            description="层级编排 Crew：团队主管拆解任务，分配给研究员/撰稿人/知识库助手协作",
            agents=[researcher, writer, kb_agent],
            process_type="hierarchical",
            manager_agent=orchestrator,
        )
        # hierarchical 模式下 task 可绑 agent 用于 output_pydantic 注入（agent 仅作 schema 提示，manager 可覆盖分配）
        research_gather_task = await _get_or_create_task(
            session,
            crew=team_crew,
            agent=researcher,
            name="research_and_gather",
            description=(
                "针对用户问题进行信息收集：\n\n{user_input}\n\n"
                "要求：\n"
                "1. 判断问题属于私有知识库内容还是公开网络信息\n"
                "2. 私有内容用 rag_search 检索知识库，公开内容用 search_web 联网搜索\n"
                "3. 用中间结果保存工具记录要点\n"
                "4. 输出整理后的素材摘要"
            ),
            expected_output="一份结构化的素材摘要，包含关键事实和来源",
            position=0,
            output_schema_id=research_material_schema.id,
        )
        await _get_or_create_task(
            session,
            crew=team_crew,
            name="write_answer",
            description=(
                "基于收集的素材，针对用户问题撰写最终回答：\n\n"
                "原始问题：{user_input}\n\n"
                "要求：\n"
                "1. 语言流畅、条理清晰\n"
                "2. 如有多种观点请客观呈现\n"
                "3. 用中文回答"
            ),
            expected_output="一段简洁、准确、结构清晰的中文回答",
            position=1,
            context_task_ids=[research_gather_task.id],
        )

        # ── Week 10：网页内容编排入库 Crew（Orchestrator 模式）──────────────
        # 工具
        fetch_url_tool = await _get_or_create_tool(
            session,
            name="fetch_url",
            tool_key="fetch_url",
            description="网页抓取工具，获取 URL 内容并转为 markdown",
        )
        kb_ingest_tool = await _get_or_create_tool(
            session,
            name="kb_ingest",
            tool_key="kb_ingest",
            description="知识库入库工具，将 markdown 文本切块向量化后写入 pgvector",
            config_json={
                "hooks": [
                    {"key": "hitl_pre_approval", "config": {"risk_level": "medium"}}
                ]
            },
        )

        # Skill：编排主管的审批循环职责
        web_ingest_skill = await _get_or_create_skill(
            session,
            name="网页内容编排入库",
            description=(
                "网页内容编排入库技能：定义编排主管在网页抓取→撰写→审批→入库全流程中的"
                "委派职责与审批循环标准"
            ),
            prompt_template=(
                "## 你的角色\n"
                "你是编排主管（Orchestrator），不执行具体操作（没挂任何工具），"
                "唯一的动作就是通过 delegate_work_to_coworker 工具把任务委派给子 agent，"
                "然后审阅结果。\n\n"
                "## 核心原则：文件侧信道\n"
                "子 agent 之间通过文件系统传递大段内容（markdown 正文），"
                "你只需要在 context 里传**文件路径**，不需要传正文。"
                "你的 prompt 很短 → LLM 响应很快 → 整体流程飞速。\n\n"
                "## 委派 SOP（必须严格遵守）\n\n"
                "### Step 1：委派抓取\n"
                "调用 delegate_work_to_coworker，参数：\n"
                "- task: 告诉它抓取哪个 URL，它会自动保存到 outputs/raw/ 并返回摘要\n"
                "- coworker: \"网页阅读员\"\n"
                "- context: 用户原始请求（短文本，不要贴任何 markdown）\n"
                "收到回复后你会得到一个文件路径，如 outputs/raw/xxx.md。\n\n"
                "### Step 2：委派撰写\n"
                "收到阅读员的摘要（含文件路径）后，委派撰写员：\n"
                "- task: 详细描述撰写要求（去噪、结构化、保存到 outputs/web_ingest/）\n"
                "- coworker: \"内容撰写员\"\n"
                "- context: **只传文件路径**，例如「文件路径：outputs/raw/xxx.md」\n"
                "  撰写员自己会用 view_file 读取原文——你不需要传正文！\n"
                "  严禁在 context 中粘贴 markdown 正文——贴正文 = 污染你的 prompt = 卡死。\n\n"
                "### Step 3：审阅\n"
                "收到撰写员产出的结构化 markdown 后，按四个维度审阅：\n"
                "1. 结构清晰：标题层级合理\n"
                "2. 内容完整：覆盖原文关键信息\n"
                "3. 格式规范：无乱码、无 HTML 残留\n"
                "4. 可入库性：段落长度合理\n\n"
                "**不达标**：重新委派「内容撰写员」修改（重复 Step 2），"
                "在 task 里明确指出问题（如「第三章缺少代码示例」「引言段落过长需拆分」），"
                "context 中传文件路径。\n"
                "**达标**：输出「审批通过，文件路径：outputs/web_ingest/xxx.md」，进入 Step 4。\n\n"
                "### Step 4：委派入库\n"
                "审阅通过后，委派写入员入库：\n"
                "- task: 描述入库要求（name=网页标题、source_type='web'），"
                "告诉写入员用 view_file 读取 content\n"
                "- coworker: \"知识库写入员\"\n"
                "- context: **只传文件路径**，例如「文件路径：outputs/web_ingest/xxx.md」\n"
                "  写入员自己会用 view_file 读取文件——你不需要传正文！\n"
                "注意：kb_ingest 工具内置 HITL 审批 hook，调用前会自动请求人类审批。\n"
                "用户批准后实际入库，拒绝或超时则返回取消原因。\n"
                "若入库员返回失败（如 embedding 异常），向用户汇报具体错误，**不要再次委派入库**。\n\n"
                "### Step 5：汇总回报\n"
                "收到入库员返回的 doc_id 和 chunk 数后，向用户汇报最终结果。\n\n"
                "## 异常处理\n\n"
                "**关键声明：遇到任何情况，你仍然必须调用 delegate_work_to_coworker。"
                "严禁不调 delegate_work_to_coworker 而直接输出分析文字。**\n\n"
                "若子 agent 回报异常，在委派时按以下策略填写 task 参数：\n"
                "1. **阻塞性**（网络不可达/HTTP 5xx/API Key 失效/DB 故障）→ "
                "task 中指示\"向用户汇报此错误，流程终止\"\n"
                "2. **非阻塞性**（内容截断/文件不存在但可重试）→ "
                "按正常 SOP 继续委派下一步\n"
                "3. **临时性**（timeout/HTTP 503）→ 委派给同一 agent，task 中给不同策略，"
                "最多重试一次\n\n"
                "## 严禁事项\n"
                "- 你**没有** fetch_url、write_markdown_file、view_file、kb_ingest 工具\n"
                "- 严禁自己输出或改写 markdown 内容——你没有这个能力\n"
                "- 严禁跳过委派直接返回结果——每个步骤必须真正调用 delegate_work_to_coworker\n"
                "- **严禁在 context 参数中粘贴 markdown 正文**！只传文件路径！"
                "贴正文会让你的 prompt 膨胀，导致 LLM 调用 70-90 秒超慢。"
                "子 agent 自己会用 view_file 读取文件内容——你不需要也无权替它读。\n"
                "- 严禁不调用 delegate_work_to_coworker 而直接输出分析/总结"
            ),
            skill_key="web_ingest_orchestrator",
        )
        # Agents
        web_orchestrator = await _get_or_create_agent(
            session,
            name="web_orchestrator",
            role="内容编排主管",
            goal=(
                "通过 delegate_work_to_coworker 工具将网页入库任务拆解并委派给三个子 agent"
                "（网页阅读员→内容撰写员→知识库写入员），审阅产出后决定放行或退改，"
                "绝不自行为之"
            ),
            backstory=(
                "你是一个严格执行 SOP 的编排主管。\n\n"
                "## 你的能力边界\n"
                "你**没有挂载任何工具**——不能抓取网页、不能写 markdown、不能调用 kb_ingest。\n"
                "你唯一可用的动作就是 delegate_work_to_coworker：选择一个子 agent 并把任务委派给它。\n"
                "你只负责拆解→委派→审阅→路由，不负责执行。\n\n"
                "## 你的团队\n"
                "- 网页阅读员：有 fetch_url 工具（自动保存到 outputs/raw/），只返回摘要\n"
                "- 内容撰写员：有 view_file + write_markdown_file 工具，自己读取原始文件并撰写结构化文档\n"
                "- 知识库写入员：有 view_file + kb_ingest 工具，自己读取结构化文件并入库\n\n"
                "## 铁律\n"
                "1. 收到用户请求后，第一件事就是委派「网页阅读员」抓取 URL，不要自己分析/总结/回复\n"
                "2. 委派时 context 参数**只传文件路径**，严禁粘贴 markdown 正文！\n"
                "   子 agent 自己会用 view_file 读取文件——你不需要也不应该替它们读。\n"
                "3. 审阅不达标必须明确指问题点并退改，而不是自己动手修改\n"
                "4. **严禁直接输出 markdown 正文、内容摘要、或任何应由子 agent 产出的内容**\n"
                "   如果你自己输出内容，说明你在冒充子 agent——这是严重违规\n"
                "5. 你返回给用户的最终消息只能是：审阅结论、退改意见、入库结果、或流程汇总\n"
                "6. 当子 agent 向你回报错误时，你必须做出仲裁："
                "判定阻塞性/非阻塞性/临时性，给子 agent 明确指令（重试/跳过/终止），"
                "严禁不做判断就直接重新委派"
            ),
            tools=[],
            skills=[web_ingest_skill],
            max_iter=15,
            memory=False,
        )
        # 工具种子（在 agent 引用之前定义）
        markdown_writer_tool = await _get_or_create_tool(
            session,
            name="write_markdown",
            tool_key="write_markdown",
            description="Markdown 文件写入工具，将 markdown 内容保存到磁盘",
        )
        view_file_tool = await _get_or_create_tool(
            session,
            name="view_file",
            tool_key="view_file",
            description="文件查看工具，读取指定路径的文本文件内容",
        )

        web_reader = await _get_or_create_agent(
            session,
            name="web_reader",
            role="网页阅读员",
            goal="使用 fetch_url 抓取网页内容（工具自动保存到磁盘），只向主管回报摘要",
            backstory=(
                "你是一位专业的网页阅读员。\n"
                "只需做一件事：调用 fetch_url 抓取主管指定的 URL。\n"
                "fetch_url 工具会自动将完整 markdown 保存到 outputs/raw/ 目录，"
                "并返回简短摘要（标题+字数+文件路径）。\n"
                "你收到摘要后直接回报给主管即可，不需要做任何额外处理。\n"
                "严禁自己编造摘要或文件路径——必须原样转述 fetch_url 的返回值。\n"
                "fetch_url 内置双层抓取（requests → Playwright 降级），"
                "对 SPA 和反爬站均有效。若两层都失败，回报主管并说明原因。\n"
                "【故障速报】同一工具连续 2 次返回相同结果即停止，向主管汇报并等待指令。"
            ),
            tools=[fetch_url_tool],
            max_iter=5,
            memory=False,
            llm_model="qwen-turbo",
        )
        web_writer = await _get_or_create_agent(
            session,
            name="web_writer",
            role="内容撰写员",
            goal="读取原始 markdown 文件 → 撰写结构化文档 → 强制保存到 outputs/web_ingest/",
            backstory=(
                "你是一位专业的内容撰写员。必须严格按以下 4 步执行，不可跳过任何一步：\n\n"
                "Step 1 — 读取：主管委派时会在 context 中给出文件路径（如 "
                "outputs/raw/langgraph_quick_start.md），用 view_file 工具读取"
                "（max_chars=25000）。若 context 中无路径，立即回报「未收到文件路径」。\n\n"
                "Step 2 — 撰写：重新组织内容——去除冗余导航/广告/页脚，保留正文核心信息，"
                "使用合理的标题层级（# / ## / ###）、段落、列表、表格，"
                "确保段落长度合理（单段不超过 500 字便于切块入库）。\n\n"
                "🔴 Step 3 — 保存（强制！不可跳过！）：调用 write_markdown_file 工具，"
                "参数：sub_dir='web_ingest'，filename 基于原标题（如 langgraph_quick_start.md），"
                "content 为完整的结构化 markdown。\n"
                "⚠️ 这是整个流程的关键步骤！如果你不调用 write_markdown_file，"
                "后续知识库写入员将找不到文件，整个入库流程会失败。\n"
                "你必须在返回文本前先调用此工具——先保存，再回报。\n\n"
                "Step 4 — 回报：任务输出中必须包含：\n"
                "  (a) 文件路径确认（write_markdown_file 返回的路径，如 outputs/web_ingest/xxx.md）\n"
                "  (b) 结构化 markdown 全文（供主管审阅）\n\n"
                "若被编排主管指出不达标，按反馈修改后重新提交（同样必须先保存再回报）。\n"
                "【故障速报规则】同一工具调用连续 2 次返回相同错误，"
                "立即停止，向主管汇报，等待指令。"
            ),
            tools=[markdown_writer_tool, view_file_tool],
            skills=[],
            max_iter=8,
            memory=False,
            llm_model="qwen-turbo",
        )
        kb_writer = await _get_or_create_agent(
            session,
            name="kb_writer",
            role="知识库写入员",
            goal="将编排主管审批通过的 markdown 写入知识库（pgvector）",
            backstory=(
                "你是一位知识库写入员。你的工作流程：\n"
                "Step A：主管会在委派时通过 context 告诉你结构化文档的文件路径（如 "
                "outputs/web_ingest/langgraph_quick_start.md），先 view_file 读取"
                "（max_chars=25000）获取完整内容。\n"
                "Step B：调用 kb_ingest 工具，传入 name（网页标题）、"
                "content（从文件读取的完整 markdown）、source_type='web'，"
                "执行切块、向量化、入库。\n"
                "入库成功后向编排主管回报 doc_id 和 chunk 数。"
                "你不负责审阅内容质量，只执行入库操作。\n"
                "kb_ingest 工具内嵌 HITL 审批 hook，调用前会自动请求人类审批——"
                "前端弹出审批框，用户批准后才实际入库，拒绝或超时则返回取消原因。\n"
                "【故障速报规则】若 kb_ingest 连续 2 次返回相同错误，立即停止重试，"
                "向编排主管汇报具体错误原因和工具返回原文，等待指令。"
            ),
            tools=[kb_ingest_tool, view_file_tool],
            max_iter=5,
            memory=False,
        )

        # Crew：hierarchical + manager_agent
        web_ingest_crew = await _get_or_create_crew(
            session,
            name="web_ingest_crew",
            description=(
                "网页内容编排入库 Crew：编排主管拆解任务，"
                "委派阅读员抓取→撰写员撰写→主管审批循环→入库员入库"
            ),
            agents=[web_reader, web_writer, kb_writer],
            process_type="hierarchical",
            manager_agent=web_orchestrator,
        )

        # Tasks（全部 agent=None，由 manager 通过 delegate_work_to_coworker 动态委派）
        fetch_url_task = await _get_or_create_task(
            session,
            crew=web_ingest_crew,
            name="fetch_url_content",
            description=(
                "委派「网页阅读员」抓取用户提供的 URL。\n\n"
                "用户请求：{user_input}\n\n"
                "把用户请求中提到的 URL 提取出来，用 delegate_work_to_coworker 委派给"
                "「网页阅读员」（coworker=\"网页阅读员\"），在 task 参数中告诉它抓取哪个 URL。\n"
                "阅读员调用 fetch_url 后，工具会自动把完整 markdown 保存到 outputs/raw/ 目录，"
                "阅读员只需原样回报工具返回的摘要（标题+字数+文件路径）。\n"
                "把阅读员返回的摘要作为本任务输出。\n"
                "若阅读员报告抓取失败，将失败原因回报给用户，流程终止。"
            ),
            expected_output="网页阅读员返回的摘要（含文件路径），例如：「✅ 已抓取：xxx，xxx字，文件=outputs/raw/xxx.md」",
            position=0,
        )
        write_markdown_task = await _get_or_create_task(
            session,
            crew=web_ingest_crew,
            name="write_structured_markdown",
            description=(
                "委派「内容撰写员」把原始 markdown 改写为结构化、可入库的文档。\n\n"
                "用 delegate_work_to_coworker 委派给「内容撰写员」（coworker=\"内容撰写员\"），"
                "task 参数中写清楚要求（去噪、结构化、单段不超过500字）。\n"
                "context 参数中**只传文件路径**（从上一任务摘要中提取），"
                "例如：「文件路径：outputs/raw/langgraph_quick_start.md」。\n"
                "严禁在 context 中粘贴 markdown 正文，只传路径即可。\n\n"
                "🔴 重要：task 参数中必须明确要求撰写员调用 write_markdown_file "
                "保存到 outputs/web_ingest/ 子目录（sub_dir='web_ingest'），"
                "这是强制性步骤，不可省略。\n"
                "等待撰写员返回结构化 markdown 和文件路径后，把结果作为本任务输出。"
            ),
            expected_output="撰写员返回的结构化 markdown 全文 + 文件路径确认（如 outputs/web_ingest/xxx.md）",
            position=1,
            context_task_ids=[fetch_url_task.id],
        )
        await _get_or_create_task(
            session,
            crew=web_ingest_crew,
            name="review_and_approve",
            description=(
                "(这是你唯一可以直接执行的任务)。\n\n"
                "不需要委派，由你亲自审阅上一任务中撰写员产出的结构化 markdown，"
                "按以下四个维度：\n"
                "1. 结构清晰：标题层级合理、段落划分得当、列表/表格使用恰当\n"
                "2. 内容完整：覆盖原网页关键信息，无重大遗漏\n"
                "3. 格式规范：无乱码、无残留 HTML 标签\n"
                "4. 可入库性：段落长度合理（单段≤500字），切块后能产生有效语义块\n\n"
                "不达标 → 重新委派「内容撰写员」修改（重复上一步），task 中明确指出具体问题，"
                "context 中传文件路径。\n"
                "达标 → 本任务输出「审批通过，文件路径：<撰写员回报的文件路径>」。\n"
                "🔴 文件路径必须从撰写员的输出中提取，严禁自己编造或拼接文件路径。"
            ),
            expected_output=(
                "「审批通过，文件路径：outputs/web_ingest/xxx.md」（路径必须来自撰写员输出），"
                "或「不达标，具体问题：...」"
            ),
            position=2,
            context_task_ids=[write_markdown_task.id],
        )
        await _get_or_create_task(
            session,
            crew=web_ingest_crew,
            name="ingest_to_kb",
            description=(
                "委派「知识库写入员」将审批通过的 markdown 文件写入知识库。\n\n"
                "前提：上一任务 review_and_approve 必须已经是「审批通过」状态。\n"
                "用 delegate_work_to_coworker 委派给「知识库写入员」，task 中告诉它调用\n"
                "kb_ingest(name=网页标题, content=从文件读取的完整 markdown, source_type='web')。\n"
                "context 参数中**只传文件路径**（从审批结论中提取，通常是 "
                "outputs/web_ingest/xxx.md）。\n"
                "写入员自己会用 view_file 读取文件——你不需要贴原文！\n"
                "严禁在 context 中粘贴 markdown 正文，只传路径即可。\n"
                "kb_ingest 工具内嵌 HITL 审批 hook，调用时自动触发人类审批——"
                "前端弹出审批框，用户批准后才实际入库。\n"
                "等待写入员返回结果后回报给用户。"
            ),
            expected_output=(
                "入库结果：doc_id=N, chunks=N, source_type=web。"
                "提示用户可用 rag_search 检索该文档。"
            ),
            position=3,
            context_task_ids=[write_markdown_task.id],
        )

        await session.commit()
    logger.info(
        "seed: default crews ensured "
        "(researcher_writer + knowledge_qa + safety_check + team_orchestrator + web_ingest_crew)"
    )
