"use client";

import { useCallback, useEffect, useState } from "react";
import {
  describeTrigger,
  listJobRuns,
  listJobs,
  patchJob,
  triggerJob,
  type JobRead,
  type JobRunRead,
} from "@/lib/api-client";
import { Clock, Play, Loader2, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";

/** Jobs 页签（S2 最小集）：列表 / 启停 / 手动触发(eval) / 最近运行状态。 */
export function JobsPanel() {
  const [jobs, setJobs] = useState<JobRead[]>([]);
  const [runsByJob, setRunsByJob] = useState<Record<number, JobRunRead[]>>({});
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busyJob, setBusyJob] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listJobs();
      setJobs(list);
      const runs: Record<number, JobRunRead[]> = {};
      await Promise.all(
        list.map(async (j) => {
          runs[j.id] = await listJobRuns(j.id, 5);
        }),
      );
      setRunsByJob(runs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const toggleEnabled = async (job: JobRead) => {
    setBusyJob(job.id);
    setError(null);
    setNotice(null);
    try {
      await patchJob(job.id, { enabled: !job.enabled });
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyJob(null);
    }
  };

  const runOnce = async (job: JobRead, evalMode: boolean) => {
    setBusyJob(job.id);
    setError(null);
    setNotice(null);
    try {
      const r = await triggerJob(job.id, evalMode);
      setNotice(
        `「${job.name}」运行完成：status=${r.status}${r.pushed_to ? `，pushed_to=${r.pushed_to}` : "，未推送"}`,
      );
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyJob(null);
    }
  };

  const statusBadge = (status: string) => {
    const cls: Record<string, string> = {
      succeeded: "bg-emerald-50 text-emerald-600",
      eval: "bg-blue-50 text-blue-600",
      running: "bg-amber-50 text-amber-600",
      failed: "bg-red-50 text-red-600",
      disabled_by_circuit: "bg-red-100 text-red-700",
    };
    return cls[status] ?? "bg-zinc-100 text-zinc-500";
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      {(error || notice) && (
        <div
          className={`rounded-lg border px-3 py-2 text-sm ${
            error
              ? "border-red-200 bg-red-50 text-red-600"
              : "border-emerald-200 bg-emerald-50 text-emerald-700"
          }`}
        >
          {error ? `⚠ ${error}` : `✓ ${notice}`}
        </div>
      )}

      <section className="rounded-xl border border-sakura-200 bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-sakura-700">
            <Clock size={14} />
            定时任务 ({jobs.length})
          </h3>
          <button
            onClick={reload}
            disabled={loading}
            className="flex items-center gap-1 rounded-lg bg-sakura-100 px-2 py-1 text-xs text-sakura-600 hover:bg-sakura-200 disabled:opacity-50"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            刷新
          </button>
        </div>

        {jobs.length === 0 && !loading ? (
          <div className="text-xs text-sakura-300">
            暂无任务（种子 job 需要 SEED_DEMO_DATA=true 或容器内手动 ensure_seed()）
          </div>
        ) : (
          <ul className="divide-y divide-sakura-50">
            {jobs.map((job) => {
              const runs = runsByJob[job.id] ?? [];
              const last = runs[0];
              const open = expanded === job.id;
              return (
                <li key={job.id} className="py-2.5">
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => setExpanded(open ? null : job.id)}
                      className="text-sakura-400 hover:text-sakura-600"
                    >
                      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-sm text-sakura-700">
                        <span className="truncate font-medium">{job.name}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] ${
                            job.enabled
                              ? "bg-emerald-50 text-emerald-600"
                              : "bg-zinc-100 text-zinc-400"
                          }`}
                        >
                          {job.enabled ? "启用" : "停用"}
                        </span>
                        {job.consecutive_failures > 0 && (
                          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">
                            连败 {job.consecutive_failures}
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 text-[10px] text-sakura-300">
                        {describeTrigger(job)} · crew #{job.crew_id}
                        {job.crew?.name ? ` (${job.crew.name})` : ""}
                        {job.next_run_at
                          ? ` · 下次 ${new Date(job.next_run_at).toLocaleString()}`
                          : ""}
                      </div>
                    </div>
                    <button
                      onClick={() => runOnce(job, true)}
                      disabled={busyJob === job.id}
                      title="手动触发一次（eval 模式：零外发，不真实推送）"
                      className="flex items-center gap-1 rounded-lg bg-sakura-100 px-2 py-1 text-xs text-sakura-600 hover:bg-sakura-200 disabled:opacity-50"
                    >
                      {busyJob === job.id ? (
                        <Loader2 size={10} className="animate-spin" />
                      ) : (
                        <Play size={10} />
                      )}
                      运行(eval)
                    </button>
                    <button
                      onClick={() => toggleEnabled(job)}
                      disabled={busyJob === job.id}
                      className={`rounded-lg px-2 py-1 text-xs ${
                        job.enabled
                          ? "bg-zinc-100 text-zinc-500 hover:bg-zinc-200"
                          : "bg-emerald-500 text-white hover:bg-emerald-600"
                      } disabled:opacity-50`}
                    >
                      {job.enabled ? "停用" : "启用"}
                    </button>
                  </div>

                  {open && (
                    <div className="mt-2 space-y-1.5 pl-6">
                      <div className="rounded-lg bg-sakura-50/50 p-2 text-[11px] text-zinc-500">
                        <div className="mb-1 font-medium text-sakura-500">输入模板</div>
                        <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px]">
                          {job.input_template || "（空）"}
                        </pre>
                      </div>
                      {runs.length === 0 ? (
                        <div className="text-[11px] text-sakura-300">尚无运行记录</div>
                      ) : (
                        <ul className="space-y-1">
                          {runs.map((r) => (
                            <li
                              key={r.id}
                              className="flex items-center gap-2 rounded-lg border border-sakura-100 px-2 py-1.5 text-[11px]"
                            >
                              <span className={`rounded px-1.5 py-0.5 ${statusBadge(r.status)}`}>
                                {r.status}
                              </span>
                              <span className="text-zinc-400">
                                {new Date(r.started_at).toLocaleString()}
                              </span>
                              <span className="text-zinc-400">· {r.tokens_used} tokens</span>
                              {r.pushed_to && (
                                <span className="text-sakura-400">· {r.pushed_to}</span>
                              )}
                              {r.error && (
                                <span className="min-w-0 flex-1 truncate text-red-400" title={r.error}>
                                  {r.error}
                                </span>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
