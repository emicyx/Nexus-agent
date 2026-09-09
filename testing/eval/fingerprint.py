"""运行指纹：数据集哈希 + git SHA +（可选）Crew 配置快照哈希。

热更新平台的特殊要求：被测对象 = 代码 + DB 配置。缺配置快照时，
"配置漂移冒充代码退化"将无法归因——所以指纹不可用必须显式降级标注，
而不是静默留空。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def git_sha(repo_dir: str | Path | None = None) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()[:12] if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def dataset_fingerprint(path: str | Path) -> str:
    return _sha(Path(path).read_bytes())


@dataclass
class RunFingerprint:
    run_id: str
    git_sha: str
    datasets: dict[str, str] = field(default_factory=dict)     # name@version -> 哈希
    config_snapshot: str | None = None                          # 配置 API 哈希；None=未采集
    judge: str | None = None                                    # "qwen-plus@rubric-v1"；None=未启用
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "git_sha": self.git_sha,
            "datasets": self.datasets,
            "config_snapshot": self.config_snapshot,
            "judge": self.judge,
            "notes": self.notes,
        }

    def compare(self, other: "RunFingerprint") -> list[str]:
        """两次运行的指纹差异说明（报告头部展示，回答"到底什么变了"）。"""
        diffs = []
        if self.git_sha != other.git_sha:
            diffs.append(f"代码: {other.git_sha} → {self.git_sha}")
        for k, v in self.datasets.items():
            if k in other.datasets and other.datasets[k] != v:
                diffs.append(f"数据集 {k} 内容有变")
            elif k not in other.datasets:
                diffs.append(f"数据集 {k} 为新增")
        if self.config_snapshot and other.config_snapshot and self.config_snapshot != other.config_snapshot:
            diffs.append("Crew 配置快照有变（热更新通道）")
        if (self.config_snapshot is None) != (other.config_snapshot is None):
            diffs.append("配置快照采集状态不一致（对比可信度降级）")
        return diffs or ["指纹完全一致"]


def config_fingerprint_from_payloads(payloads: dict[str, str]) -> str:
    """对 {agents, crews, tools, tasks...} 的 API JSON 文本做稳定哈希。"""
    canonical = json.dumps(payloads, ensure_ascii=False, sort_keys=True)
    return _sha(canonical.encode("utf-8"))
