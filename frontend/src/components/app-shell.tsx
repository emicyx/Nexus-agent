"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MessageCircle, Settings, Sparkles } from "lucide-react";
import { type ReactNode } from "react";

interface AppShellProps {
  leftPanel?: ReactNode;
  rightPanel?: ReactNode;
  children: ReactNode;
}

/** 三栏式应用 Shell：左栏导航 + 中栏内容 + 可选右栏 */
export function AppShell({ leftPanel, rightPanel, children }: AppShellProps) {
  const pathname = usePathname();
  const isChat = pathname === "/chat" || pathname === "/";
  const isConfig = pathname === "/config";

  return (
    <div className="flex h-screen flex-col">
      {/* TopBar */}
      <header className="flex items-center gap-3 border-b border-sakura-200 bg-white px-4 py-2.5 shadow-sm">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-sakura-300 to-sakura-500">
            <Sparkles size={16} className="text-white" />
          </div>
          <span className="text-lg font-bold text-sakura-900">Nexus</span>
        </div>

        <nav className="ml-6 flex items-center gap-1">
          <Link
            href="/chat"
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              isChat
                ? "bg-sakura-100 text-sakura-700"
                : "text-zinc-500 hover:bg-sakura-50 hover:text-sakura-600"
            }`}
          >
            <MessageCircle size={15} />
            对话
          </Link>
          <Link
            href="/config"
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              isConfig
                ? "bg-sakura-100 text-sakura-700"
                : "text-zinc-500 hover:bg-sakura-50 hover:text-sakura-600"
            }`}
          >
            <Settings size={15} />
            配置
          </Link>
        </nav>

        <div className="ml-auto flex items-center gap-2 text-xs text-sakura-400">
          <span className="hidden sm:inline">多功能 AI Agent 助手</span>
        </div>
      </header>

      {/* 三栏主体 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 左栏 */}
        {leftPanel && (
          <aside className="hidden w-56 flex-shrink-0 flex-col border-r border-sakura-200 bg-white/80 md:flex">
            {leftPanel}
          </aside>
        )}

        {/* 中栏 */}
        <main className="flex-1 overflow-hidden">{children}</main>

        {/* 右栏 */}
        {rightPanel && (
          <aside className="hidden w-56 md:w-64 lg:w-80 flex-shrink-0 flex-col border-l border-sakura-200 bg-white/80 md:flex">
            {rightPanel}
          </aside>
        )}
      </div>
    </div>
  );
}
