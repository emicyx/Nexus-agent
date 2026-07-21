"use client";

import { useState } from "react";
import {
  createOutputSchema,
  updateOutputSchema,
  deleteOutputSchema,
  type OutputSchemaRead,
} from "@/lib/api-client";
import { Trash2, Save, Plus, X } from "lucide-react";

const FIELD_TYPES = ["str", "int", "float", "bool", "list[str]", "list[int]", "list[float]"];

interface Props {
  schema_?: OutputSchemaRead;
  onSaved: () => void;
  onDeleted: () => void;
}

export function SchemaForm({ schema_, onSaved, onDeleted }: Props) {
  const isEdit = !!schema_;
  const [name, setName] = useState(schema_?.name ?? "");
  const [description, setDescription] = useState(schema_?.description ?? "");
  const [fields, setFields] = useState<{ name: string; type: string; required: boolean; description: string }[]>(
    schema_?.schema_fields ?? [],
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addField = () => {
    setFields([...fields, { name: "", type: "str", required: true, description: "" }]);
  };

  const removeField = (idx: number) => {
    setFields(fields.filter((_, i) => i !== idx));
  };

  const updateField = (idx: number, key: string, value: unknown) => {
    const next = fields.map((f, i) => (i === idx ? { ...f, [key]: value } : f));
    setFields(next);
  };

  const handleSave = async () => {
    if (!name.trim()) { setError("名称不能为空"); return; }
    setSaving(true);
    setError(null);
    try {
      const payload = { name, description, schema_fields: fields };
      if (isEdit && schema_) {
        await updateOutputSchema(schema_.id, payload);
      } else {
        await createOutputSchema(payload);
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!schema_) return;
    if (!confirm(`确认删除 Schema "${schema_.name}"？`)) return;
    try { await deleteOutputSchema(schema_.id); onDeleted(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  const inputCls =
    "w-full rounded-lg border border-sakura-200 bg-white px-3 py-2 text-sm text-zinc-700 placeholder-sakura-300 focus:outline-none focus:ring-2 focus:ring-sakura-300";

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-bold text-sakura-900">{isEdit ? "编辑 Schema" : "新建 Schema"}</h2>
        {isEdit && (
          <button onClick={handleDelete} className="ml-auto flex items-center gap-1 rounded-lg bg-red-50 px-3 py-1 text-xs text-red-500 hover:bg-red-100">
            <Trash2 size={12} /> 删除
          </button>
        )}
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">⚠ {error}</div>}

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">名称 (唯一标识)</label>
        <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="ResearchMaterial" />
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">描述</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} rows={2} placeholder="输出格式的简要描述" />
      </div>

      {/* 字段列表 */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs font-medium text-sakura-400">字段定义</span>
          <button onClick={addField} className="flex items-center gap-0.5 rounded bg-sakura-100 px-2 py-0.5 text-[10px] text-sakura-600 hover:bg-sakura-200">
            <Plus size={10} /> 加字段
          </button>
        </div>
        {fields.length === 0 && (
          <div className="rounded-lg border border-dashed border-sakura-200 px-3 py-3 text-center text-xs text-sakura-300">
            暂无字段，点击"+ 加字段"添加
          </div>
        )}
        <div className="space-y-2">
          {fields.map((f, i) => (
            <div key={i} className="flex items-start gap-2 rounded-lg border border-sakura-100 bg-sakura-50/30 p-2">
              <div className="flex-1 space-y-1.5">
                <div className="flex gap-2">
                  <input
                    value={f.name}
                    onChange={(e) => updateField(i, "name", e.target.value)}
                    className={`${inputCls} flex-1 text-xs`}
                    placeholder="字段名 (如 title)"
                  />
                  <select value={f.type} onChange={(e) => updateField(i, "type", e.target.value)} className={`${inputCls} w-28 text-xs`}>
                    {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div className="flex gap-3">
                  <label className="flex items-center gap-1 text-[10px] text-sakura-400">
                    <input type="checkbox" checked={f.required} onChange={(e) => updateField(i, "required", e.target.checked)} className="rounded border-sakura-300 text-sakura-500" />
                    必填
                  </label>
                  <input
                    value={f.description}
                    onChange={(e) => updateField(i, "description", e.target.value)}
                    className={`${inputCls} flex-1 text-xs`}
                    placeholder="字段描述"
                  />
                </div>
              </div>
              <button onClick={() => removeField(i)} className="mt-1 rounded p-0.5 text-sakura-300 hover:bg-red-50 hover:text-red-400">
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>

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
    </div>
  );
}
