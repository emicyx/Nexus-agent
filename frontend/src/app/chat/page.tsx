"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useChat, type CollabStep, type ChatMode } from "@/hooks/use-chat";
import { MessageList } from "@/components/chat/message-list";
import { StepPanel } from "@/components/chat/step-panel";
import { AppShell } from "@/components/app-shell";
import { agentStyle } from "@/lib/agent-style";
import { commandsFromCrews, filterCommands } from "@/lib/commands";
import {
  listCrews,
  listChatSessions,
  deleteChatSession,
  getChannelsStatus,
  isQQSession,
  type CrewRead,
  type ChatSessionRead,
  type OneBotChannelStatus,
} from "@/lib/api-client";
import { Send, Square, Plus, Users, GitBranch, MessageCircle, Trash2, History, Loader2, ArrowDown, Sparkles, Terminal, Radio } from "lucide-react";

const DEV_MODE_STORAGE_KEY = "nexus:dev_mode";

export default function ChatPage() {
  const [crews, setCrews] = useState<CrewRead[]>([]);
  // v2 R0：默认 Auto 模式（路由 v0 决定 crew）；开发者模式恢复手动 crew 下拉
  const [devMode, setDevMode] = useState(false);
  const [selectedCrewId, setSelectedCrewId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<ChatSessionRead[]>([]);
  const mode: ChatMode = devMode ? "manual" : "auto";
  const autoMode = mode === "auto";

  // 恢复开发者模式偏好（构建期 SSR 无 localStorage，hydration 后再读）
  useEffect(() => {
    try {
      setDevMode(localStorage.getItem(DEV_MODE_STORAGE_KEY) === "1");
    } catch {
      // ignore
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(DEV_MODE_STORAGE_KEY, devMode ? "1" : "0");
    } catch {
      // ignore
    }
  }, [devMode]);

  const {
    messages,
    steps,
    approvals,
    isStreaming,
    error,
    errorKind,
    currentSessionUuid,
    lastRoutedCrew,
    send,
    stop,
    retry,
    newChat,
    loadSession,
    resolveApproval,
  } = useChat(selectedCrewId, { mode });
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  // 是否处于滚动容器底部（用户上滚读历史时置 false，暂停自动滚底跟随）
  const atBottomRef = useRef(true);
  // 用户离开底部时显示悬浮「回到底部」按钮
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  useEffect(() => {
    listCrews()
      .then(setCrews)
      .catch((err) => {
        console.error("加载 Crew 列表失败:", err);
        setCrews([]);
      });
  }, []);

  // S1 渠道在线状态（QQ chip）：挂载时查 + 60s 轮询；未配置渠道不渲染
  const [onebot, setOnebot] = useState<OneBotChannelStatus | null>(null);
  useEffect(() => {
    let alive = true;
    const poll = () =>
      getChannelsStatus()
        .then((s) => alive && setOnebot(s.onebot))
        .catch(() => {/* 后端不可达时保持上次状态 */});
    poll();
    const timer = setInterval(poll, 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // 会话列表：Auto 模式列出全部会话（路由的 crew 不固定）；开发者模式按所选 crew 过滤
  const refreshSessions = useCallback((m: ChatMode, crewId: number | null) => {
    if (m === "auto") {
      listChatSessions()
        .then(setSessions)
        .catch((err) => {
          console.error("加载会话列表失败:", err);
          setSessions([]);
        });
      return;
    }
    if (crewId == null) {
      setSessions([]);
      return;
    }
    listChatSessions(crewId)
      .then(setSessions)
      .catch((err) => {
        console.error("加载会话列表失败:", err);
        setSessions([]);
      });
  }, []);

  useEffect(() => {
    refreshSessions(mode, selectedCrewId);
  }, [mode, selectedCrewId, refreshSessions]);

  // 流式结束后刷新 sessions 列表（同步最新消息数/时间）
  const prevStreamingRef = useRef(false);
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      refreshSessions(mode, selectedCrewId);
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, mode, selectedCrewId, refreshSessions]);

  // 切换会话/新建对话时重置为跟随底部（先于自动滚底 effect 声明，保证切会话后回到底部）
  useEffect(() => {
    atBottomRef.current = true;
    setShowScrollToBottom(false);
  }, [currentSessionUuid]);

  // 自动滚到底部：仅当用户已在底部时跟随——流式期间上滚读历史不会被拽回
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !atBottomRef.current) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, steps, approvals, currentSessionUuid]);

  const handleMessageScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    atBottomRef.current = atBottom;
    if (atBottom) {
      setShowScrollToBottom(false);
    } else if (messages.length > 0) {
      setShowScrollToBottom(true);
    }
  };

  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = true;
    setShowScrollToBottom(false);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

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
      refreshSessions(mode, selectedCrewId);
      // 若删除的是当前 session，则清空当前对话
      if (sessions.find((s) => s.id === sessionId)?.session_uuid === currentSessionUuid) {
        newChat();
      }
    } catch (err) {
      console.error("delete session failed", err);
    }
  };

  const selectedCrew = crews.find((c) => c.id === selectedCrewId) || null;
  // 左栏信息卡与右栏 crewInfo 的数据源：
  // 开发者模式=手选 crew；Auto 模式=最近一次 routed_crew 命中的 crew
  const infoCrew = autoMode
    ? (lastRoutedCrew ? crews.find((c) => c.name === lastRoutedCrew.crew) || null : null)
    : selectedCrew;
  const crewInfo = infoCrew
    ? {
        name: infoCrew.name,
        agents: infoCrew.agents.map((a) => ({ id: a.id, name: a.name, role: a.role })),
        managerRole: infoCrew.manager_agent?.role ?? null,
      }
    : null;

  // / 命令列表（来自 crew 目录：crew 存在才展示其命令）
  const commands = useMemo(() => commandsFromCrews(crews), [crews]);
  const commandMatches = useMemo(
    () => (autoMode && !isStreaming ? filterCommands(commands, input) : []),
    [autoMode, isStreaming, commands, input],
  );

  // 左栏内容
  const leftPanel = (
    <div className="flex h-full flex-col p-3">
      {/* S1 渠道在线状态 chip：仅配置了 QQ 渠道时渲染 */}
      {onebot?.configured && (
        <div
          className={`mb-3 flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-[11px] ${
            onebot.connected
              ? "border-emerald-200 bg-emerald-50/60 text-emerald-700"
              : "border-amber-200 bg-amber-50/60 text-amber-700"
          }`}
          title={
            onebot.connected && onebot.connected_since
              ? `已连接：${new Date(onebot.connected_since * 1000).toLocaleString("zh-CN")}`
              : "NapCat 未连接（断线会自动重连）"
          }
        >
          <span className="flex items-center gap-1.5">
            <Radio size={12} />
            QQ 渠道
          </span>
          <span className={`flex items-center gap-1 ${onebot.connected ? "" : "opacity-80"}`}>
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                onebot.connected ? "animate-pulse bg-emerald-500" : "bg-amber-500"
              }`}
            />
            {onebot.connected ? "在线" : "离线"}
          </span>
        </div>
      )}
      {/* 模式区：Auto 助手模式（默认）/ 开发者模式的 Crew 选择 */}
      {autoMode ? (
        <div className="mb-3 rounded-lg border border-sakura-200 bg-sakura-50/50 p-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-sakura-700">
            <Sparkles size={13} className="text-sakura-400" />
            助手模式（Auto）
          </div>
          <p className="text-[10px] leading-relaxed text-sakura-400">
            直接提问由默认助手回答；输入 <span className="font-mono">/</span> 用命令切换专职 Crew。
          </p>
          {commands.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {commands.map((c) => (
                <button
                  key={c.command}
                  type="button"
                  onClick={() => setInput(`${c.command} `)}
                  disabled={isStreaming}
                  title={c.description}
                  className="rounded-full border border-sakura-200 bg-white px-2 py-0.5 font-mono text-[10px] text-sakura-600 transition hover:border-sakura-300 hover:bg-sakura-50 disabled:opacity-50"
                >
                  {c.command}
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
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
      )}

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
                  <div className={`flex items-center gap-1 truncate text-xs font-medium ${isActive ? "text-sakura-700" : "text-sakura-600"}`}>
                    <span className="truncate">{s.title || "新对话"}</span>
                    {isQQSession(s.session_uuid) && (
                      <span className="shrink-0 rounded bg-indigo-100 px-1 py-px text-[9px] font-medium text-indigo-600">
                        QQ
                      </span>
                    )}
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

      {/* 当前 Crew 信息卡片（开发者模式=手选；Auto=最近路由命中的 crew） */}
      {infoCrew && (
        <div className="rounded-lg border border-sakura-200 bg-sakura-50/50 p-3">
          <div className="mb-2 flex items-center gap-1.5">
            <Users size={13} className="text-sakura-400" />
            <span className="text-xs font-semibold text-sakura-700">{infoCrew.name}</span>
          </div>
          <div className="mb-1 text-[10px] text-sakura-400">
            模式: {infoCrew.process_type}
          </div>
          <div className="text-[10px] text-sakura-400">
            Agents: {infoCrew.agents.map((a) => a.name).join(", ") || "无"}
          </div>
          {infoCrew.manager_agent && (
            <div className="mt-1 text-[10px] text-sakura-400">
              主 Agent: {infoCrew.manager_agent.name}
            </div>
          )}
        </div>
      )}

      {/* 开发者模式开关 */}
      <div className="mt-3 border-t border-sakura-100 pt-3">
        <label className="flex cursor-pointer items-center gap-2 text-[11px] text-sakura-500">
          <input
            type="checkbox"
            checked={devMode}
            onChange={(e) => setDevMode(e.target.checked)}
            disabled={isStreaming}
            className="h-3.5 w-3.5 accent-sakura-400"
          />
          开发者模式（手动选择 Crew）
        </label>
      </div>

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
        {/* 消息区 — 自身即滚动容器（overflow-y-auto），滚轮可上下滑动历史 */}
        <div
          ref={scrollRef}
          onScroll={handleMessageScroll}
          className="relative flex-1 overflow-y-auto"
        >
          <MessageList
            messages={messages}
            steps={steps}
            approvals={approvals}
            isStreaming={isStreaming}
            onResolveApproval={resolveApproval}
            onExampleClick={(text) => send(text)}
            autoMode={autoMode}
          />
          {/* 悬浮「回到底部」按钮：用户离开底部读历史时出现 */}
          {showScrollToBottom && (
            <button
              onClick={scrollToBottom}
              className="absolute bottom-3 left-1/2 z-30 flex -translate-x-1/2 items-center gap-1 rounded-full border border-sakura-200 bg-white/95 px-3 py-1.5 text-xs text-sakura-600 shadow-lg backdrop-blur transition hover:bg-sakura-50"
            >
              <ArrowDown size={12} />
              回到底部
            </button>
          )}
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
            <span className="mr-3 text-sm text-red-600">⚠ {error}</span>
            <span className="flex shrink-0 items-center gap-3">
              {errorKind === "token_limit" && (
                // 上下文超限时重试无意义，引导新建对话清空上下文
                <button
                  onClick={newChat}
                  disabled={isStreaming}
                  className="text-xs text-red-600 underline transition hover:text-red-800 disabled:opacity-50"
                >
                  新建对话
                </button>
              )}
              {errorKind !== "budget_exceeded" ? (
                <button
                  onClick={retry}
                  disabled={isStreaming}
                  className="text-xs text-red-500 underline transition hover:text-red-700 disabled:opacity-50"
                >
                  重试
                </button>
              ) : (
                // 日预算熔断（budget_exceeded）：今日额度已用完，重试无意义，提示明日再试
                <span className="text-xs text-red-400">今日额度已用完，请明日再试</span>
              )}
            </span>
          </div>
        )}

        {/* / 命令面板：Auto 模式下输入以 / 开头时唤起（来自 crew 目录） */}
        {commandMatches.length > 0 && (
          <div className="max-h-44 overflow-y-auto border-t border-sakura-200 bg-white px-3 py-2">
            {commandMatches.map((c) => (
              <button
                key={c.command}
                type="button"
                onClick={() => setInput(`${c.command} `)}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition hover:bg-sakura-50"
              >
                <Terminal size={12} className="shrink-0 text-sakura-400" />
                <span className="shrink-0 font-mono text-xs font-medium text-sakura-600">{c.command}</span>
                <span className="truncate text-[11px] text-sakura-400">{c.description}</span>
              </button>
            ))}
          </div>
        )}

        {/* 输入区 */}
        <form onSubmit={handleSubmit} className="flex gap-2 border-t border-sakura-200 bg-white/80 p-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              autoMode
                ? "输入问题，或输入 / 使用命令（/kb /write…）"
                : "输入你的问题..."
            }
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
