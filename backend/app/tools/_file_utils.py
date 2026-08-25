"""文件工具公共辅助函数（P0-1 沙箱版）。

所有文件工具的读写必须限制在沙箱内：
- 写入仅允许 {SANDBOX_DATA_DIR}/outputs/**（sub_dir 可为任意相对子目录）
- 读取允许 {SANDBOX_DATA_DIR}/outputs/** 与 /screenshots/**（产物目录）
  及 SANDBOX_EXTRA_READ_DIRS 白名单目录；data 根其余部分（CrewAI 内部
  存储 .db / .crewai_user.json 等）不可读

路径解析统一：拒绝绝对参数 → 拼接 → Path.resolve() 归一化（消解 .. / 符号链接）
→ is_relative_to 强制 containment。越界抛 SandboxViolation，由各工具捕获后
返回中文错误串（不中断 CrewAI 执行流）。
"""
from pathlib import Path

from app.config import settings

# 截断阈值
_MAX_VIEW_CHARS = 8000


class SandboxViolation(PermissionError):
    """路径越出沙箱，文件操作被拒绝。"""


def _data_base() -> Path:
    return Path(settings.SANDBOX_DATA_DIR).resolve()


def output_base() -> Path:
    return _data_base() / "outputs"


def read_bases() -> list[Path]:
    """读取白名单根目录列表。

    只开放产物目录（outputs / screenshots）+ 额外配置目录，
    不再放开整个 {SANDBOX_DATA_DIR}：data 根下有 CrewAI 内部存储
    （latest_kickoff_task_outputs.db 跨会话任务输出、long_term_memory_storage.db、
    .crewai_user.json 设备指纹），agent 可经 view_file 读进 prompt 属于越权
    读取面。需要读其他目录时用 SANDBOX_EXTRA_READ_DIRS 显式加白。
    """
    bases = [output_base(), _data_base() / "screenshots"]
    for d in settings.SANDBOX_EXTRA_READ_DIRS:
        if d:
            bases.append(Path(d).resolve())
    return bases


def dir_listing_hint(missing: Path, base: Path | None = None) -> str | None:
    """文件不存在时的目录清单提示（view_file 等工具 404 报错的附注）。

    背景（2026-08-25 线上事故）：manager 在委派链中编造/重构文件路径，
    下游 view_file 只报"文件不存在"，manager 缺目录信息无法自我纠正，
    误判为抓取失败无限重试。在报错处直接附上目录实际清单（mtime 倒序）
    与最接近文件名，把纠错信息送到最需要它的决策环节。
    失败返回 None，调用方回退原始报错。
    """
    try:
        import difflib
        import time as _time
        from datetime import datetime as _dt

        parent = missing.parent
        if not parent.is_dir():
            return None
        files = [f for f in parent.iterdir() if f.is_file()]
        if not files:
            return None
        now = _time.time()
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        lines = []
        for f in files[:12]:
            st = f.stat()
            age_min = max(0, int((now - st.st_mtime) // 60))
            lines.append(
                f"- {f.name}（{max(1, st.st_size // 1024)}KB，"
                f"{age_min} 分钟前修改）"
            )
        hint = (
            "该文件所在目录的实际内容（按修改时间，最新在前）：\n"
            + "\n".join(lines)
        )
        close = difflib.get_close_matches(
            missing.name, [f.name for f in files], n=3, cutoff=0.4
        )
        if close:
            hint += f"\n最接近的文件名：{'、'.join(close)}"
        hint += "\n提示：请从上方清单逐字复制真实文件名，禁止自行拼接/编造路径。"
        return hint
    except Exception:  # noqa: BLE001 - 提示失败回退原始报错
        return None


def _ensure_within(path: Path, base: Path, what: str) -> Path:
    resolved = path.resolve()
    if resolved == base or base in resolved.parents:
        return resolved
    raise SandboxViolation(
        f"路径越出沙箱被拒绝：{what} 仅允许在 {base} 内（收到 {resolved}）"
    )


def resolve_output_path(filename: str, sub_dir: str = "") -> Path:
    """解析输出文件路径，自动创建目录，强制限制在 outputs 沙箱内。

    filename: 文件名（如 hello.py），不允许绝对路径或含 ..
    sub_dir: 可选相对子目录（如 src/），不允许绝对路径或含 ..
    返回: {SANDBOX_DATA_DIR}/outputs/{sub_dir}/{filename}（已 resolve）
    """
    if Path(filename).is_absolute() or Path(sub_dir).is_absolute():
        raise SandboxViolation("filename/sub_dir 不允许使用绝对路径")
    base = output_base()
    target = base / sub_dir / filename if sub_dir else base / filename
    final = _ensure_within(target, base, "写入")
    final.parent.mkdir(parents=True, exist_ok=True)
    return final


def resolve_read_path(file_path: str) -> Path:
    """解析读取路径，强制限制在读取白名单目录内。

    相对路径按 {SANDBOX_DATA_DIR}/ 为基准；绝对路径必须在白名单内。
    """
    p = Path(file_path)
    if not p.is_absolute():
        p = _data_base() / file_path
    resolved = p.resolve()
    for base in read_bases():
        if resolved == base or base in resolved.parents:
            return resolved
    raise SandboxViolation(
        f"路径越出沙箱被拒绝：读取仅允许在 {[str(b) for b in read_bases()]} 内"
        f"（收到 {resolved}）"
    )


def ensure_dir_within(directory: str, *, allow_extra: bool = True) -> Path:
    """校验目录参数在读取白名单内，返回 resolve 后的目录（不创建）。

    供目录遍历类工具（fixed_directory_read 等）使用。
    """
    return resolve_read_path(directory)


def truncate(text: str, max_chars: int = _MAX_VIEW_CHARS) -> str:
    """截断过长文本。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(已截断)"
