"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import {
  streamChat,
  submitApproval,
  getChatSession,
  type ChatEvent,
} from "@/lib/api-client";

function generateSessionId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const SESSION_STORAGE_PREFIX = "nexus:session_uuid:";

function loadStoredSessionUuid(crewId: number | null): string | null {
  if (crewId == null) return null;
  try {
    const key = `${SESSION_STORAGE_PREFIX}${crewId}`;
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeSessionUuid(crewId: number | null, uuid: string) {
  if (crewId == null) return;
  try {
    localStorage.setItem(`${SESSION_STORAGE_PREFIX}${crewId}`, uuid);
  } catch {
    // localStorage 不可用（隐私模式）忽略
  }
}

function clearStoredSessionUuid(crewId: number | null) {
  if (crewId == null) return;
  try {
    localStorage.removeItem(`${SESSION_STORAGE_PREFIX}${crewId}`);
  } catch {
    // ignore
  }
}

export interface Message {
  role: "user" | "assistant";
  content: string;
}

/** 协作步骤：可以是 Agent 思考、流式思考 token、工具调用或工具结果 */
export interface CollabStep {
  id: number;
  kind: "thinking" | "thinking_streaming" | "tool_call" | "tool_result";
  agent?: string;
  content: string;
  tool?: string;
  input?: string;
  output?: string;
  step?: number;
  pending?: boolean;
}

export interface Approval {
  id: string;
  action: string;
  risk_level: string;
  reason?: string;
  agent?: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "TIMEOUT";
  comment?: string;
}

export function useChat(crewId?: number | null) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [steps, setSteps] = useState<CollabStep[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentSessionUuid, setCurrentSessionUuid] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string>(generateSessionId());
  const lastMessageRef = useRef<string>("");

  // crew 切换时：从 localStorage 恢复该 crew 的最后 session_uuid，并加载历史消息
  useEffect(() => {
    if (crewId == null) {
      setMessages([]);
      setSteps([]);
      setApprovals([]);
      setError(null);
      setCurrentSessionUuid(null);
      sessionIdRef.current = generateSessionId();
      return;
    }
    const storedUuid = loadStoredSessionUuid(crewId);
    if (storedUuid) {
      sessionIdRef.current = storedUuid;
      setCurrentSessionUuid(storedUuid);
      // 加载历史消息（失败则清空，保留 uuid 等首条消息时由后端 lazy 创建）
      getChatSessionByIdUuid(storedUuid)
        .then((msgs) => {
          setMessages(msgs);
        })
        .catch(() => {
          // session 不存在（可能 DB 已清）→ 重置
          sessionIdRef.current = generateSessionId();
          setCurrentSessionUuid(null);
          clearStoredSessionUuid(crewId);
          setMessages([]);
        });
    } else {
      sessionIdRef.current = generateSessionId();
      setCurrentSessionUuid(null);
      setMessages([]);
    }
    setSteps([]);
    setApprovals([]);
    setError(null);
  }, [crewId]);

  const send = useCallback(async (text: string) => {
    if (!text.trim() || isStreaming) return;

    setError(null);
    setSteps([]);
    setApprovals([]);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);
    lastMessageRef.current = text;

    const controller = new AbortController();
    abortRef.current = controller;
    setIsStreaming(true);

    try {
      let assistantText = "";
      const collab: CollabStep[] = [];
      let stepId = 0;

      const pushStep = (s: Omit<CollabStep, "id">) => {
        collab.push({ ...s, id: ++stepId });
        setSteps([...collab]);
      };

      for await (const evt of streamChat(text, controller.signal, {
        crewId: crewId ?? undefined,
        sessionId: sessionIdRef.current,
      })) {
        switch (evt.type) {
          case "agent_thinking":
            // 如果之前有同 agent+step 的流式 thinking，将其标记为完成
            for (let i = collab.length - 1; i >= 0; i--) {
              if (
                collab[i].kind === "thinking_streaming" &&
                collab[i].agent === evt.agent &&
                collab[i].step === evt.step
              ) {
                collab[i].kind = "thinking";
                break;
              }
            }
            // 整块 thinking 作为最终版本入库
            pushStep({
              kind: "thinking",
              agent: evt.agent,
              content: evt.content,
              step: evt.step,
            });
            break;
          case "thinking_token":
            // 流式 token：找到同 agent+step 的 thinking_streaming step，追加 token
            {
              const lastStreaming = (() => {
                for (let i = collab.length - 1; i >= 0; i--) {
                  if (
                    collab[i].kind === "thinking_streaming" &&
                    collab[i].agent === evt.agent &&
                    collab[i].step === evt.step
                  ) {
                    return collab[i];
                  }
                }
                return null;
              })();
              if (lastStreaming) {
                lastStreaming.content += evt.content;
                setSteps([...collab]);
              } else {
                pushStep({
                  kind: "thinking_streaming",
                  agent: evt.agent,
                  content: evt.content,
                  step: evt.step,
                  pending: true,
                });
              }
            }
            break;
          case "tool_call":
            pushStep({
              kind: "tool_call",
              agent: evt.agent,
              tool: evt.tool,
              input: evt.input,
              content: `调用工具 ${evt.tool}`,
              pending: true,
            });
            break;
          case "tool_result":
            for (let i = collab.length - 1; i >= 0; i--) {
              if (
                collab[i].kind === "tool_call" &&
                collab[i].tool === evt.tool &&
                collab[i].pending
              ) {
                collab[i].pending = false;
                break;
              }
            }
            pushStep({
              kind: "tool_result",
              agent: evt.agent,
              tool: evt.tool,
              output: evt.output,
              content: `工具 ${evt.tool} 返回结果`,
            });
            break;
          case "approval_requested": {
            const info = evt.input || {
              approval_id: "",
              action: evt.content,
              risk_level: "medium",
              reason: "",
              timeout: 150,
            };
            setApprovals((prev) => [
              ...prev,
              {
                id: info.approval_id,
                action: info.action,
                risk_level: info.risk_level,
                reason: info.reason,
                agent: evt.agent,
                status: "PENDING" as const,
              },
            ]);
            pushStep({
              kind: "tool_call",
              agent: evt.agent,
              tool: "human_approval",
              input: `等待审批: ${info.action}`,
              content: `请求人类审批: ${info.action}`,
              pending: true,
            });
            break;
          }
          case "token":
            assistantText += evt.content;
            setMessages((prev) => {
              const next = [...prev];
              next[next.length - 1] = { role: "assistant", content: assistantText };
              return next;
            });
            break;
          case "final_answer":
            assistantText = evt.content;
            setMessages((prev) => {
              const next = [...prev];
              next[next.length - 1] = { role: "assistant", content: assistantText };
              return next;
            });
            break;
          case "error":
            setError(evt.content);
            break;
          case "done":
            break;
        }
      }
      // 首条消息发送成功后，session_uuid 才真正落到 DB。这里持久化到 localStorage。
      if (crewId != null) {
        storeSessionUuid(crewId, sessionIdRef.current);
        setCurrentSessionUuid(sessionIdRef.current);
      }
    } catch (err) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [isStreaming, crewId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const retry = useCallback(() => {
    if (isStreaming || !lastMessageRef.current) return;
    setMessages((prev) => {
      if (prev.length >= 2 && prev[prev.length - 1].role === "assistant" && !prev[prev.length - 1].content) {
        return prev.slice(0, -2);
      }
      return prev;
    });
    setTimeout(() => send(lastMessageRef.current), 50);
  }, [isStreaming, send]);

  /** 最新的思考步骤（流式或完整，供 MessageList 实时展示）。 */
  const latestThinking = useMemo(
    () => steps.filter((s) => s.kind === "thinking" || s.kind === "thinking_streaming").pop() ?? null,
    [steps],
  );

  /** 新建对话：生成新 uuid，清空当前 messages（DB 不动）。 */
  const newChat = useCallback(() => {
    setMessages([]);
    setSteps([]);
    setApprovals([]);
    setError(null);
    sessionIdRef.current = generateSessionId();
    setCurrentSessionUuid(null);
    // 注意：不清除 localStorage，等用户真正发消息后再覆盖
  }, []);

  /**
   * 切换到某个历史 session：加载 messages 并设为当前。
   * @param sessionId DB session id
   * @param sessionUuid DB session_uuid（前端用作 sessionIdRef）
   */
  const loadSession = useCallback(
    async (sessionId: number, sessionUuid: string) => {
      if (isStreaming) return;
      setSteps([]);
      setApprovals([]);
      setError(null);
      try {
        const detail = await getChatSession(sessionId);
        setMessages(
          detail.messages.map((m) => ({
            role: m.role as "user" | "assistant",
            content: m.content,
          })),
        );
        sessionIdRef.current = sessionUuid;
        setCurrentSessionUuid(sessionUuid);
        if (crewId != null) {
          storeSessionUuid(crewId, sessionUuid);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [isStreaming, crewId],
  );

  const resolveApproval = useCallback(
    async (approvalId: string, decision: "approve" | "reject", comment = "") => {
      try {
        const result = await submitApproval(approvalId, decision, comment);
        setApprovals((prev) =>
          prev.map((a) =>
            a.id === approvalId
              ? {
                  ...a,
                  status: result.status as Approval["status"],
                  comment: result.comment || comment,
                }
              : a,
          ),
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  return {
    messages,
    steps,
    approvals,
    isStreaming,
    error,
    currentSessionUuid,
    latestThinking,
    send,
    stop,
    retry,
    newChat,
    loadSession,
    resolveApproval,
  };
}

/**
 * 通过 session_uuid 加载历史消息。
 * 注意：API 是 GET /v1/chat/sessions/{id}（int），但前端只有 uuid。
 * 解决：先调用 listSessions 过滤匹配的 uuid 拿 id。
 * 简化：调用 /v1/chat/sessions?crew_id=X 拿到列表，找到匹配 uuid。
 */
async function getChatSessionByIdUuid(uuid: string): Promise<Message[]> {
  // 复用 listChatSessions，但这里我们不知道 crewId，扫全部。
  // 为简化：调用 /v1/chat/sessions 不带 crew_id 过滤。
  const { listChatSessions } = await import("@/lib/api-client");
  const sessions = await listChatSessions();
  const found = sessions.find((s) => s.session_uuid === uuid);
  if (!found) {
    throw new Error("session not found");
  }
  const { getChatSession } = await import("@/lib/api-client");
  const detail = await getChatSession(found.id);
  return detail.messages.map((m) => ({
    role: m.role as "user" | "assistant",
    content: m.content,
  }));
}
