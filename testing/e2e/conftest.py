"""Tier 3 真实 E2E smoke conftest。

- 默认跳过所有 e2e 用例；显式传 --run-e2e 才执行（真实 LLM，花 token）。
- 每个 smoke 会把 SSE trace + 耗时写进 testing/e2e/evidence/ 供评审报告引用。
"""
from pathlib import Path

import httpx
import pytest

BASE_URL = "http://localhost:8000"
EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"


def pytest_addoption(parser):
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="运行真实 LLM 端到端 smoke（需 QWEN_API_KEY，花 token）",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-e2e"):
        skip = pytest.mark.skip(reason="需要 --run-e2e")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def client():
    # function 级：每个用例在其自己的事件循环内创建独立 AsyncClient，
    # 避免 session 级 client 跨测试复用绑定已关闭的 loop（"Event loop is closed"）
    return httpx.AsyncClient(base_url=BASE_URL, timeout=180)


@pytest.fixture(autouse=True)
def _ensure_evidence_dir():
    EVIDENCE_DIR.mkdir(exist_ok=True)


@pytest.fixture
def evidence() -> Path:
    EVIDENCE_DIR.mkdir(exist_ok=True)
    return EVIDENCE_DIR
