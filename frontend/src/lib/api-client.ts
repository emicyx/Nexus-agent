export type ChatEvent =
  | { type: "agent_thinking"; content: string; step: number; agent?: string }
  | { type: "thinking_token"; content: string; step?: number; agent?: string }
  | { type: "tool_call"; agent: string; tool: string; input?: string }
  | { type: "tool_result"; agent: string; tool: string; output?: string }
  | {
      type: "approval_requested";
      content: string;
      agent?: string;
      tool?: string;
      input?: {
        approval_id: string;
        action: string;
        risk_level: string;
        reason?: string;
        timeout?: number;
      };
    }
  | { type: "token"; content: string }
  | { type: "final_answer"; content: string }
  | { type: "task_completed"; content: string; agent: string; output?: { task_name: string; agent: string; output_format: string; pydantic_valid: boolean; raw_preview: string } }
  | { type: "delegation"; content: string; agent: string; input?: { task: string; context: string; coworker: string } }
  | { type: "error"; content: string }
  | { type: "done" };

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

/**
 * 发送聊天消息并通过 SSE 流式接收 Agent 事件。
 * 浏览器 EventSource 不支持 POST，所以用 fetch + ReadableStream 手动解析 SSE。
 */
export async function* streamChat(
  message: string,
  signal?: AbortSignal,
  opts: { crewId?: number; single?: boolean; sessionId?: string } = {},
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${API_BASE}/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      crew_id: opts.crewId ?? null,
      single: opts.single ?? false,
      session_id: opts.sessionId ?? null,
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE 事件以 \n\n 分隔
    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);

      // 解析 event: 和 data: 行
      const eventMatch = /event: (.+)/.exec(rawEvent);
      const dataMatch = /data: (.+)/s.exec(rawEvent);

      const eventType = eventMatch?.[1]?.trim() ?? "message";
      const dataStr = dataMatch?.[1]?.trim() ?? "{}";

      try {
        const payload = JSON.parse(dataStr);
        yield { type: eventType, ...payload } as ChatEvent;
      } catch {
        // skip malformed
      }
    }
  }
}

// ==================== 配置中心 REST API ====================

export interface ToolRead {
  id: number;
  name: string;
  tool_key: string;
  description: string;
  config_json: Record<string, unknown> | null;
}

export interface ToolCreate {
  name: string;
  tool_key: string;
  description?: string;
  config_json?: Record<string, unknown> | null;
}

// ── OutputSchema ──
export interface OutputSchemaRead {
  id: number;
  name: string;
  description: string;
  schema_fields: { name: string; type: string; required: boolean; description: string }[];
}
export interface OutputSchemaCreate {
  name: string;
  description?: string;
  schema_fields: { name: string; type: string; required: boolean; description: string }[];
}

export const listOutputSchemas = () => jsonRequest<OutputSchemaRead[]>(`${API_BASE}/v1/schemas`);
export const createOutputSchema = (payload: OutputSchemaCreate) =>
  jsonRequest<OutputSchemaRead>(`${API_BASE}/v1/schemas`, { method: "POST", body: JSON.stringify(payload) });
