"use client";

import { useState } from "react";
import {
  createSkill,
  updateSkill,
  deleteSkill,
  type SkillRead,
} from "@/lib/api-client";
import { Trash2, Save } from "lucide-react";

interface Props {
  skill?: SkillRead;
  onSaved: () => void;
  onDeleted: () => void;
}

export function SkillForm({ skill, onSaved, onDeleted }: Props) {
  const isEdit = !!skill;
  const [name, setName] = useState(skill?.name ?? "");
  const [description, setDescription] = useState(skill?.description ?? "");
  const [promptTemplate, setPromptTemplate] = useState(skill?.prompt_template ?? "");
  const [skillKey, setSkillKey] = useState(skill?.skill_key ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name,
        description,
        prompt_template: promptTemplate,
        skill_key: skillKey || null,
      };
      if (isEdit && skill) {
        await updateSkill(skill.id, payload);
      } else {
        await createSkill(payload);
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!skill) return;
    if (!confirm(`确认删除 Skill "${skill.name}"？`)) return;
    try {
      await deleteSkill(skill.id);
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
        <h2 className="text-lg font-bold text-sakura-900">{isEdit ? "编辑 Skill" : "新建 Skill"}</h2>
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

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">名称 (唯一标识)</label>
        <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="代码审查" />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">描述</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} rows={2} placeholder="技能的简要描述" />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">prompt_template (指令模板)</label>
        <textarea
          value={promptTemplate}
          onChange={(e) => setPromptTemplate(e.target.value)}
          className={`${inputCls} font-mono text-xs`}
          rows={8}
          placeholder="你具备XXX能力。在执行任务时，请关注以下维度：&#10;1. ...&#10;2. ..."
        />
        <div className="mt-1 text-[10px] text-sakura-300">
          挂载到 Agent 后，此模板会注入到 Agent 的 backstory 中
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">skill_key (可选)</label>
        <input value={skillKey} onChange={(e) => setSkillKey(e.target.value)} className={inputCls} placeholder="code_review" />
      </div>

      <div className="flex items-center gap-2 pt-2">
        <button
          onClick={handleSave}
          disabled={saving || !name || !promptTemplate}
          className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-sakura-400 to-sakura-500 px-4 py-2 text-sm font-medium text-white transition hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-40"
        >
          <Save size={14} />
          {saving ? "保存中..." : isEdit ? "保存修改" : "创建"}
        </button>
      </div>
    </div>
  );
}
