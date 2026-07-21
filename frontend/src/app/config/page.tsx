"use client";

import { useEffect, useState } from "react";
import {
  listAgents,
  listCrews,
  listDocuments,
  listOutputSchemas,
  listSkills,
  listTools,
  type AgentRead,
  type CrewRead,
  type DocumentRead,
  type OutputSchemaRead,
  type SkillRead,
  type ToolRead,
} from "@/lib/api-client";
import { AgentForm } from "@/components/config/agent-form";
import { CrewForm } from "@/components/config/crew-form";
import { KnowledgeForm } from "@/components/config/knowledge-form";
import { SchemaForm } from "@/components/config/schema-form";
import { SkillForm } from "@/components/config/skill-form";
import { ToolForm } from "@/components/config/tool-form";
import { AppShell } from "@/components/app-shell";
import { Bot, Wrench, Users, BookOpen, Zap, Plus, ChevronRight, FileCode } from "lucide-react";

type Tab = "agents" | "tools" | "skills" | "schemas" | "crews" | "knowledge";
type SelState = number | null;

export default function ConfigPage() {
  const [tab, setTab] = useState<Tab>("agents");
  const [agents, setAgents] = useState<AgentRead[]>([]);
  const [tools, setTools] = useState<ToolRead[]>([]);
  const [skills, setSkills] = useState<SkillRead[]>([]);
  const [schemas, setSchemas] = useState<OutputSchemaRead[]>([]);
  const [crews, setCrews] = useState<CrewRead[]>([]);
  const [documents, setDocuments] = useState<DocumentRead[]>([]);
  const [selAgent, setSelAgent] = useState<SelState>(null);
  const [selTool, setSelTool] = useState<SelState>(null);
  const [selSkill, setSelSkill] = useState<SelState>(null);
  const [selSchema, setSelSchema] = useState<SelState>(null);
  const [selCrew, setSelCrew] = useState<SelState>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const [a, t, s, sc, c, d] = await Promise.all([listAgents(), listTools(), listSkills(), listOutputSchemas(), listCrews(), listDocuments()]);
      setAgents(a);
      setTools(t);
      setSkills(s);
      setSchemas(sc);
      setCrews(c);
      setDocuments(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const switchTab = (t: Tab) => {
    setTab(t);
    setSelAgent(null);
    setSelTool(null);
    setSelSkill(null);
    setSelSchema(null);
    setSelCrew(null);
  };

  const selectedAgent = selAgent && selAgent > 0 ? agents.find((a) => a.id === selAgent) : null;
  const selectedTool = selTool && selTool > 0 ? tools.find((t) => t.id === selTool) : null;
  const selectedSkill = selSkill && selSkill > 0 ? skills.find((s) => s.id === selSkill) : null;
  const selectedSchema = selSchema && selSchema > 0 ? schemas.find((s) => s.id === selSchema) : null;
  const selectedCrew = selCrew && selCrew > 0 ? crews.find((c) => c.id === selCrew) : null;
  const isAgentNew = selAgent === -1;
  const isToolNew = selTool === -1;
  const isSkillNew = selSkill === -1;
  const isSchemaNew = selSchema === -1;
  const isCrewNew = selCrew === -1;

  const TAB_CONFIG = [
    { key: "agents" as Tab, label: "Agents", icon: Bot, count: agents.length },
    { key: "tools" as Tab, label: "Tools", icon: Wrench, count: tools.length },
    { key: "skills" as Tab, label: "Skills", icon: Zap, count: skills.length },
    { key: "schemas" as Tab, label: "Schemas", icon: FileCode, count: schemas.length },
    { key: "crews" as Tab, label: "Crews", icon: Users, count: crews.length },
    { key: "knowledge" as Tab, label: "知识库", icon: BookOpen, count: documents.length },
  ];

  // 左栏：Tab 导航
  const leftPanel = (
    <div className="flex h-full flex-col p-3">
      <div className="mb-3 text-xs font-medium text-sakura-400">配置分类</div>
      <div className="space-y-1">
        {TAB_CONFIG.map(({ key, label, icon: Icon, count }) => (
          <button
            key={key}
            onClick={() => switchTab(key)}
            className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${
              tab === key
                ? "bg-sakura-100 text-sakura-700 font-medium"
                : "text-zinc-500 hover:bg-sakura-50 hover:text-sakura-600"
            }`}
          >
            <Icon size={15} />
            <span className="flex-1 text-left">{label}</span>
            <span className="text-xs text-sakura-300">{count}</span>
            {tab === key && <ChevronRight size={14} className="text-sakura-400" />}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <AppShell leftPanel={leftPanel}>
      <div className="flex h-full overflow-hidden">
        {/* 列表面板（knowledge tab 无侧栏，全宽） */}
        {tab !== "knowledge" && (
          <aside className="w-56 flex-shrink-0 overflow-y-auto border-r border-sakura-200 bg-white/60">
            <div className="flex items-center justify-between border-b border-sakura-100 px-3 py-2">
              <span className="text-xs font-medium text-sakura-400">列表</span>
              <button
                onClick={() => {
                  if (tab === "agents") setSelAgent(-1);
                  else if (tab === "tools") setSelTool(-1);
                  else if (tab === "skills") setSelSkill(-1);
                  else if (tab === "schemas") setSelSchema(-1);
                  else if (tab === "crews") setSelCrew(-1);
                }}
                className="flex items-center gap-0.5 rounded bg-sakura-100 px-2 py-0.5 text-[10px] text-sakura-600 hover:bg-sakura-200"
              >
                <Plus size={10} />
                新建
              </button>
            </div>
            {loading && <div className="p-3 text-xs text-sakura-300">加载中...</div>}
            {tab === "agents" && (
              <ConfigSidebar
                items={agents.map((a) => ({ id: a.id, title: a.name, subtitle: a.role }))}
                selectedId={selAgent}
                onSelect={setSelAgent}
                emptyHint="无 Agent，点击 + 新建"
              />
            )}
            {tab === "tools" && (
              <ConfigSidebar
                items={tools.map((t) => ({ id: t.id, title: t.name, subtitle: t.tool_key }))}
                selectedId={selTool}
                onSelect={setSelTool}
                emptyHint="无 Tool，点击 + 新建"
              />
            )}
            {tab === "skills" && (
              <ConfigSidebar
                items={skills.map((s) => ({ id: s.id, title: s.name, subtitle: s.skill_key ?? s.description }))}
                selectedId={selSkill}
                onSelect={setSelSkill}
                emptyHint="无 Skill，点击 + 新建"
              />
            )}
            {tab === "schemas" && (
              <ConfigSidebar
                items={schemas.map((s) => ({ id: s.id, title: s.name, subtitle: `${s.schema_fields.length} 个字段` }))}
                selectedId={selSchema}
                onSelect={setSelSchema}
                emptyHint="无 Schema，点击 + 新建"
              />
            )}
            {tab === "crews" && (
              <ConfigSidebar
                items={crews.map((c) => ({
                  id: c.id,
                  title: c.name,
                  subtitle: `${c.process_type} · ${c.agents.length} agents`,
                }))}
                selectedId={selCrew}
                onSelect={setSelCrew}
                emptyHint="无 Crew，点击 + 新建"
              />
            )}
          </aside>
        )}

        {/* 编辑区 */}
        <main className="flex-1 overflow-y-auto p-5">
          {error && (
            <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
              ⚠ {error}
            </div>
          )}

          {tab === "agents" &&
            (isAgentNew ? (
              <AgentForm key="new" allTools={tools} allSkills={skills} onSaved={reload} onDeleted={async () => { setSelAgent(null); await reload(); }} />
            ) : selectedAgent ? (
              <AgentForm
                key={selectedAgent.id}
                agent={selectedAgent}
                allTools={tools}
                allSkills={skills}
                onSaved={reload}
                onDeleted={async () => {
                  setSelAgent(null);
                  await reload();
                }}
              />
            ) : (
              <Placeholder />
            ))}
          {tab === "tools" &&
            (isToolNew ? (
              <ToolForm key="new" onSaved={reload} onDeleted={async () => { setSelTool(null); await reload(); }} />
            ) : selectedTool ? (
              <ToolForm
                key={selectedTool.id}
                tool={selectedTool}
                onSaved={reload}
                onDeleted={async () => {
                  setSelTool(null);
                  await reload();
                }}
              />
            ) : (
              <Placeholder />
            ))}
          {tab === "skills" &&
            (isSkillNew ? (
              <SkillForm key="new" onSaved={reload} onDeleted={async () => { setSelSkill(null); await reload(); }} />
            ) : selectedSkill ? (
              <SkillForm
                key={selectedSkill.id}
                skill={selectedSkill}
                onSaved={reload}
                onDeleted={async () => {
                  setSelSkill(null);
                  await reload();
                }}
              />
            ) : (
              <Placeholder />
            ))}
          {tab === "schemas" &&
            (isSchemaNew ? (
              <SchemaForm key="new" onSaved={reload} onDeleted={async () => { setSelSchema(null); await reload(); }} />
            ) : selectedSchema ? (
              <SchemaForm
                key={selectedSchema.id}
                schema_={selectedSchema}
                onSaved={reload}
                onDeleted={async () => {
                  setSelSchema(null);
                  await reload();
                }}
              />
            ) : (
              <Placeholder />
            ))}
          {tab === "crews" &&
            (isCrewNew ? (
              <CrewForm key="new" allAgents={agents} onSaved={reload} onDeleted={async () => { setSelCrew(null); await reload(); }} />
            ) : selectedCrew ? (
              <CrewForm
                key={selectedCrew.id}
                crew={selectedCrew}
                allAgents={agents}
                onSaved={reload}
                onDeleted={async () => {
                  setSelCrew(null);
                  await reload();
                }}
              />
            ) : (
              <Placeholder />
            ))}
          {tab === "knowledge" && (
            <KnowledgeForm documents={documents} onReload={reload} />
          )}
        </main>
      </div>
    </AppShell>
  );
}

function ConfigSidebar({
  items,
  selectedId,
  onSelect,
  emptyHint,
}: {
  items: { id: number; title: string; subtitle: string }[];
  selectedId: SelState;
  onSelect: (id: number) => void;
  emptyHint: string;
}) {
  return (
    <div>
      {items.length === 0 && <div className="p-3 text-[10px] text-sakura-300">{emptyHint}</div>}
      {items.map((it) => (
        <button
          key={it.id}
          onClick={() => onSelect(it.id)}
          className={`w-full border-b border-sakura-50 px-3 py-2 text-left transition ${
            selectedId === it.id
              ? "bg-sakura-50 text-sakura-700"
              : "text-zinc-500 hover:bg-sakura-50/50"
          }`}
        >
          <div className="truncate text-sm font-medium">{it.title}</div>
          <div className="truncate text-[10px] text-sakura-300">{it.subtitle}</div>
        </button>
      ))}
    </div>
  );
}

function Placeholder() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-sakura-300">
      从左侧选择一项进行编辑，或点击 + 新建。
    </div>
  );
}
