/** Agent 自动配色：基于角色名 hash 确定性映射，新增 agent 零维护。 */

export interface AgentStyle {
  color: string;
  border: string;
  bg: string;
  icon: string;
  label: string;
}

const PALETTE: Omit<AgentStyle, "label">[] = [
  { color: "text-emerald-600", border: "border-emerald-400", bg: "bg-emerald-50",  icon: "🔍" },
  { color: "text-sky-600",     border: "border-sky-400",     bg: "bg-sky-50",      icon: "✍️" },
  { color: "text-violet-600",  border: "border-violet-400",  bg: "bg-violet-50",   icon: "🌐" },
  { color: "text-amber-600",   border: "border-amber-400",   bg: "bg-amber-50",    icon: "📦" },
  { color: "text-red-600",     border: "border-red-400",     bg: "bg-red-50",      icon: "🛡️" },
  { color: "text-purple-600",  border: "border-purple-400",  bg: "bg-purple-50",   icon: "👑" },
  { color: "text-teal-600",    border: "border-teal-400",    bg: "bg-teal-50",     icon: "📋" },
  { color: "text-indigo-600",  border: "border-indigo-400",  bg: "bg-indigo-50",   icon: "⚙️" },
  { color: "text-pink-600",    border: "border-pink-400",    bg: "bg-pink-50",     icon: "💡" },
  { color: "text-orange-600",  border: "border-orange-400",  bg: "bg-orange-50",   icon: "🔧" },
];

function hashString(s: string): number {
  let hash = 0;
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash) + s.charCodeAt(i);
    hash |= 0; // 32-bit int
  }
  return Math.abs(hash);
}

export function agentStyle(role?: string): AgentStyle {
  const label = role || "Agent";
  const idx = role ? hashString(role) % PALETTE.length : 0;
  return { ...PALETTE[idx], label };
}
