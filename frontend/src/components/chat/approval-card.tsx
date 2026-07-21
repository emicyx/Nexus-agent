"use client";

import { useState } from "react";
import { ShieldCheck, ShieldAlert, Check, X, Loader2 } from "lucide-react";
import type { Approval } from "@/hooks/use-chat";

const RISK_STYLES: Record<string, { color: string; label: string; bg: string }> = {
  low: { color: "text-emerald-600 border-emerald-400", label: "低风险", bg: "bg-emerald-50" },
  medium: { color: "text-amber-600 border-amber-400", label: "中风险", bg: "bg-amber-50" },
  high: { color: "text-orange-600 border-orange-400", label: "高风险", bg: "bg-orange-50" },
  critical: { color: "text-red-600 border-red-400", label: "极高风险", bg: "bg-red-50" },
};

export function ApprovalList({
  approvals,
  onResolve,
}: {
  approvals: Approval[];
  onResolve: (id: string, decision: "approve" | "reject", comment?: string) => void;
}) {
  if (approvals.length === 0) return null;
  return (
    <div className="space-y-2.5">
      {approvals.map((a) => (
        <ApprovalCard key={a.id} approval={a} onResolve={onResolve} />
      ))}
    </div>
  );
}

function ApprovalCard({
  approval,
  onResolve,
}: {
  approval: Approval;
  onResolve: (id: string, decision: "approve" | "reject", comment?: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const style = RISK_STYLES[approval.risk_level] || RISK_STYLES.medium;
  const isPending = approval.status === "PENDING";

  const handleDecision = async (decision: "approve" | "reject") => {
    setSubmitting(true);
    try {
      await onResolve(approval.id, decision, comment);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={`rounded-xl border-l-4 ${style.color} ${style.bg} p-3.5`}>
      <div className="flex items-center gap-2 mb-2">
        {isPending ? (
          <ShieldAlert size={16} className="animate-pulse text-sakura-500" />
        ) : (
          <ShieldCheck size={16} className="text-sakura-400" />
        )}
        <span className="text-sm font-semibold text-sakura-900">HITL 审批请求</span>
        <span className={`text-[10px] px-2 py-0.5 rounded-full ${style.color} ${style.bg} border`}>
          {style.label}
        </span>
        {approval.agent && (
          <span className="text-[10px] text-zinc-400">来自: {approval.agent}</span>
        )}
        {!isPending && (
          <span
            className={`ml-auto text-[10px] px-2 py-0.5 rounded-full ${
              approval.status === "APPROVED"
                ? "bg-emerald-100 text-emerald-600"
                : approval.status === "REJECTED"
                  ? "bg-red-100 text-red-600"
                  : "bg-zinc-100 text-zinc-500"
            }`}
          >
            {approval.status === "APPROVED" && "已批准"}
            {approval.status === "REJECTED" && "已拒绝"}
            {approval.status === "TIMEOUT" && "已超时"}
          </span>
        )}
      </div>

      <div className="text-sm text-zinc-700 mb-1.5">
        <span className="text-zinc-400">操作：</span>
        {approval.action}
      </div>
      {approval.reason && (
        <div className="text-xs text-zinc-400 mb-1.5">理由：{approval.reason}</div>
      )}
      {approval.comment && !isPending && (
        <div className="text-xs text-zinc-400 mb-1.5">备注：{approval.comment}</div>
      )}

      {isPending && (
        <>
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="备注（可选）"
            className="w-full rounded-lg border border-sakura-200 bg-white px-3 py-1.5 text-sm text-zinc-700 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-sakura-300 mb-2"
          />
          <div className="flex gap-2">
            <button
              onClick={() => handleDecision("approve")}
              disabled={submitting}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-emerald-500 text-white text-sm hover:bg-emerald-600 disabled:opacity-40 transition"
            >
              {submitting ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              批准
            </button>
            <button
              onClick={() => handleDecision("reject")}
              disabled={submitting}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-red-500 text-white text-sm hover:bg-red-600 disabled:opacity-40 transition"
            >
              <X size={14} />
              拒绝
            </button>
          </div>
        </>
      )}
    </div>
  );
}
