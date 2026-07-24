import { type Message, type Approval, type CollabStep } from "@/hooks/use-chat";
import { agentStyle } from "@/lib/agent-style";
import { Brain, User, Sparkles, Send, Loader2, Cpu } from "lucide-react";
import { ApprovalList } from "./approval-card";

export function MessageList({
  messages,
  steps,
  approvals,
  isStreaming,
  onResolveApproval,
  onExampleClick,
}: {
  messages: Message[];
  steps: CollabStep[];
  approvals?: Approval[];
  isStreaming: boolean;
  onResolveApproval?: (id: string, decision: "approve" | "reject", comment?: string) => void;
  onExampleClick?: (text: string) => void;
}) {
  // 空状态：欢迎卡片 + 示例问题
  if (messages.length === 0) {
    return <EmptyState onExampleClick={onExampleClick} />;
  }

  const lastThinking = steps.filter((s) => s.kind === "thinking" || s.kind === "thinking_streaming").pop();

  return (
    <div className="flex-1 overflow-y-auto space-y-3 px-4 py-4">
      {/* HITL 审批卡片 */}
      {approvals && approvals.length > 0 && onResolveApproval && (
        <ApprovalList approvals={approvals} onResolve={onResolveApproval} />
      )}

      {messages.map((msg, i) => (
        <MessageBubble
          key={i}
          message={msg}
          isStreaming={isStreaming && i === messages.length - 1}
        />
      ))}

      {/* 思考过程气泡 — SSE 流式实时更新 + 呼吸灯 */}
      {isStreaming && lastThinking && (
        <ThinkingBubble step={lastThinking} />
      )}
    </div>
  );
}

function MessageBubble({ message, isStreaming }: { message: Message; isStreaming: boolean }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex gap-2.5 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full ${
          isUser
            ? "bg-gradient-to-br from-sakura-400 to-sakura-600"
            : "bg-gradient-to-br from-sakura-200 to-sakura-300"
        }`}
      >
        {isUser ? (
          <User size={14} className="text-white" />
        ) : (
          <Brain size={14} className="text-white" />
        )}
      </div>
      <div
        className={`max-w-[75%] rounded-xl px-3.5 py-2 ${
          isUser
            ? "bg-gradient-to-br from-sakura-100 to-sakura-200 text-sakura-900"
            : "bg-white text-zinc-700 border border-sakura-200 shadow-sm"
        }`}
      >
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
          {message.content || (isStreaming ? (
            <span className="flex items-center gap-1.5 text-sakura-400">
              <span className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sakura-400 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sakura-400 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sakura-400" />
              </span>
              <span className="text-xs">Agent 思考中</span>
            </span>
          ) : (
            ""
          ))}
        </p>
      </div>
    </div>
  );
}

function ThinkingBubble({ step }: { step: CollabStep }) {
  const style = agentStyle(step.agent);

  return (
    <div className="flex gap-2.5">
      {/* Agent 头像 */}
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-sakura-300 to-sakura-400">
        <Cpu size={13} className="text-white" />
      </div>

      {/* 思考内容卡片 + 呼吸灯 */}
      <div className="max-w-[75%] rounded-xl border-2 bg-sakura-50/80 px-3.5 py-2.5 animate-breath">
        {/* Agent 角色 + 状态指示 */}
        <div className="mb-1.5 flex items-center gap-1.5">
          <span className={`text-[11px] font-semibold ${style.color}`}>
            {style.icon} {style.label}
          </span>
          <span className="flex items-center gap-1 text-[10px] text-sakura-400">
            <span className="h-2 w-2 rounded-full animate-breath-dot" />
            思考中
          </span>
          {step.step != null && (
            <span className="ml-auto text-[10px] text-sakura-300">
              Step {step.step}
            </span>
          )}
        </div>

        {/* 思考内容 — 流式实时更新 */}
        <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-sakura-700 line-clamp-6">
          {step.content}
        </p>
      </div>
    </div>
  );
}

const EXAMPLES = [
  "1+1等于几？",
  "帮我搜索一下今天的新闻",
  "年假有几天？",
  "写一段关于 AI 的短文",
];

function EmptyState({ onExampleClick }: { onExampleClick?: (text: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-5 px-6 py-8">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-sakura-300 to-sakura-500">
        <Sparkles size={24} className="text-white" />
      </div>
      <div className="text-center">
        <h2 className="text-lg font-bold text-sakura-900">你好！我是你的 AI Agent 助手</h2>
        <p className="mt-1 text-sm text-sakura-400">选择一个 Crew，输入问题开始对话</p>
      </div>
      <div className="grid w-full max-w-md grid-cols-2 gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => onExampleClick?.(ex)}
            className="flex items-center gap-2 rounded-xl border border-sakura-200 bg-white px-3 py-2 text-left text-xs text-sakura-600 transition hover:border-sakura-300 hover:bg-sakura-50"
          >
            <Send size={12} className="text-sakura-400" />
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
