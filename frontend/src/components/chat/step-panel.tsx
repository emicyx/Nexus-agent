"use client";

import { type CollabStep } from "@/hooks/use-chat";
import {
  Brain,
  Search,
  Wrench,
  CheckCircle,
  Loader2,
  Sparkles,
  Crown,
} from "lucide-react";

/** Agent 颜色映射 */
const AGENT_STYLES: Record<string, { color: string; label: string }> = {
  研究员: { color: "text-emerald-600 border-emerald-400", label: "研究员" },
  撰稿人: { color: "text-sky-600 border-sky-400", label: "撰稿人" },
  Crew: { color: "text-amber-600 border-amber-400", label: "Crew" },
  团队主管: { color: "text-sakura-600 border-sakura-400", label: "团队主管" },
};

function agentStyle(agent?: string) {
  return (
    AGENT_STYLES[agent || ""] || {
      color: "text-sakura-600 border-sakura-400",
      label: agent || "Agent",
    }
  );
}

export function StepPanel({
  steps,
  isStreaming,
  crewInfo,
}: {
  steps: CollabStep[];
  isStreaming: boolean;
  crewInfo?: { name: string; agents: { id: number; name: string; role: string }[] } | null;
}) {
  const thinkingCount = steps.filter((s) => s.kind === "thinking").length;
  const toolCalls = steps.filter((s) => s.kind === "tool_call");
  const toolDone = toolCalls.filter((s) => !s.pending).length;

  return (
    <div className="flex h-full flex-col">
      {/* 面板头部 */}
      <div className="flex items-center gap-2 border-b border-sakura-200 px-4 py-2.5">
        <Brain size={15} className="text-sakura-500" />
        <span className="text-sm font-semibold text-sakura-900">协作步骤流</span>
        {steps.length > 0 && (
          <span className="text-xs text-sakura-400">
            · 思考 {thinkingCount} · 工具 {toolDone}/{toolCalls.length}
          </span>
        )}
        {isStreaming && (
          <span className="ml-auto flex items-center gap-1 text-xs text-emerald-500">
            <Loader2 size={12} className="animate-spin" />
            执行中
          </span>
        )}
      </div>

      {/* 面板内容 */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        {steps.length === 0 ? (
          /* 空状态 */
          <div className="flex h-full flex-col items-center justify-center gap-3 py-8 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-sakura-100">
              <Sparkles size={20} className="text-sakura-400" />
            </div>
            <div className="text-sm text-sakura-400">Agent 待命中</div>
            {crewInfo && crewInfo.agents.length > 0 && (
              <div className="mt-2 w-full space-y-1.5">
                <div className="text-xs font-medium text-sakura-300">
                  当前 Crew: {crewInfo.name}
                </div>
                {crewInfo.agents.map((a) => (
                  <div
                    key={a.id}
                    className="flex items-center gap-2 rounded-lg bg-sakura-50 px-2.5 py-1.5"
                  >
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-sakura-300 to-sakura-500 text-xs text-white">
                      {a.name.charAt(0)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium text-sakura-700">
                        {a.name}
                      </div>
                      <div className="truncate text-[10px] text-sakura-400">
                        {a.role}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          /* 步骤列表 */
          <div className="space-y-1.5">
            {steps.map((s, i) => {
              const isLatest =
                isStreaming &&
                i === steps.length - 1 &&
                (s.kind === "thinking" || s.pending);
              return (
                <StepCard key={s.id} step={s} isLatest={isLatest} />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function StepCard({ step, isLatest }: { step: CollabStep; isLatest?: boolean }) {
  const style = agentStyle(step.agent);
  const isManager = step.agent === "团队主管";
  const breathClass = isLatest ? " animate-breath" : "";

  if (step.kind === "tool_call") {
    return (
      <div className={`rounded-lg border-l-2 ${style.color} bg-sakura-50/60 pl-3 pr-2 py-1.5${breathClass}`}>
        <div className="flex items-center gap-1.5">
          {step.pending ? (
            <Loader2 size={12} className="animate-spin text-sakura-400" />
          ) : (
            <Wrench size={12} className="text-zinc-400" />
          )}
          <span className="text-xs font-medium">{style.label}</span>
          <span className="text-xs text-zinc-400">调用</span>
          <span className="text-xs font-medium text-sakura-600">{step.tool}</span>
          {step.pending && (
            <span className="ml-auto text-[10px] text-sakura-400 animate-pulse">
              执行中...
            </span>
          )}
        </div>
        {step.input && (
          <div className="mt-0.5 text-[11px] text-zinc-400 line-clamp-2">
            {step.input}
          </div>
        )}
      </div>
    );
  }

  if (step.kind === "tool_result") {
    return (
      <div className={`rounded-lg border-l-2 ${style.color} bg-sakura-50/60 pl-3 pr-2 py-1.5${breathClass}`}>
        <div className="flex items-center gap-1.5">
          <CheckCircle size={12} className="text-emerald-500" />
          <span className="text-xs font-medium">{style.label}</span>
          <span className="text-xs text-zinc-400">工具</span>
          <span className="text-xs font-medium text-sakura-600">{step.tool}</span>
          <span className="text-xs text-emerald-500">返回结果</span>
        </div>
        {step.output && (
          <div className="mt-0.5 text-[11px] text-zinc-400 line-clamp-3">
            {step.output}
          </div>
        )}
      </div>
    );
  }

  // thinking — manager 决策用 Crown 图标 + 高亮背景
  if (isManager) {
    return (
      <div className={`rounded-lg border-l-[3px] border-sakura-500 bg-sakura-100/80 pl-3 pr-2 py-1.5${breathClass}`}>
        <div className="flex items-center gap-1.5">
          <Crown size={13} className="text-sakura-600" />
          <span className="text-xs font-semibold text-sakura-700">{style.label}</span>
          <span className="text-[10px] text-sakura-400">决策</span>
          {step.step != null && (
            <span className="text-[10px] text-sakura-400">Step {step.step}</span>
          )}
        </div>
        <div className="mt-0.5 text-[11px] text-sakura-600 line-clamp-4">
          {step.content}
        </div>
      </div>
    );
  }

  // sub-agent thinking
  return (
    <div className={`rounded-lg border-l-2 ${style.color} bg-sakura-50/60 pl-3 pr-2 py-1.5${breathClass}`}>
      <div className="flex items-center gap-1.5">
        <Search size={12} className="text-sakura-400" />
        <span className="text-xs font-medium">{style.label}</span>
        {step.step != null && (
          <span className="text-[10px] text-zinc-400">Step {step.step}</span>
        )}
      </div>
      <div className="mt-0.5 text-[11px] text-zinc-500 line-clamp-4">
        {step.content}
      </div>
    </div>
  );
}
