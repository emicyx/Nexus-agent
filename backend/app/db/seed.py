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
                "## 委派 SOP（必须严格遵守）\n\n"
                "### Step 1：委派抓取\n"
                "调用 delegate_work_to_coworker，参数：\n"
                "- task: 描述要抓取的 URL 和格式要求\n"
                "- coworker: \"网页阅读员\"\n"
                "- context: 用户原始请求\n\n"
                "### Step 2：委派撰写\n"
                "收到网页阅读员返回的 markdown 后，立即委派撰写员：\n"
                "- task: 详细描述撰写要求（去噪、结构化、保存到 outputs/web_ingest/）\n"
                "- coworker: \"内容撰写员\"\n"
                "- context: 【关键】把网页阅读员返回的 markdown **完整粘贴**在这里，一字不改。\n"
                "  子 agent 看不到你的会话历史，只认 context 字段。若 context 里没有完整原文，撰写员会拒绝工作。\n\n"
                "### Step 3：审阅\n"
                "收到撰写员产出的 markdown 后，按四个维度审阅：\n"
                "1. 结构清晰：标题层级合理\n"
                "2. 内容完整：覆盖原文关键信息\n"
                "3. 格式规范：无乱码、无 HTML 残留\n"
                "4. 可入库性：段落长度合理\n\n"
                "**不达标**：重新委派「内容撰写员」修改（重复 Step 2），"
                "在 task 里明确指出问题（如「第三章缺少代码示例」「引言段落过长需拆分」）。\n"
                "**达标**：进入 Step 4。\n\n"
                "### Step 4：委派入库\n"
                "审阅通过后，委派写入员入库：\n"
                "- task: 描述入库要求（name=网页标题、content=最终 markdown、source_type='web'）\n"
                "- coworker: \"知识库写入员\"\n"
                "- context: 最终 markdown **完整粘贴**\n"
                "注意：kb_ingest 工具内置 HITL 审批 hook，调用前会自动请求人类审批。\n"
                "用户批准后实际入库，拒绝或超时则返回取消原因。\n\n"
                "### Step 5：汇总回报\n"
                "收到入库员返回的 doc_id 和 chunk 数后，向用户汇报最终结果。\n\n"
                "## 严禁事项\n"
                "- 你**没有** fetch_url、write_markdown_file、kb_ingest 工具，调用不了\n"
                "- 严禁自己输出或改写 markdown 内容——你没有这个能力\n"
                "- 严禁跳过委派直接返回结果——每个步骤必须真正调用 delegate_work_to_coworker\n"
                "- 严禁在 context 参数中总结、压缩、改写上游产出——必须完整原文粘贴"
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
                "- 网页阅读员：有 fetch_url 工具，能抓取任意 URL 并返回 markdown\n"
                "- 内容撰写员：有 write_markdown_file 工具，能撰写结构化 markdown 并保存到磁盘\n"
                "- 知识库写入员：有 kb_ingest 工具，能将 markdown 向量化入库\n\n"
                "## 铁律\n"
                "1. 收到用户请求后，第一件事就是委派「网页阅读员」抓取 URL，不要自己分析/总结/回复\n"
                "2. 委派时 context 参数必须**完整粘贴**上游产出的原文，一字不改\n"
                "3. 审阅不达标必须明确指问题点并退改，而不是自己动手修改\n"
                "4. **严禁直接输出 markdown 正文、内容摘要、或任何应由子 agent 产出的内容**\n"
                "   如果你自己输出内容，说明你在冒充子 agent——这是严重违规\n"
                "5. 你返回给用户的最终消息只能是：审阅结论、退改意见、入库结果、或流程汇总"
            ),
            tools=[],
            skills=[web_ingest_skill],
            max_iter=20,
            memory=True,
        )
        web_reader = await _get_or_create_agent(
            session,
            name="web_reader",
            role="网页阅读员",
            goal="使用 fetch_url 工具抓取指定 URL 的网页内容并转为 markdown，"
            "为撰写员提供原始素材",
            backstory=(
                "你是一位专业的网页阅读员。收到 URL 后，你会调用 fetch_url 工具"
                "抓取页面内容并转为 markdown，确保抓取成功、内容完整。"
                "fetch_url 工具内置双层抓取：先 requests 快速抓静态 HTML，"
                "若返回内容过短或命中反爬关键词（登录墙/验证码），"
                "自动降级到 Playwright 浏览器渲染 JS 后再抓。"
                "对于 SPA（docsify/vuepress/hash 路由）和知乎等反爬站点都能拿到正文。"
                "若两层均失败，立即回报编排主管并说明原因，"
                "不自行编造内容。"
            ),
            tools=[fetch_url_tool],
            max_iter=5,
            memory=False,
        )
        # write_markdown 工具种子（用于内容撰写员保存 markdown 到磁盘）
        markdown_writer_tool = await _get_or_create_tool(
            session,
            name="write_markdown",
            tool_key="write_markdown",
            description="Markdown 文件写入工具，将 markdown 内容保存到磁盘",
        )

        web_writer = await _get_or_create_agent(
            session,
            name="web_writer",
            role="内容撰写员",
            goal="基于网页阅读员抓取的 markdown，撰写结构化、可入库的高质量 markdown 文档",
            backstory=(
                "你是一位专业的内容撰写员。基于网页阅读员抓取的原始 markdown，"
                "你会重新组织内容：去除冗余导航/广告/页脚，"
                "保留正文核心信息，"
                "使用合理的标题层级（# / ## / ###）、段落、列表、表格，"
                "确保段落长度合理（建议单段不超过 500 字便于切块入库）。"
                "撰写完成后用 write_markdown_file 工具保存到 outputs/web_ingest/ 子目录"
                "（调用时传 sub_dir='web_ingest'），"
                "并把完整 markdown 文本作为任务输出返回，供编排主管审阅。"
                "若被编排主管指出不达标，按反馈修改后重新提交。\n"
                "【关键】你看到的原始 markdown 是编排主管在委派你时通过 "
                "delegate_work_to_coworker 的 context 参数传给你的——"
                "请从 context 中完整读取 markdown 原文，不要假设它来自其他渠道。"
                "若 context 中没有 markdown 原文（只有摘要或为空），"
                "立即回报编排主管\"未收到原始内容\"，不自行编造。"
            ),
            tools=[markdown_writer_tool],
            skills=[web_ingest_skill],
            max_iter=8,
            memory=True,
        )
        kb_writer = await _get_or_create_agent(
            session,
            name="kb_writer",
            role="知识库写入员",
            goal="将编排主管审批通过的 markdown 写入知识库（pgvector）",
            backstory=(
                "你是一位知识库写入员。当编排主管审批通过 markdown 后，"
                "你会调用 kb_ingest 工具，传入 name（网页标题）、"
                "content（最终 markdown）、source_type='web'，"
                "执行切块、向量化、入库。"
                "入库成功后向编排主管回报 doc_id 和 chunk 数。"
                "你不负责审阅内容质量，只执行入库操作。\n"
                "【关键】你要入库的 markdown 原文是编排主管在委派你时通过 "
                "delegate_work_to_coworker 的 context 参数传给你的——"
                "请从 context 中完整读取 markdown 原文作为 kb_ingest 的 content 参数。"
                "若 context 中没有完整 markdown（只有摘要或审批结论），"
                "立即回报编排主管\"未收到完整入库内容\"，不自行编造或截取。"
            ),
            tools=[kb_ingest_tool],
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
                "委派「网页阅读员」抓取用户提供的 URL 并转为 markdown。\n\n"
                "用户请求：{user_input}\n\n"
                "把用户请求中提到的 URL 提取出来，用 delegate_work_to_coworker 委派给"
                "「网页阅读员」（coworker=\"网页阅读员\"），在 task 参数中告诉它抓取哪个 URL，"
                "在 context 参数中粘贴用户原始请求。\n"
                "等待网页阅读员返回 markdown 后，把结果作为本任务输出（不要自己改写）。\n"
                "若阅读员报告抓取失败，将失败原因回报给用户，流程终止。"
            ),
            expected_output="网页阅读员返回的原始 markdown 文本（完整原文，不总结不压缩）",
            position=0,
        )
        write_markdown_task = await _get_or_create_task(
            session,
            crew=web_ingest_crew,
            name="write_structured_markdown",
            description=(
                "委派「内容撰写员」把上一任务的 markdown 改写为结构化、可入库的文档。\n\n"
                "用 delegate_work_to_coworker 委派给「内容撰写员」（coworker=\"内容撰写员\"），"
                "task 参数中写清楚要求（去噪、结构化、单段不超过500字、"
                "用 write_markdown_file 保存到 outputs/web_ingest/），"
                "context 参数中**完整粘贴**上游 fetch_url_content 返回的 markdown 原文。\n"
                "等待撰写员返回结构化 markdown 后，把结果作为本任务输出。\n"
                "严禁你自己输出 markdown 内容——你没有撰写能力，必须委派。"
            ),
            expected_output="内容撰写员产出的结构化 markdown 文档（完整原文）",
            position=1,
            context_task_ids=[fetch_url_task.id],
        )
        await _get_or_create_task(
            session,
            crew=web_ingest_crew,
            name="review_and_approve",
            description=(
                "(这是你唯一可以直接执行的任务)。\n\n"
                "不需要委派，由你亲自按以下四个维度审阅：\n"
                "1. 结构清晰：标题层级合理、段落划分得当、列表/表格使用恰当\n"
                "2. 内容完整：覆盖原网页关键信息，无重大遗漏\n"
                "3. 格式规范：无乱码、无残留 HTML 标签\n"
                "4. 可入库性：段落长度合理（单段≤500字），切块后能产生有效语义块\n\n"
                "不达标 → 重新委派「内容撰写员」修改（重复上一步），task 中明确指出具体问题\n"
                "达标 → 本任务输出\"审批通过\"+ 最终 markdown，进入下一步"
            ),
            expected_output=(
                "「审批通过」+ 最终 markdown 原文，或「不达标，具体问题：...」"
            ),
            position=2,
            context_task_ids=[write_markdown_task.id],
        )
        await _get_or_create_task(
            session,
            crew=web_ingest_crew,
            name="ingest_to_kb",
            description=(
                "委派「知识库写入员」将审批通过的 markdown 写入知识库。\n\n"
                "前提：上一任务 review_and_approve 必须已经是「审批通过」状态。\n"
                "用 delegate_work_to_coworker 委派给「知识库写入员」，task 中告诉它调用\n"
                "kb_ingest(name=网页标题, content=最终 markdown, source_type='web')。\n"
                "context 参数中**完整粘贴**审批通过的最终 markdown 原文。\n"
                "kb_ingest 工具内嵌 HITL 审批 hook，调用时自动触发人类审批——"
                "前端弹出审批框，用户批准后才执行入库，拒绝或超时则返回取消原因。\n"
                "等待写入员返回结果后回报给用户。\n"
                "严禁你自己输出 doc_id 或 chunk 数——必须等写入员真实返回。"
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
