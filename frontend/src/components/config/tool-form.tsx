"use client";

import { useState } from "react";
import {
  createTool,
  updateTool,
  deleteTool,
  type ToolRead,
} from "@/lib/api-client";
import { Trash2, Save } from "lucide-react";

const TOOL_KEY_OPTIONS = [
  { value: "baidu_search", label: "baidu_search - 百度搜索" },
  { value: "intermediate", label: "intermediate - 中间结果保存" },
  { value: "add_image_local", label: "add_image_local - 添加本地图片" },
  { value: "fixed_directory_read", label: "fixed_directory_read - 固定目录读取" },
  { value: "rag_search", label: "rag_search - 知识库语义检索" },
  { value: "human_approval", label: "human_approval - HITL 人类审批" },
];

// Week 7: 结构化参数配置（按 tool_key 渲染不同字段）
const TOOL_PARAM_SCHEMA: Record<string, { key: string; label: string; default: number; min: number; max: number }> = {
  rag_search: { key: "top_k", label: "top_k (检索返回条数)", default: 5, min: 1, max: 20 },
  baidu_search: { key: "max_results", label: "max_results (搜索返回条数)", default: 20, min: 1, max: 50 },
};

interface Props {
  tool?: ToolRead;
  onSaved: () => void;
  onDeleted: () => void;
}

export function ToolForm({ tool, onSaved, onDeleted }: Props) {
  const isEdit = !!tool;
  const [name, setName] = useState(tool?.name ?? "");
  const [toolKey, setToolKey] = useState(tool?.tool_key ?? "baidu_search");
  const [description, setDescription] = useState(tool?.description ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 结构化参数
  const paramSchema = TOOL_PARAM_SCHEMA[toolKey];
  const getInitialParam = () => {
    if (!paramSchema) return null;
    const val = tool?.config_json?.[paramSchema.key];
    return typeof val === "number" ? val : paramSchema.default;
  };
  const [paramValue, setParamValue] = useState<number | null>(getInitialParam());

  // toolKey 变化时重置参数
  const handleToolKeyChange = (newKey: string) => {
    setToolKey(newKey);
    const schema = TOOL_PARAM_SCHEMA[newKey];
    if (schema) {
      const val = tool?.config_json?.[schema.key];
      setParamValue(typeof val === "number" ? val : schema.default);
    } else {
      setParamValue(null);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      // 组装 config_json
      let configParsed: Record<string, unknown> | null = null;
      if (paramSchema && paramValue !== null) {
        configParsed = { [paramSchema.key]: paramValue };
      }

      const payload = {
        name,
        tool_key: toolKey,
        description,
        config_json: configParsed,
      };
      if (isEdit && tool) {
        await updateTool(tool.id, payload);
      } else {
        await createTool(payload);
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!tool) return;
    if (!confirm(`确认删除 Tool "${tool.name}"？`)) return;
    try {
      await deleteTool(tool.id);
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
        <h2 className="text-lg font-bold text-sakura-900">{isEdit ? "编辑 Tool" : "新建 Tool"}</h2>
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
        <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="baidu_search" />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">tool_key (注册表键)</label>
        <select value={toolKey} onChange={(e) => handleToolKeyChange(e.target.value)} className={inputCls}>
          {TOOL_KEY_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-sakura-400">描述</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} rows={2} />
      </div>

      {/* Week 7: 结构化参数表单 */}
      {paramSchema && paramValue !== null && (
        <div>
          <label className="mb-1 block text-xs font-medium text-sakura-400">{paramSchema.label}</label>
          <input
            type="number"
            min={paramSchema.min}
            max={paramSchema.max}
            value={paramValue}
            onChange={(e) => setParamValue(parseInt(e.target.value) || paramSchema.default)}
            className={inputCls}
          />
          <div className="mt-1 text-[10px] text-sakura-300">
            范围 {paramSchema.min}-{paramSchema.max}，默认 {paramSchema.default}
          </div>
        </div>
      )}

      {/* 无参数化工具时显示提示 */}
      {!paramSchema && (
        <div className="rounded-lg border border-sakura-100 bg-sakura-50/30 px-3 py-2 text-xs text-sakura-300">
          该工具无参数化配置项
        </div>
      )}

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
