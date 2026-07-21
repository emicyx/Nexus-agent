"use client";

import { useEffect, useState } from "react";
import {
  createCrew,
  updateCrew,
  deleteCrew,
  listOutputSchemas,
  listTasks,
  createTask,
  updateTask,
  deleteTask,
  type CrewRead,
  type AgentRead,
  type OutputSchemaRead,
  type TaskRead,
  type TaskCreate,
} from "@/lib/api-client";
import { TaskForm } from "./task-form";
import { Trash2, Save, Plus, ChevronDown, ChevronUp } from "lucide-react";

interface Props {
  crew?: CrewRead;
  allAgents: AgentRead[];
  onSaved: () => void;
  onDeleted: () => void;
}

export function CrewForm({ crew, allAgents, onSaved, onDeleted }: Props) {
  const isEdit = !!crew;
  const [name, setName] = useState(crew?.name ?? "");
  const [description, setDescription] = useState(crew?.description ?? "");
  const [processType, setProcessType] = useState(crew?.process_type ?? "sequential");
  const [managerAgentId, setManagerAgentId] = useState<number | null>(
    crew?.manager_agent_id ?? null
  );
  const [agentIds, setAgentIds] = useState<number[]>(
    crew?.agents.map((a) => a.id) ?? []
  );
  const [tasks, setTasks] = useState<TaskRead[]>([]);
  const [schemas, setSchemas] = useState<OutputSchemaRead[]>([]);
  const [editingTask, setEditingTask] = useState<number | null>(null);
  const [isAddingTask, setIsAddingTask] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reloadSchemas = async () => {
    try {
      setSchemas(await listOutputSchemas());
    } catch (_) { /* non-critical */ }
  };

  const reloadTasks = async () => {
    if (!crew) return;
    try {
      setTasks(await listTasks(crew.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    setEditingTask(null);
    setIsAddingTask(false);
    reloadTasks();
    reloadSchemas();
  }, [crew?.id]);

  const toggleAgent = (id: number) => {
    setAgentIds((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]
    );
  };

  const moveAgent = (idx: number, dir: -1 | 1) => {
    setAgentIds((prev) => {
      const next = [...prev];
      const target = idx + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name,
        description,
        process_type: processType,
        agent_ids: agentIds,
        manager_agent_id: processType === "hierarchical" ? managerAgentId : null,
      };
      if (isEdit && crew) {
        await updateCrew(crew.id, payload);
      } else {
        await createCrew(payload);
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!crew) return;
    if (!confirm(`确认删除 Crew "${crew.name}"？所有关联 Task 也会删除。`)) return;
    try {
      await deleteCrew(crew.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleCreateTask = async (payload: TaskCreate) => {
    if (!crew) return;
    try {
      await createTask(crew.id, payload);
      setIsAddingTask(false);
      await reloadTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleUpdateTask = async (taskId: number, payload: Partial<TaskCreate>) => {
    try {
      await updateTask(taskId, payload);
      setEditingTask(null);
      await reloadTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleDeleteTask = async (taskId: number) => {
    if (!confirm("确认删除此 Task？")) return;
    try {
      await deleteTask(taskId);
      setEditingTask(null);
      await reloadTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const inputCls =
    "w-full rounded-lg border border-sakura-200 bg-white px-3 py-2 text-sm text-zinc-700 placeholder-sakura-300 focus:outline-none focus:ring-2 focus:ring-sakura-300";

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-bold text-sakura-900">{isEdit ? "编辑 Crew" : "新建 Crew"}</h2>
        {isEdit && (
          <button
            onClick={handleDelete}
            className="ml-auto flex items-center gap-1 rounded-lg bg-red-50 px-3 py-1 text-xs text-red-500 hover:bg-red-100"
          >
            <Trash2 size={12} />
            删除
          </button>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
          ⚠ {error}
        </div>
      )}

      <Field label="名称 (唯一标识)">
        <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="researcher_writer" />
      </Field>

      <Field label="描述">
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} rows={2} />
      </Field>

      <Field label="协作模式 (process_type)">
        <select value={processType} onChange={(e) => setProcessType(e.target.value)} className={inputCls}>
          <option value="sequential">sequential - 顺序执行（按 Task position）</option>
          <option value="hierarchical">hierarchical - 层级编排（需主 Agent，Week 6）</option>
        </select>
      </Field>

      {processType === "hierarchical" && (
        <Field label="主 Agent (manager_agent，层级编排时负责拆解和分配任务)">
          <select
            value={managerAgentId ?? 0}
            onChange={(e) => setManagerAgentId(e.target.value ? Number(e.target.value) : null)}
            className={inputCls}
          >
            <option value={0}>（未选择）</option>
            {allAgents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.role})
              </option>
            ))}
          </select>
          <div className="mt-1 text-xs text-sakura-300">
            主 Agent 不应同时出现在下方成员 Agents 列表中
          </div>
        </Field>
      )}

      <Field label={`成员 Agents（按顺序，共 ${agentIds.length} 个）`}>
        {allAgents.length === 0 ? (
          <div className="text-xs text-sakura-300">无可用 Agent，先到 Agents 标签创建</div>
        ) : (
          <div className="space-y-1.5">
            {allAgents.map((a) => {
              const idx = agentIds.indexOf(a.id);
              const selected = idx >= 0;
              return (
                <div
                  key={a.id}
                  className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition ${
                    selected
                      ? "border-sakura-300 bg-sakura-50 text-sakura-700"
                      : "border-sakura-100 text-zinc-500 hover:bg-sakura-50/50"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => toggleAgent(a.id)}
                    className="rounded border-sakura-300 text-sakura-500 focus:ring-sakura-300"
                  />
                  <span className="font-medium">{a.name}</span>
                  <span className="text-xs text-sakura-300">({a.role})</span>
                  {selected && (
                    <>
                      <span className="ml-2 text-xs text-sakura-400">#{idx + 1}</span>
                      <button
                        onClick={() => moveAgent(idx, -1)}
                        disabled={idx === 0}
                        className="rounded p-1 text-sakura-400 hover:bg-sakura-100 disabled:opacity-30"
                      >
                        <ChevronUp size={12} />
                      </button>
                      <button
                        onClick={() => moveAgent(idx, 1)}
                        disabled={idx === agentIds.length - 1}
                        className="rounded p-1 text-sakura-400 hover:bg-sakura-100 disabled:opacity-30"
                      >
                        <ChevronDown size={12} />
                      </button>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Field>

      <div className="flex items-center gap-2 pt-2">
        <button
          onClick={handleSave}
          disabled={saving || !name}
          className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-sakura-400 to-sakura-500 px-4 py-2 text-sm font-medium text-white transition hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-40"
        >
          <Save size={14} />
          {saving ? "保存中..." : isEdit ? "保存修改" : "创建"}
        </button>
      </div>

      {/* Tasks 子资源 */}
      {isEdit && crew && (
        <div className="border-t border-sakura-100 pt-4">
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-sm font-semibold text-sakura-700">Tasks（{tasks.length}）</h3>
            <button
              onClick={() => { setIsAddingTask(true); setEditingTask(null); }}
              className="flex items-center gap-0.5 rounded-lg bg-sakura-100 px-2 py-1 text-xs text-sakura-600 hover:bg-sakura-200"
            >
              <Plus size={10} />
              新建 Task
            </button>
          </div>

          {isAddingTask && (
            <div className="mb-3 rounded-lg border border-sakura-200 bg-sakura-50/30 p-3">
              <h4 className="mb-2 text-xs font-medium text-sakura-400">新建 Task</h4>
              <TaskForm
                allAgents={allAgents}
                allSchemas={schemas}
                siblingTasks={tasks}
                onSubmit={handleCreateTask}
                onCancel={() => setIsAddingTask(false)}
              />
            </div>
          )}

          {tasks.length === 0 && !isAddingTask && (
            <div className="text-xs text-sakura-300">无 Task，点击 + 新建</div>
          )}

          <div className="space-y-2">
            {tasks.map((t) => (
              <div key={t.id} className="rounded-lg border border-sakura-100 bg-white">
                <button
                  onClick={() => { setEditingTask(editingTask === t.id ? null : t.id); setIsAddingTask(false); }}
                  className="w-full px-3 py-2 text-left hover:bg-sakura-50/50"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-sakura-300">#{t.position + 1}</span>
                    <span className="text-sm font-medium text-sakura-700">{t.name}</span>
                    <span className="ml-auto text-xs text-sakura-300">
                      {t.agent_id
                        ? allAgents.find((a) => a.id === t.agent_id)?.name || `agent#${t.agent_id}`
                        : "（由主 Agent 分配）"}
                    </span>
                  </div>
                  <div className="mt-0.5 line-clamp-1 text-xs text-sakura-300">{t.description}</div>
                </button>
                {editingTask === t.id && (
                  <div className="border-t border-sakura-100 p-3">
                    <TaskForm
                      task={t}
                      allAgents={allAgents}
                      allSchemas={schemas}
                      siblingTasks={tasks.filter((x) => x.id !== t.id)}
                      onSubmit={(p) => handleUpdateTask(t.id, p)}
                      onCancel={() => setEditingTask(null)}
                      onDelete={() => handleDeleteTask(t.id)}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-sakura-400">{label}</label>
      {children}
    </div>
  );
}
