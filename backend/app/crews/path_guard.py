"""委派链路径守卫与决策文本净化（2026-08-25 线上事故沉淀）。

事故复盘：hierarchical 委派链中，manager 会"重构"子 Agent 返回的文件路径
（工具真实落盘 outputs/raw/en-us.md，manager 输出
overwatch-champions-series-en-us.md），下游按假路径读取失败后 manager
误判为"抓取失败"无限重试。SOP 中"严禁编造路径"的指令级防线被实证穿透
——LLM 在多跳链路中对"逐字复制精确字符串"仍会重构，必须机械护栏。

两道防线：
1. validate_claimed_output_paths：在 BaseAgentTool._execute 回流点
   （子 Agent 结果返回 manager 之前）校验声称的 outputs/** 路径，
   不存在则附目录实际清单注入纠错上下文，manager 下一轮即可自我纠正。
2. sanitize_step_text：决策/思考事件文本净化——剥离裸 tool-call JSON
   残留、折叠重复行（manager 决策卡曾出现同段文字×2、裸
   {"name":..., "arguments":...}×3 直接展示给用户）。

设计约束：任何校验/净化失败一律降级放行原文，绝不阻断委派本身。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("crews.path_guard")

# 声称的产物路径：outputs/xxx.[ext]。要求前有边界（空白/引号/等号/括号等，
# 覆盖工具返回格式"文件=outputs/raw/xxx.md"），避免误配 xxxoutputs/。
_CLAIMED_PATH_RE = re.compile(
    r"(?:^|[\s'\"=：:《（(【\[])((?:\./)?outputs/[A-Za-z0-9_\-./\u4e00-\u9fff]+\.[A-Za-z0-9]{1,8})"
)

# 独占一行的裸 tool-call JSON（manager 决策残留）：{"name": "xxx", "arguments": {...}}
_BARE_TOOLCALL_RE = re.compile(
    r"^\s*\{\s*\"name\"\s*:\s*\"[^\"]+\"\s*,\s*\"arguments\"\s*:\s*\{.*?\}\s*\}\s*,?\s*$",
    re.MULTILINE,
)

_MAX_LIST_ENTRIES = 12


def validate_claimed_output_paths(result: str, *, coworker: str = "") -> str:
    """校验委派结果中声称的 outputs/** 路径，不存在则追加目录清单纠错。

    Args:
        result: 子 Agent 返回给 manager 的原始结果文本。
        coworker: 子 Agent 角色名（仅用于日志）。

    Returns:
        原文（无声称/全部存在/校验异常时），或原文+系统纠错附注。
    """
    try:
        claims = sorted({m.group(1) for m in _CLAIMED_PATH_RE.finditer(result)})
        if not claims:
            return result

        from app.tools._file_utils import _data_base  # 延迟导入，避免 crews→tools 环

        base = _data_base()
        bad: list[tuple[str, list[str]]] = []
        for rel in claims:
            resolved = (base / rel).resolve()
            if not resolved.is_relative_to(base):
                continue  # 越界路径由沙箱防线处理，此处不管
            if resolved.exists():
                continue
            parent = resolved.parent
            if not parent.is_dir():
                continue  # 目录都不存在：保持原报错语义，不抢戏
            entries = _list_dir_entries(parent, base)
            if entries:
                bad.append((rel, entries))

        if not bad:
            return result

        notes = []
        for rel, entries in bad:
            notes.append(
                f"✗ {rel} —— 不存在。该文件所在目录的实际内容（按修改时间，最新在前）：\n"
                + "\n".join(entries)
            )
        guard = (
            "\n\n---\n[系统路径校验] 你上述结果中声称的以下文件路径经磁盘校验不存在。"
            "禁止在后续输出中使用编造/拼接的路径，必须从下方清单逐字复制真实路径：\n"
            + "\n".join(notes)
        )
        logger.warning(
            "path_guard: coworker=%s 声称路径不存在 claims=%s",
            coworker, [rel for rel, _ in bad],
        )
        return result + guard
    except Exception:  # noqa: BLE001 - 守卫失败绝不阻断委派
        logger.exception("path_guard validate failed, 降级放行原文")
        return result


def sanitize_step_text(text: str) -> str:
    """净化决策/思考事件文本：剥离裸 tool-call JSON、折叠完全重复的行。

    仅用于 SSE 事件展示；返回值不参与任何控制逻辑（AgentFinish 检测等
    仍使用原文）。
    """
    try:
        if not text:
            return text
        cleaned = _BARE_TOOLCALL_RE.sub("", text)
        seen: set[str] = set()
        kept: list[str] = []
        blank_streak = 0
        for line in cleaned.splitlines():
            stripped = line.strip()
            if not stripped:
                blank_streak += 1
                if blank_streak <= 1:
                    kept.append(line)
                continue
            blank_streak = 0
            if stripped in seen:
                continue  # 完全重复的非空行只保留首次出现
            seen.add(stripped)
            kept.append(line)
        out = "\n".join(kept).strip()
        return out or text  # 净化后为空（如全文都是裸 JSON）则回退原文
    except Exception:  # noqa: BLE001
        return text


def _list_dir_entries(parent, base) -> list[str]:
    """目录文件清单：按 mtime 倒序，标注大小与修改时间（分钟前）。"""
    import time
    from datetime import datetime

    now = time.time()
    files = sorted(
        (f for f in parent.iterdir() if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:_MAX_LIST_ENTRIES]
    entries = []
    for f in files:
        st = f.stat()
        age_min = max(0, int((now - st.st_mtime) // 60))
        size_kb = max(1, st.st_size // 1024)
        try:
            rel = f.relative_to(base)
        except ValueError:
            rel = f.name
        entries.append(
            f"- {rel.as_posix()}（{size_kb}KB，{age_min} 分钟前修改，"
            f"{datetime.fromtimestamp(st.st_mtime):%m-%d %H:%M}）"
        )
    return entries
