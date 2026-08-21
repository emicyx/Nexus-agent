"""文件工具沙箱单测（P0-1，backend/app/tools/_file_utils.py）。

沙箱根目录通过 monkeypatch settings 指到 tmp_path，不依赖容器内 /app/data。
"""
import pytest

from app.config import settings
from app.tools._file_utils import (
    SandboxViolation,
    resolve_output_path,
    resolve_read_path,
)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """把沙箱根指到 tmp_path/sandbox，额外读目录指到 tmp_path/extra。"""
    data = tmp_path / "sandbox"
    data.mkdir(parents=True, exist_ok=True)
    extra = tmp_path / "extra"
    extra.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "SANDBOX_DATA_DIR", str(data))
    monkeypatch.setattr(settings, "SANDBOX_EXTRA_READ_DIRS", [str(extra)])
    return data, extra


# ---------- 写入沙箱 ----------

def test_output_normal_relative():
    p = resolve_output_path("hello.md", "sub/dir")
    assert p.name == "hello.md"
    assert "outputs" in p.parts
    assert p.parent.exists()  # 目录自动创建


def test_output_traversal_sub_dir_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SandboxViolation):
        resolve_output_path("evil.md", "../" + outside.name)


def test_output_absolute_filename_rejected(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_output_path(str(tmp_path / "evil.md"))


def test_output_absolute_sub_dir_rejected(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_output_path("evil.md", str(tmp_path))


def test_output_dotdot_filename_rejected():
    with pytest.raises(SandboxViolation):
        resolve_output_path("../../etc/passwd.txt")


def test_sandbox_violation_is_permission_error():
    assert issubclass(SandboxViolation, PermissionError)


# ---------- 读取沙箱 ----------

def test_read_relative_inside_data(_sandbox):
    data, _ = _sandbox
    (data / "outputs").mkdir(parents=True, exist_ok=True)
    f = data / "outputs" / "a.md"
    f.write_text("x", encoding="utf-8")
    p = resolve_read_path("outputs/a.md")
    assert p == f.resolve()


def test_read_absolute_inside_data(_sandbox):
    data, _ = _sandbox
    f = data / "notes.txt"
    f.write_text("x", encoding="utf-8")
    assert resolve_read_path(str(f)) == f.resolve()


def test_read_absolute_outside_rejected(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("key", encoding="utf-8")
    with pytest.raises(SandboxViolation):
        resolve_read_path(str(outside))


def test_read_dotdot_escape_rejected():
    with pytest.raises(SandboxViolation):
        resolve_read_path("../../../etc/passwd")


def test_read_from_extra_dir(_sandbox):
    _, extra = _sandbox
    f = extra / "pic.png"
    f.write_bytes(b"\x89PNG")
    assert resolve_read_path(str(f)) == f.resolve()
