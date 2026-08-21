"""Tier 2 API 集成测试 conftest。

用 fastapi.testclient.TestClient（同步），其 lifespan 自动执行 startup：
init_db + ensure_seed + LLM warmup。所有请求走同一事件循环，规避 SQLAlchemy
async engine 跨 loop 复用问题。全程 Mock AliyunLLM.call + embedding，零真实 LLM 成本。
"""
import pytest
from fastapi.testclient import TestClient

from app.llm.aliyun_llm import AliyunLLM
from app.main import app


@pytest.fixture(scope="session")
def client():
    """TestClient 生命周期内自动跑 startup（建表 + 种子 + 预热）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    """把 AliyunLLM.call + acall 换成固定返回，避免真实调用（含 chat SSE 测试）。

    原生异步改造后 acall 不再路由到 call，两个都要 mock，否则集成测试打真实网络。
    """

    def fake_call(self, messages, tools=None, callbacks=None, available_functions=None,
                  max_iterations=10, _retry_on_empty=True, **kwargs):
        return "这是 Mock LLM 的固定回答。"

    async def fake_acall(self, messages, tools=None, callbacks=None, available_functions=None,
                         max_iterations=10, _retry_on_empty=True, **kwargs):
        return "这是 Mock LLM 的固定回答。"

    monkeypatch.setattr(AliyunLLM, "call", fake_call)
    monkeypatch.setattr(AliyunLLM, "acall", fake_acall)


@pytest.fixture(autouse=True)
def _mock_embedding(monkeypatch):
    """文档入库/检索走 Mock embedding，避免真实 DashScope embedding 调用。"""
    import app.services.document_service as ds

    fake_vec = [1.0] + [0.0] * 1023  # 单位向量，cosine 距离自身=0

    async def fake_embed_texts(texts):
        return [list(fake_vec) for _ in texts]

    async def fake_embed_query(text):
        return list(fake_vec)

    monkeypatch.setattr(ds, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(ds, "embed_query", fake_embed_query)
