"""Tier 1 纯逻辑单测通用 fixtures。"""
import sys
from pathlib import Path

# 确保 app.* 可导入（Docker 内 PYTHONPATH=/app，本地兜底）
_ROOT = Path(__file__).resolve().parents[2]  # testing/unit/..  -> 仓库根
_BACKEND = _ROOT / "backend"
for _p in (str(_BACKEND), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
