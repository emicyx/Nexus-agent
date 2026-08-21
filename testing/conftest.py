"""testing/ 根 conftest：确保 backend 目录可被 import（app.*）。

在 Docker backend 容器内 PYTHONPATH=/app 已生效；本地跑时手动补路径。
"""
import os
import sys

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
