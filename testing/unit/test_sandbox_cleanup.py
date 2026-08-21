"""沙箱产物清理单测（backend/app/core/sandbox_cleanup.py，上线欠账 A6）。"""
import os
import time

from app.config import settings
from app.core.sandbox_cleanup import check_disk_usage, cleanup_sandbox


def _make_old(path, days_old=10):
    path.write_text("stale", encoding="utf-8")
    ts = time.time() - days_old * 86400
    os.utime(path, (ts, ts))


def test_cleanup_removes_only_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_DATA_DIR", str(tmp_path))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    sub = outputs / "2024-07-15"
    sub.mkdir()

    old_file = outputs / "old.md"
    _make_old(old_file, days_old=10)
    old_nested = sub / "a.txt"
    _make_old(old_nested, days_old=8)
    new_file = outputs / "new.md"
    new_file.write_text("fresh", encoding="utf-8")

    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    old_shot = screenshots / "s.png"
    _make_old(old_shot, days_old=30)

    stats = cleanup_sandbox(retention_days=7)

    assert not old_file.exists()
    assert not old_nested.exists()
    assert not old_shot.exists()
    assert new_file.exists()  # 保留期内不动
    assert not sub.exists()  # 清空后的空目录一并移除
    assert outputs.exists()  # 根目录保留
    assert screenshots.exists()

    by_target = stats["targets"]
    assert by_target[str(outputs)]["deleted_files"] == 2
    assert by_target[str(screenshots)]["deleted_files"] == 1
    assert stats["deleted_files"] == 3


def test_cleanup_missing_dirs_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_DATA_DIR", str(tmp_path))
    stats = cleanup_sandbox(retention_days=7)
    assert stats["deleted_files"] == 0


def test_cleanup_keeps_new_files_in_deep_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_DATA_DIR", str(tmp_path))
    outputs = tmp_path / "outputs"
    deep = outputs / "a" / "b" / "c"
    deep.mkdir(parents=True)
    keep = deep / "keep.txt"
    keep.write_text("x", encoding="utf-8")

    stale_dir = outputs / "stale"
    stale_dir.mkdir()
    _make_old(stale_dir / "gone.txt", days_old=9)

    cleanup_sandbox(retention_days=7)
    assert keep.exists()
    assert keep.read_text(encoding="utf-8") == "x"
    assert not (stale_dir / "gone.txt").exists()
    assert not stale_dir.exists()  # 空目录移除
    assert deep.exists()  # 有文件的目录链保留


def test_disk_usage_returns_pct_or_none(tmp_path):
    pct = check_disk_usage(tmp_path)
    assert pct is None or 0.0 <= pct <= 100.0
    # 不存在的祖先链：逐级上探，最终能测到系统盘或返回 None，不抛异常
    missing = tmp_path / "no" / "such" / "dir"
    pct2 = check_disk_usage(missing)
    assert pct2 is None or 0.0 <= pct2 <= 100.0
