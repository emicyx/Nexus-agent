"""文件工具公共辅助函数。

所有文件工具写入沙箱目录 /app/data/outputs/，
FileViewerTool 读取 /app/data/ 下的文件。
"""
from pathlib import Path

# 输出沙箱根目录
_OUTPUT_BASE = Path("/app/data/outputs")
# 读取根目录（含 outputs 子目录 + 上传的文档等）
_READ_BASE = Path("/app/data")

# 截断阈值
_MAX_VIEW_CHARS = 8000


def resolve_output_path(filename: str, sub_dir: str = "") -> Path:
    """解析输出文件路径，自动创建目录。

    filename: 文件名（如 hello.py）
    sub_dir: 可选子目录（如 src/）
    返回: /app/data/outputs/{sub_dir}/{filename}
    """
    base = _OUTPUT_BASE
    if sub_dir:
        base = base / sub_dir
    base.mkdir(parents=True, exist_ok=True)
    return base / filename


def resolve_read_path(file_path: str) -> Path:
    """解析读取路径。

    支持绝对路径和相对 /app/data/ 的路径。
    """
    p = Path(file_path)
    if p.is_absolute():
        return p
    return _READ_BASE / file_path


def truncate(text: str, max_chars: int = _MAX_VIEW_CHARS) -> str:
    """截断过长文本。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(已截断)"
