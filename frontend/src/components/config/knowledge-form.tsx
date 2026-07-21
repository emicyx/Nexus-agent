"use client";

import { useState } from "react";
import {
  createDocument,
  deleteDocument,
  searchDocuments,
  uploadDocumentFile,
  type DocumentRead,
  type SearchResult,
} from "@/lib/api-client";
import { Upload, FileText, Search, Trash2, Loader2 } from "lucide-react";

interface Props {
  documents: DocumentRead[];
  onReload: () => Promise<void>;
}

export function KnowledgeForm({ documents, onReload }: Props) {
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [docFilter, setDocFilter] = useState<number | "">("");

  const handleUploadText = async () => {
    if (!name.trim() || !content.trim()) {
      setError("名称和内容不能为空");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createDocument({ name: name.trim(), content });
      setName("");
      setContent("");
      await onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleUploadFile = async () => {
    if (!file) {
      setError("请先选择文件");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await uploadDocumentFile(file, name.trim() || undefined);
      setFile(null);
      setName("");
      await onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("确认删除该文档及其所有分块？")) return;
    setBusy(true);
    setError(null);
    try {
      await deleteDocument(id);
      await onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSearch = async () => {
    if (!query.trim()) {
      setError("搜索词不能为空");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await searchDocuments(
        query.trim(),
        topK,
        docFilter === "" ? undefined : Number(docFilter),
      );
      setResults(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const inputCls =
    "w-full rounded-lg border border-sakura-200 bg-white px-3 py-2 text-sm text-zinc-700 placeholder-sakura-300 focus:outline-none focus:ring-2 focus:ring-sakura-300";

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
          ⚠ {error}
        </div>
      )}

      {/* 上传文档 */}
      <section className="rounded-xl border border-sakura-200 bg-white p-4">
        <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-sakura-700">
          <Upload size={14} />
          上传文档
        </h3>
        <div className="grid gap-3">
          <input
            type="text"
            placeholder="文档名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputCls}
          />
          <textarea
            placeholder="粘贴文本内容（按空行分段切块，单段超过 500 字会硬切）"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={5}
            className={`${inputCls} font-mono text-xs`}
          />
          <div className="flex flex-wrap gap-2">
            <button
              onClick={handleUploadText}
              disabled={busy}
              className="flex items-center gap-1 rounded-lg bg-gradient-to-r from-sakura-400 to-sakura-500 px-3 py-1.5 text-sm font-medium text-white transition hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-50"
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              上传文本
            </button>
            <input
              type="file"
              accept=".txt,.md,.text"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-xs text-sakura-400 file:mr-2 file:rounded-lg file:border-0 file:bg-sakura-100 file:px-2 file:py-1 file:text-sakura-600"
            />
            <button
              onClick={handleUploadFile}
              disabled={busy || !file}
              className="flex items-center gap-1 rounded-lg bg-emerald-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-emerald-600 disabled:opacity-50"
            >
              上传文件
            </button>
          </div>
        </div>
      </section>

      {/* 已上传文档 */}
      <section className="rounded-xl border border-sakura-200 bg-white p-4">
        <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-sakura-700">
          <FileText size={14} />
          已上传文档 ({documents.length})
        </h3>
        {documents.length === 0 ? (
          <div className="text-xs text-sakura-300">暂无文档</div>
        ) : (
          <ul className="divide-y divide-sakura-50">
            {documents.map((d) => (
              <li key={d.id} className="flex items-center gap-3 py-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sakura-100">
                  <FileText size={14} className="text-sakura-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-sakura-700">{d.name}</div>
                  <div className="text-[10px] text-sakura-300">
                    {d.source_type} · {d.chunk_count} 块 · {new Date(d.created_at).toLocaleString()}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(d.id)}
                  disabled={busy}
                  className="flex items-center gap-1 rounded-lg bg-red-50 px-2 py-1 text-xs text-red-500 hover:bg-red-100 disabled:opacity-50"
                >
                  <Trash2 size={10} />
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 调试检索 */}
      <section className="rounded-xl border border-sakura-200 bg-white p-4">
        <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-sakura-700">
          <Search size={14} />
          调试检索
        </h3>
        <div className="mb-3 flex flex-wrap gap-2">
          <input
            type="text"
            placeholder="输入查询测试语义检索"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className={`flex-1 ${inputCls}`}
          />
          <select
            value={docFilter}
            onChange={(e) =>
              setDocFilter(e.target.value === "" ? "" : Number(e.target.value))
            }
            className={`${inputCls} w-40`}
          >
            <option value="">全部文档</option>
            {documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            max={20}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value) || 5)}
            className={`w-16 ${inputCls}`}
          />
          <button
            onClick={handleSearch}
            disabled={busy}
            className="flex items-center gap-1 rounded-lg bg-sakura-100 px-3 py-1.5 text-sm text-sakura-600 hover:bg-sakura-200 disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            检索
          </button>
        </div>
        {results && (
          <div className="space-y-2">
            {results.length === 0 ? (
              <div className="text-xs text-sakura-300">无结果（知识库可能为空）</div>
            ) : (
              results.map((r, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-sakura-100 bg-sakura-50/30 p-2.5 text-xs"
                >
                  <div className="mb-1 text-sakura-400">
                    [{r.document_name}] · 相关度={r.score.toFixed(4)} · pos={r.position}
                  </div>
                  <div className="whitespace-pre-wrap text-zinc-600">{r.content}</div>
                </div>
              ))
            )}
          </div>
        )}
      </section>
    </div>
  );
}
