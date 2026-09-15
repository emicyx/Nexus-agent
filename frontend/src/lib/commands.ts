/**
 * Auto 模式 / 命令表（前端镜像）。
 *
 * 权威表在后端 backend/app/crews/route_v0.py 的 COMMAND_CREW_MAP——
 * 新增命令时两处一起改（S1 的 /ops 即如此），此处仅做展示与输入辅助，
 * 实际路由完全由后端决定（routed_crew 事件回传真实结果）。
 */
import type { CrewRead } from "@/lib/api-client";

/** 命令 → crew 名（与 route_v0.COMMAND_CREW_MAP 保持一致） */
export const CREW_COMMANDS: Record<string, string> = {
  "/kb": "knowledge_qa",
  "/write": "iterative_write_crew",
  "/ingest": "web_ingest_crew",
};

export interface CrewCommand {
  command: string;
  crewName: string;
  description: string;
}

/** 从 crew 目录（GET /v1/crews）派生当前可用命令：crew 存在才展示对应命令。 */
export function commandsFromCrews(crews: CrewRead[]): CrewCommand[] {
  return Object.entries(CREW_COMMANDS)
    .map(([command, crewName]) => {
      const crew = crews.find((c) => c.name === crewName);
      return crew
        ? { command, crewName, description: crew.description || crewName }
        : null;
    })
    .filter((c): c is CrewCommand => c !== null);
}

/** 输入以 / 开头时过滤匹配的命令（前缀匹配，大小写不敏感）。 */
export function filterCommands(commands: CrewCommand[], input: string): CrewCommand[] {
  const text = input.trimStart().toLowerCase();
  if (!text.startsWith("/")) return [];
  return commands.filter((c) => c.command.startsWith(text.split(/\s+/)[0]));
}
