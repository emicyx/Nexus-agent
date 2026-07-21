"use client";

import { useState } from "react";
import type { AgentRead, OutputSchemaRead, TaskRead, TaskCreate } from "@/lib/api-client";
import { Trash2 } from "lucide-react";

interface Props {
  task?: TaskRead;
  allAgents: AgentRead[];
  allSchemas: OutputSchemaRead[];
  siblingTasks: TaskRead[];
  onSubmit: (payload: TaskCreate) => void;
  onCancel: () => void;
  onDelete?: () => void;
}

export function TaskForm({ task, allAgents, allSchemas, siblingTasks, onSubmit, onCancel, onDelete }: Props) {
  const isEdit = !!task;
  const [name, setName] = useState(task?.name ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [expectedOutput, setExpectedOutput] = useState(task?.expected_output ?? "");
  const [agentId, setAgentId] = useState<number | null>(task?.agent_id ?? null);
  const [outputSchemaId, setOutputSchemaId] = useState<number | null>(task?.output_schema_id ?? null);
  const [position, setPosition] = useState(task?.position ?? 0);
  const [contextIds, setContextIds] = useState<number[]>(task?.context_task_ids ?? []);
  const [error, setError] = useState<string | null>(null);

  const toggleContext = (id: number) => {
    setContextIds((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]
    );
  };

  const handleSubmit = () => {
    if (!name.trim() || !description.trim()) {
      setError("名称和描述不能为空");
      return;
    }
    setError(null);
    onSubmit({
      name,
      description,
      expected_output: expectedOutput,
      agent_id: agentId,
      output_schema_id: outputSchemaId,
      position,
      context_task_ids: contextIds.length > 0 ? contextIds : null,
    });
  };

  const inputCls =
    "w-full rounded-lg border border-sakura-200 bg-white px-3 py-2 text-sm text-zinc-700 placeholder-sakura-300 focus:outline-none focus:ring-2 focus:ring-sakura-300";

  return (
    <div className="space-y-3">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
          ⚠ {error}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <div className="col-span-2">
          <label className="mb-1 block text-xs font-medium text-sakura-400">名称</label>
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="research" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-sakura-400">position</label>
          <input type="number" value={position} onChange={(e) => setPosition(parseInt(e.target.value) || 0)} className={inputCls} />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">描述（支持 {"{user_input}"} 占位符）</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} rows={4} />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">期望输出 (expected_output)</label>
        <textarea value={expectedOutput} onChange={(e) => setExpectedOutput(e.target.value)} className={inputCls} rows={2} />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">执行 Agent</label>
        <select
          value={agentId ?? 0}
          onChange={(e) => setAgentId(e.target.value ? Number(e.target.value) : null)}
          className={inputCls}
        >
          <option value={0}>（由主 Agent 动态分配 — 仅 hierarchical）</option>
          {allAgents.map((a) => (
            <option key={a.id} value={a.id}>{a.name} ({a.role})</option>
          ))}
        </select>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">输出 Schema（可选）</label>
        <select
          value={outputSchemaId ?? 0}
          onChange={(e) => setOutputSchemaId(e.target.value ? Number(e.target.value) : null)}
          className={inputCls}
        >
          <option value={0}>无（自由文本输出）</option>
          {allSchemas.map((s) => (
            <option key={s.id} value={s.id}>{s.name} — {s.description.slice(0, 40)}{s.description.length > 40 ? "..." : ""}</option>
          ))}
        </select>
      </div>

      {siblingTasks.length > 0 && (
        <div>
          <label className="mb-1 block text-xs font-medium text-sakura-400">Context 依赖（上游 Task 的输出作为本 Task 上下文）</label>
          <div className="space-y-1">
            {siblingTasks.map((t) => (
              <label
                key={t.id}
                className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm transition cursor-pointer ${
                  contextIds.includes(t.id)
                    ? "border-sakura-300 bg-sakura-50 text-sakura-700"
                    : "border-sakura-100 text-zinc-500 hover:bg-sakura-50/50"
                }`}
              >
                <input
                  type="checkbox"
                  checked={contextIds.includes(t.id)}
                  onChange={() => toggleContext(t.id)}
                  className="rounded border-sakura-300 text-sakura-500 focus:ring-sakura-300"
                />
                <span>{t.name}</span>
                <span className="text-xs text-sakura-300">#{t.position + 1}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={handleSubmit}
          disabled={!name || !description}
          className="rounded-lg bg-gradient-to-r from-sakura-400 to-sakura-500 px-3 py-1.5 text-sm font-medium text-white transition hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-40"
        >
          {isEdit ? "保存修改" : "创建"}
        </button>
        <button
          onClick={onCancel}
          className="rounded-lg bg-white border border-sakura-200 px-3 py-1.5 text-sm text-zinc-500 hover:bg-sakura-50"
        >
          取消
        </button>
        {isEdit && onDelete && (
          <button
            onClick={onDelete}
            className="ml-auto flex items-center gap-1 rounded-lg bg-red-50 px-3 py-1.5 text-xs text-red-500 hover:bg-red-100"
          >
            <Trash2 size={12} />
            删除
          </button>
        )}
      </div>
    </div>
  );
}
