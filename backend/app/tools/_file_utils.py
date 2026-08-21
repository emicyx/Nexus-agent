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
