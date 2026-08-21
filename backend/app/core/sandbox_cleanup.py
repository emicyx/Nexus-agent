"""沙箱产物定期清理（上线欠账 A6）。

背景：Agent 每次运行都往 {SANDBOX_DATA_DIR}/outputs/** 与 /screenshots 写文件
（markdown/excel/word/code 工具产物、网页抓取存档、浏览器截图），无任何清理，
磁盘必然写满。

方案：
- cleanup_sandbox：删除 outputs/screenshots 下修改时间超过保留期（默认 7 天）
  的文件与随之清空的目录；rglob 保证只动沙箱子树内的条目。
- check_disk_usage：数据盘用量超阈值打告警日志（配合外部告警采集）。
- 调度：随应用 lifespan 启动（每日一次，线程池执行不阻塞事件循环）；
  也可 `python -m app.core.sandbox_cleanup` 独立运行（供系统 cron）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("sandbox_cleanup")


def _cleanup_targets() -> list[Path]:
    from app.tools._file_utils import output_base

    data_dir = Path(settings.SANDBOX_DATA_DIR).resolve()
    return [output_base(), data_dir / "screenshots"]


def cleanup_sandbox(retention_days: int | None = None) -> dict[str, Any]:
    """删除超期文件与空目录，返回统计。阻塞 IO，调用方自行放线程池。"""
    retention = (
        retention_days if retention_days is not None else settings.SANDBOX_RETENTION_DAYS
    )
    cutoff = time.time() - retention * 86400
    stats: dict[str, Any] = {
        "retention_days": retention,
        "deleted_files": 0,
        "deleted_bytes": 0,
        "removed_dirs": 0,
        "targets": {},
    }
    for root in _cleanup_targets():
        if not root.exists():
            continue
        target_deleted = 0
        target_bytes = 0
        subdirs: list[Path] = []
        for path in root.rglob("*"):
            try:
                if path.is_symlink() or path.is_file():
                    st = path.lstat()  # lstat：符号链接按自身 mtime，不跟随
                    if st.st_mtime < cutoff:
                        # unlink 只删除目录条目本身（rglob 已保证在子树内）
                        path.unlink()
                        target_deleted += 1
                        target_bytes += st.st_size
                elif path.is_dir():
                    subdirs.append(path)
            except OSError:
                logger.debug("cleanup skip unreadable entry: %s", path, exc_info=True)
        # 自底向上清掉因删除而变空的目录（根目录保留）
        subdirs.sort(key=lambda d: len(d.parts), reverse=True)
        removed_dirs = 0
        for d in subdirs:
            try:
                d.rmdir()  # 仅当目录为空才成功
                removed_dirs += 1
            except OSError:
                pass
        stats["deleted_files"] += target_deleted
        stats["deleted_bytes"] += target_bytes
        stats["removed_dirs"] += removed_dirs
        stats["targets"][str(root)] = {
            "deleted_files": target_deleted,
            "deleted_bytes": target_bytes,
            "removed_dirs": removed_dirs,
        }
    if stats["deleted_files"]:
        logger.info(
            "sandbox cleanup: 删除 %d 个文件（%.1f MB），移除 %d 个空目录（保留 %d 天）",
            stats["deleted_files"],
            stats["deleted_bytes"] / 1024 / 1024,
            stats["removed_dirs"],
            retention,
        )
    return stats


def check_disk_usage(data_dir: Path | None = None) -> float | None:
    """数据盘用量检查，超阈值打告警。返回用量百分比（拿不到返回 None）。"""
    target = data_dir or Path(settings.SANDBOX_DATA_DIR)
    # 目录可能还不存在，逐级向上找已存在的祖先
    probe = target
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        logger.debug("disk usage check failed", exc_info=True)
        return None
    pct = usage.used * 100 / usage.total
    if pct >= settings.DISK_USAGE_WARN_PCT:
        logger.warning(
            "disk_usage: %s 已用 %.1f%%（%.1f GB / %.1f GB），超过告警阈值 %d%%",
            probe,
            pct,
            usage.used / 1024**3,
            usage.total / 1024**3,
            settings.DISK_USAGE_WARN_PCT,
        )
    return pct


# ── 应用内调度（lifespan 启动，停机取消）─────────────────────────────

_cleanup_task: asyncio.Task | None = None


async def _cleanup_loop() -> None:
    # 启动后稍等再首跑，避开启动高峰
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(cleanup_sandbox)
            await asyncio.to_thread(check_disk_usage)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sandbox cleanup run failed")
        interval = max(1, settings.SANDBOX_CLEANUP_INTERVAL_HOURS) * 3600
        await asyncio.sleep(interval)


def start_cleanup_scheduler() -> None:
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        return
    _cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info(
        "sandbox cleanup scheduler started（每 %dh，保留 %d 天）",
        settings.SANDBOX_CLEANUP_INTERVAL_HOURS,
        settings.SANDBOX_RETENTION_DAYS,
    )


def stop_cleanup_scheduler() -> None:
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
    _cleanup_task = None


if __name__ == "__main__":
    # 供系统 cron 独立执行：python -m app.core.sandbox_cleanup
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    result = cleanup_sandbox()
    check_disk_usage()
    print(json.dumps(result, ensure_ascii=False))