export const updateOutputSchema = (id: number, payload: Partial<OutputSchemaCreate>) =>
  jsonRequest<OutputSchemaRead>(`${API_BASE}/v1/schemas/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteOutputSchema = (id: number) =>
  jsonRequest<void>(`${API_BASE}/v1/schemas/${id}`, { method: "DELETE" });

// Week 7: Skill 类型
export interface SkillRead {
  id: number;
  name: string;
  description: string;
  prompt_template: string;
  skill_key: string | null;
  config_json: Record<string, unknown> | null;
}

export interface SkillCreate {
  name: string;
  description?: string;
  prompt_template: string;
  skill_key?: string | null;
  config_json?: Record<string, unknown> | null;
}

export interface AgentRead {
  id: number;
  name: string;
  role: string;
  goal: string;
  backstory: string;
  llm_model: string | null;
  temperature: number | null;
  max_iter: number;
  memory: boolean;
  tools: ToolRead[];
  skills: SkillRead[];
}

export interface AgentCreate {
  name: string;
  role: string;
  goal: string;
  backstory: string;
  llm_model?: string | null;
  temperature?: number | null;
  max_iter?: number;
  memory?: boolean;
  tool_ids?: number[];
  skill_ids?: number[];
}

export interface CrewCreate {
  name: string;
  description?: string;
  process_type?: string; // "sequential" | "hierarchical"
  agent_ids?: number[];
  manager_agent_id?: number | null;
}
export interface CrewRead {
  id: number;
  name: string;
  description: string;
  process_type: string;
  manager_agent_id?: number | null;
  manager_agent?: AgentRead | null;
  agents: AgentRead[];
  tasks?: TaskRead[];
}

async function jsonRequest<T>(url: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

// ---- Agent ----
export const listAgents = () => jsonRequest<AgentRead[]>(`${API_BASE}/v1/agents`);
export const getAgent = (id: number) => jsonRequest<AgentRead>(`${API_BASE}/v1/agents/${id}`);
export const createAgent = (payload: AgentCreate) =>
  jsonRequest<AgentRead>(`${API_BASE}/v1/agents`, { method: "POST", body: JSON.stringify(payload) });
export const updateAgent = (id: number, payload: Partial<AgentCreate>) =>
  jsonRequest<AgentRead>(`${API_BASE}/v1/agents/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteAgent = (id: number) =>
  jsonRequest<void>(`${API_BASE}/v1/agents/${id}`, { method: "DELETE" });
export const setAgentTools = (id: number, toolIds: number[]) =>
  jsonRequest<AgentRead>(`${API_BASE}/v1/agents/${id}/tools`, { method: "POST", body: JSON.stringify(toolIds) });

// ---- Tool ----
export const listTools = () => jsonRequest<ToolRead[]>(`${API_BASE}/v1/tools`);
export const createTool = (payload: ToolCreate) =>
  jsonRequest<ToolRead>(`${API_BASE}/v1/tools`, { method: "POST", body: JSON.stringify(payload) });
export const updateTool = (id: number, payload: Partial<ToolCreate>) =>
  jsonRequest<ToolRead>(`${API_BASE}/v1/tools/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteTool = (id: number) =>
  jsonRequest<void>(`${API_BASE}/v1/tools/${id}`, { method: "DELETE" });

// ---- Skill (Week 7) ----
export const listSkills = () => jsonRequest<SkillRead[]>(`${API_BASE}/v1/skills`);
export const createSkill = (payload: SkillCreate) =>
  jsonRequest<SkillRead>(`${API_BASE}/v1/skills`, { method: "POST", body: JSON.stringify(payload) });
export const updateSkill = (id: number, payload: Partial<SkillCreate>) =>
  jsonRequest<SkillRead>(`${API_BASE}/v1/skills/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteSkill = (id: number) =>
  jsonRequest<void>(`${API_BASE}/v1/skills/${id}`, { method: "DELETE" });
export const setAgentSkills = (id: number, skillIds: number[]) =>
  jsonRequest<AgentRead>(`${API_BASE}/v1/agents/${id}/skills`, { method: "POST", body: JSON.stringify(skillIds) });

// ---- Crew ----
export const listCrews = () => jsonRequest<CrewRead[]>(`${API_BASE}/v1/crews`);
export const getCrew = (id: number) => jsonRequest<CrewRead>(`${API_BASE}/v1/crews/${id}`);
export const createCrew = (payload: CrewCreate) =>
  jsonRequest<CrewRead>(`${API_BASE}/v1/crews`, { method: "POST", body: JSON.stringify(payload) });
export const updateCrew = (id: number, payload: Partial<CrewCreate>) =>
  jsonRequest<CrewRead>(`${API_BASE}/v1/crews/${id}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteCrew = (id: number) =>
  jsonRequest<void>(`${API_BASE}/v1/crews/${id}`, { method: "DELETE" });

// ---- Task（Crew 子资源）----
export interface TaskRead {
  id: number;
  crew_id: number;
  agent_id: number | null;
  name: string;
  description: string;
  expected_output: string;
  position: number;
  context_task_ids: number[] | null;
  output_schema_id: number | null;
}
export interface TaskCreate {
  name: string;
  description: string;
  expected_output?: string;
  agent_id: number | null;
  position?: number;
  context_task_ids?: number[] | null;
  output_schema_id?: number | null;
}
export type TaskUpdate = Partial<TaskCreate>;
export const listTasks = (crewId: number) =>
  jsonRequest<TaskRead[]>(`${API_BASE}/v1/crews/${crewId}/tasks`);
export const createTask = (crewId: number, payload: TaskCreate) =>
  jsonRequest<TaskRead>(`${API_BASE}/v1/crews/${crewId}/tasks`, { method: "POST", body: JSON.stringify(payload) });
export const updateTask = (taskId: number, payload: TaskUpdate) =>
  jsonRequest<TaskRead>(`${API_BASE}/v1/crews/tasks/${taskId}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteTask = (taskId: number) =>
  jsonRequest<void>(`${API_BASE}/v1/crews/tasks/${taskId}`, { method: "DELETE" });

// ---- Approval（HITL Week 5）----
export interface ApprovalRead {
  approval_id: string;
  status: string; // PENDING / APPROVED / REJECTED / TIMEOUT
  action: string;
  risk_level: string;
  reason?: string;
  agent_role?: string;
  comment?: string;
  created_at: number;
  resolved_at?: number | null;
  timeout?: number;
}
export const submitApproval = (approvalId: string, decision: "approve" | "reject", comment = "") =>
  jsonRequest<ApprovalRead>(`${API_BASE}/v1/approvals/${approvalId}`, {
    method: "POST",
    body: JSON.stringify({ decision, comment }),
  });
export const getApproval = (approvalId: string) =>
  jsonRequest<ApprovalRead>(`${API_BASE}/v1/approvals/${approvalId}`);

// ---- Document (Week 4 RAG) ----
export interface DocumentRead {
  id: number;
  name: string;
  source_type: string;
  chunk_count: number;
  created_at: string;
}

export interface SearchResult {
  content: string;
  document_name: string;
  position: number;
  score: number;
}

export const listDocuments = () =>
  jsonRequest<DocumentRead[]>(`${API_BASE}/v1/documents`);

export const createDocument = (payload: { name: string; content: string; source_type?: string }) =>
  jsonRequest<DocumentRead>(`${API_BASE}/v1/documents`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export async function uploadDocumentFile(file: File, name?: string): Promise<DocumentRead> {
  const form = new FormData();
  form.append("file", file);
  if (name) form.append("name", name);
  const res = await fetch(`${API_BASE}/v1/documents/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return (await res.json()) as DocumentRead;
}

export const deleteDocument = (id: number) =>
  jsonRequest<void>(`${API_BASE}/v1/documents/${id}`, { method: "DELETE" });

export const searchDocuments = (q: string, topK = 5, documentId?: number) => {
  const params = new URLSearchParams({ q, top_k: String(topK) });
  if (documentId) params.set("document_id", String(documentId));
  return jsonRequest<SearchResult[]>(
    `${API_BASE}/v1/documents/search?${params.toString()}`,
  );
};

// ==================== Chat Sessions（Week 11+ 持久化对话历史） ====================

export interface ChatMessageRead {
  id: number;
  role: string; // "user" | "assistant"
  content: string;
  created_at: string;
}

export interface ChatSessionRead {
  id: number;
  crew_id: number;
  session_uuid: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_at: string | null;
}

export interface ChatSessionDetail extends ChatSessionRead {
  messages: ChatMessageRead[];
}

export const listChatSessions = (crewId?: number) => {
  const params = crewId != null ? `?crew_id=${crewId}` : "";
  return jsonRequest<ChatSessionRead[]>(`${API_BASE}/v1/chat/sessions${params}`);
};

export const getChatSession = (id: number) =>
  jsonRequest<ChatSessionDetail>(`${API_BASE}/v1/chat/sessions/${id}`);

export const createChatSession = (payload: {
  crew_id: number;
  session_uuid: string;
  title?: string;
}) =>
  jsonRequest<ChatSessionRead>(`${API_BASE}/v1/chat/sessions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateChatSession = (id: number, payload: { title: string }) =>
  jsonRequest<ChatSessionRead>(`${API_BASE}/v1/chat/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteChatSession = (id: number) =>
  jsonRequest<void>(`${API_BASE}/v1/chat/sessions/${id}`, { method: "DELETE" });

