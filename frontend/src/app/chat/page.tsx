"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useChat, type CollabStep } from "@/hooks/use-chat";
import { MessageList } from "@/components/chat/message-list";
import { StepPanel } from "@/components/chat/step-panel";
import { AppShell } from "@/components/app-shell";
import { agentStyle } from "@/lib/agent-style";
import {
  listCrews,
  listChatSessions,
  deleteChatSession,
  type CrewRead,
  type ChatSessionRead,
} from "@/lib/api-client";
import { Send, Square, Plus, Users, GitBranch, MessageCircle, Trash2, History, Loader2 } from "lucide-react";

export default function ChatPage() {
  const [crews, setCrews] = useState<CrewRead[]>([]);
  const [selectedCrewId, setSelectedCrewId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<ChatSessionRead[]>([]);
  const {
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
  } = useChat(selectedCrewId);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listCrews()
      .then(setCrews)
      .catch(() => {});
  }, []);

  // 切换 crew 时加载该 crew 的 sessions 列表
  const refreshSessions = useCallback((crewId: number | null) => {
    if (crewId == null) {
      setSessions([]);
      return;
    }
    listChatSessions(crewId)
      .then(setSessions)
      .catch(() => setSessions([]));
  }, []);

  useEffect(() => {
    refreshSessions(selectedCrewId);
  }, [selectedCrewId, refreshSessions]);

  // 流式结束后刷新 sessions 列表（同步最新消息数/时间）
  const prevStreamingRef = useRef(false);
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      refreshSessions(selectedCrewId);
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, selectedCrewId, refreshSessions]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, steps]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim()) {
      send(input);
      setInput("");
    }
  };

  const handleNewChat = () => {
    newChat();
  };

  const handleLoadSession = (s: ChatSessionRead) => {
    if (isStreaming) return;
    loadSession(s.id, s.session_uuid);
  };

  const handleDeleteSession = async (e: React.MouseEvent, sessionId: number) => {
    e.stopPropagation();
    if (isStreaming) return;
    try {
      await deleteChatSession(sessionId);
      refreshSessions(selectedCrewId);
      // 若删除的是当前 session，则清空当前对话
      if (sessions.find((s) => s.id === sessionId)?.session_uuid === currentSessionUuid) {
        newChat();
      }
    } catch (err) {
      console.error("delete session failed", err);
    }
  };

  const selectedCrew = crews.find((c) => c.id === selectedCrewId) || null;
  const crewInfo = selectedCrew
    ? {
        name: selectedCrew.name,
        agents: selectedCrew.agents.map((a) => ({ id: a.id, name: a.name, role: a.role })),
      }
    : null;

  // 左栏内容
  const leftPanel = (
    <div className="flex h-full flex-col p-3">
      {/* Crew 选择 */}
      <div className="mb-3">
        <label className="mb-1 flex items-center gap-1 text-xs font-medium text-sakura-400">
          <GitBranch size={12} />
          Crew 选择
        </label>
        <select
          value={selectedCrewId ?? 0}
          onChange={(e) => setSelectedCrewId(e.target.value ? Number(e.target.value) : null)}
          disabled={isStreaming}
          className="w-full rounded-lg border border-sakura-200 bg-white px-2.5 py-1.5 text-sm text-sakura-700 focus:outline-none focus:ring-2 focus:ring-sakura-300 disabled:opacity-50"
        >
          <option value={0}>默认 Crew</option>
          {crews.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.process_type})
            </option>
          ))}
        </select>
      </div>

      {/* 新建对话 */}
      <button
        onClick={handleNewChat}
        disabled={isStreaming}
        className="mb-3 flex items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-sakura-400 to-sakura-500 px-3 py-2 text-sm font-medium text-white transition hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-50"
      >
        <Plus size={14} />
        新建对话
      </button>

      {/* 历史会话列表 */}
      <div className="mb-3 flex items-center gap-1 text-xs font-medium text-sakura-400">
        <History size={12} />
        历史对话
      </div>
      <div className="mb-3 flex-1 overflow-y-auto space-y-1 pr-1">
        {sessions.length === 0 ? (
          <div className="py-4 text-center text-[11px] text-sakura-300">
            暂无历史对话
          </div>
        ) : (
          sessions.map((s) => {
            const isActive = s.session_uuid === currentSessionUuid;
            return (
              <div
                key={s.id}
                onClick={() => handleLoadSession(s)}
                className={`group flex cursor-pointer items-start justify-between gap-2 rounded-lg border px-2.5 py-2 text-left transition ${
                  isActive
                    ? "border-sakura-400 bg-sakura-50"
                    : "border-sakura-100 bg-white hover:border-sakura-200 hover:bg-sakura-50/50"
                } ${isStreaming ? "pointer-events-none opacity-60" : ""}`}
              >
                <div className="min-w-0 flex-1">
                  <div className={`truncate text-xs font-medium ${isActive ? "text-sakura-700" : "text-sakura-600"}`}>
                    {s.title || "新对话"}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[10px] text-sakura-400">
                    <span>{s.message_count} 条</span>
                    {s.last_message_at && (
                      <span>
                        {new Date(s.last_message_at).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={(e) => handleDeleteSession(e, s.id)}
                  disabled={isStreaming}
                  className="shrink-0 rounded p-1 text-sakura-300 opacity-0 transition hover:bg-red-50 hover:text-red-500 group-hover:opacity-100 disabled:opacity-0"
                  title="删除对话"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* 当前 Crew 信息卡片 */}
      {selectedCrew && (
        <div className="rounded-lg border border-sakura-200 bg-sakura-50/50 p-3">
          <div className="mb-2 flex items-center gap-1.5">
            <Users size={13} className="text-sakura-400" />
            <span className="text-xs font-semibold text-sakura-700">{selectedCrew.name}</span>
          </div>
          <div className="mb-1 text-[10px] text-sakura-400">
            模式: {selectedCrew.process_type}
          </div>
          <div className="text-[10px] text-sakura-400">
            Agents: {selectedCrew.agents.map((a) => a.name).join(", ") || "无"}
          </div>
          {selectedCrew.manager_agent && (
            <div className="mt-1 text-[10px] text-sakura-400">
              主 Agent: {selectedCrew.manager_agent.name}
            </div>
          )}
        </div>
      )}

      {/* 底部统计 */}
      <div className="mt-auto space-y-1 border-t border-sakura-100 pt-3">
        <div className="flex items-center justify-between text-[10px] text-sakura-300">
          <span className="flex items-center gap-1">
            <MessageCircle size={10} />
            消息
          </span>
          <span>{messages.length}</span>
        </div>
        <div className="flex items-center justify-between text-[10px] text-sakura-300">
          <span>步骤</span>
          <span>{steps.length}</span>
        </div>
      </div>
    </div>
  );

  // 活跃 Agent 状态：从最后一个 pending/streaming step 推导
  const activeAgent = useMemo(() => {
    if (!isStreaming || steps.length === 0) return null;
    // 找最后一个 thinking_streaming 或 pending tool_call
    for (let i = steps.length - 1; i >= 0; i--) {
      const s = steps[i];
      if (s.kind === "thinking_streaming") {
        return { agent: s.agent, step: s.step, status: "思考中" };
      }
      if (s.kind === "tool_call" && s.pending) {
        return { agent: s.agent, step: s.step, status: `正在调用 ${s.tool || "工具"}` };
      }
      if (s.kind === "thinking") {
        return { agent: s.agent, step: s.step, status: "思考完成" };
      }
    }
    return null;
  }, [steps, isStreaming]);

  // 右栏内容
  const rightPanel = (
    <StepPanel steps={steps} isStreaming={isStreaming} crewInfo={crewInfo} />
  );

  return (
    <AppShell leftPanel={leftPanel} rightPanel={rightPanel}>
      <div className="flex h-full flex-col">
        {/* 消息区 */}
        <div ref={scrollRef} className="flex-1 overflow-hidden">
          <MessageList
            messages={messages}
            steps={steps}
            approvals={approvals}
            isStreaming={isStreaming}
            onResolveApproval={resolveApproval}
            onExampleClick={(text) => send(text)}
          />
        </div>

        {/* 活跃 Agent 状态条 */}
        {activeAgent && (
          <div className="flex items-center gap-2 border-t border-sakura-100 bg-sakura-50/50 px-4 py-1.5">
            <Loader2 size={12} className="animate-spin text-sakura-400" />
            <span className="text-xs text-sakura-500">
              {agentStyle(activeAgent.agent ?? undefined).icon}{" "}
              {activeAgent.agent || "Agent"}
              {activeAgent.step != null && ` · Step ${activeAgent.step}`}
            </span>
            <span className="text-xs text-sakura-300">· {activeAgent.status}</span>
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="flex items-center justify-between border-t border-red-200 bg-red-50 px-4 py-2">
            <span className="text-sm text-red-600">⚠ {error}</span>
            <button
              onClick={retry}
              disabled={isStreaming}
              className="text-xs text-red-500 underline transition hover:text-red-700 disabled:opacity-50"
            >
              重试
            </button>
          </div>
        )}

        {/* 输入区 */}
        <form onSubmit={handleSubmit} className="flex gap-2 border-t border-sakura-200 bg-white/80 p-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入你的问题..."
            disabled={isStreaming}
            className="flex-1 rounded-xl border border-sakura-200 bg-white px-4 py-2 text-sm text-zinc-700 placeholder-sakura-300 focus:outline-none focus:ring-2 focus:ring-sakura-300 disabled:opacity-50"
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={stop}
              className="rounded-xl bg-red-500 px-4 py-2 text-white transition hover:bg-red-600"
            >
              <Square size={18} />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="rounded-xl bg-gradient-to-r from-sakura-400 to-sakura-500 px-4 py-2 text-white transition hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-30"
            >
              <Send size={18} />
            </button>
          )}
        </form>
      </div>
    </AppShell>
  );
}
