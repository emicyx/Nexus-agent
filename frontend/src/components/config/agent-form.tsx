"use client";

import { useState } from "react";
import {
  createAgent,
  updateAgent,
  deleteAgent,
  type AgentRead,
  type SkillRead,
  type ToolRead,
} from "@/lib/api-client";
import { Trash2, Save } from "lucide-react";

interface Props {
  agent?: AgentRead;
  allTools: ToolRead[];
  allSkills: SkillRead[];
  onSaved: () => void;
  onDeleted: () => void;
}

export function AgentForm({ agent, allTools, allSkills, onSaved, onDeleted }: Props) {
  const isEdit = !!agent;
  const [name, setName] = useState(agent?.name ?? "");
  const [role, setRole] = useState(agent?.role ?? "");
  const [goal, setGoal] = useState(agent?.goal ?? "");
  const [backstory, setBackstory] = useState(agent?.backstory ?? "");
  const [llmModel, setLlmModel] = useState(agent?.llm_model ?? "");
  const [temperature, setTemperature] = useState(agent?.temperature ?? 0.7);
  const [maxIter, setMaxIter] = useState(agent?.max_iter ?? 8);
  const [memory, setMemory] = useState(agent?.memory ?? false);
  const [toolIds, setToolIds] = useState<number[]>(
    agent?.tools.map((t) => t.id) ?? []
  );
  const [skillIds, setSkillIds] = useState<number[]>(
    agent?.skills.map((s) => s.id) ?? []
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleTool = (id: number) => {
    setToolIds((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]
    );
  };

  const toggleSkill = (id: number) => {
    setSkillIds((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name,
        role,
        goal,
        backstory,
        llm_model: llmModel || null,
        temperature,
        max_iter: maxIter,
        memory,
      };
      if (isEdit && agent) {
        await updateAgent(agent.id, { ...payload, tool_ids: toolIds, skill_ids: skillIds });
      } else {
        await createAgent({ ...payload, tool_ids: toolIds, skill_ids: skillIds });
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!agent) return;
    if (!confirm(`确认删除 Agent "${agent.name}"？`)) return;
    try {
      await deleteAgent(agent.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const inputCls =
    "w-full rounded-lg border border-sakura-200 bg-white px-3 py-2 text-sm text-zinc-700 placeholder-sakura-300 focus:outline-none focus:ring-2 focus:ring-sakura-300";

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-bold text-sakura-900">{isEdit ? "编辑 Agent" : "新建 Agent"}</h2>
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
        <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="researcher" />
      </Field>

      <Field label="角色 (role)">
        <input value={role} onChange={(e) => setRole(e.target.value)} className={inputCls} placeholder="研究员" />
      </Field>

      <Field label="目标 (goal)">
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} className={inputCls} rows={2} />
      </Field>

      <Field label="背景故事 (backstory)">
        <textarea value={backstory} onChange={(e) => setBackstory(e.target.value)} className={inputCls} rows={3} />
      </Field>

      <div className="grid grid-cols-3 gap-3">
        <Field label="LLM 模型 (空=默认)">
          <input value={llmModel} onChange={(e) => setLlmModel(e.target.value)} className={inputCls} placeholder="qwen-plus" />
        </Field>
        <Field label="温度">
          <input
            type="number"
            step="0.1"
            value={temperature}
            onChange={(e) => setTemperature(parseFloat(e.target.value))}
            className={inputCls}
          />
        </Field>
        <Field label="max_iter">
          <input
            type="number"
            value={maxIter}
            onChange={(e) => setMaxIter(parseInt(e.target.value))}
            className={inputCls}
          />
        </Field>
      </div>

      <Field label="记忆">
        <label className="flex items-center gap-2 text-sm text-zinc-600">
          <input
            type="checkbox"
            checked={memory}
            onChange={(e) => setMemory(e.target.checked)}
            className="rounded border-sakura-300 text-sakura-500 focus:ring-sakura-300"
          />
          启用 memory（需 DB + embedder，MVP 默认关闭）
        </label>
      </Field>

      <Field label="挂载工具">
        {allTools.length === 0 ? (
          <div className="text-xs text-sakura-300">无可用工具，先到 Tools 标签创建</div>
        ) : (
          <div className="space-y-1.5">
            {allTools.map((t) => (
              <label
                key={t.id}
                className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition cursor-pointer ${
                  toolIds.includes(t.id)
                    ? "border-sakura-300 bg-sakura-50 text-sakura-700"
                    : "border-sakura-100 text-zinc-500 hover:bg-sakura-50/50"
                }`}
              >
                <input
                  type="checkbox"
                  checked={toolIds.includes(t.id)}
                  onChange={() => toggleTool(t.id)}
                  className="rounded border-sakura-300 text-sakura-500 focus:ring-sakura-300"
                />
                <span className="font-medium">{t.name}</span>
                <span className="text-xs text-sakura-300">({t.tool_key})</span>
              </label>
            ))}
          </div>
        )}
      </Field>

      <Field label="挂载技能 (Skills)">
        {allSkills.length === 0 ? (
          <div className="text-xs text-sakura-300">无可用技能，先到 Skills 标签创建</div>
        ) : (
          <div className="space-y-1.5">
            {allSkills.map((s) => (
              <label
                key={s.id}
                className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition cursor-pointer ${
                  skillIds.includes(s.id)
                    ? "border-sakura-300 bg-sakura-50 text-sakura-700"
                    : "border-sakura-100 text-zinc-500 hover:bg-sakura-50/50"
                }`}
              >
                <input
                  type="checkbox"
                  checked={skillIds.includes(s.id)}
                  onChange={() => toggleSkill(s.id)}
                  className="rounded border-sakura-300 text-sakura-500 focus:ring-sakura-300"
                />
                <span className="font-medium">{s.name}</span>
                <span className="text-xs text-sakura-300">
                  {s.skill_key ? `(${s.skill_key})` : `(${s.description.slice(0, 20)})`}
                </span>
              </label>
            ))}
          </div>
        )}
      </Field>

      <div className="flex items-center gap-2 pt-2">
        <button
          onClick={handleSave}
          disabled={saving || !name || !role}
          className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-sakura-400 to-sakura-500 px-4 py-2 text-sm font-medium text-white transition hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-40"
        >
          <Save size={14} />
          {saving ? "保存中..." : isEdit ? "保存修改" : "创建"}
        </button>
        <div className="text-xs text-sakura-300">
          保存后下次对话立即生效（DB 驱动，无需重启）
        </div>
      </div>
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
