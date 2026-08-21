"""P0-4 / P1-9 运行时安全开关单测（app/main.py）。

仅测试纯逻辑函数（_validate_runtime_security / _docs_enabled），
不触发 lifespan（不建表/不连 DB）。
"""
import pytest

from app.config import settings


def _fns():
    from app.main import _docs_enabled, _validate_runtime_security

    return _validate_runtime_security, _docs_enabled


def test_production_missing_api_key_fails_fast(monkeypatch):
    validate, _ = _fns()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "APP_API_KEY", "")
    with pytest.raises(RuntimeError, match="APP_API_KEY"):
        validate()


def test_prod_alias_also_fails_fast(monkeypatch):
    validate, _ = _fns()
    monkeypatch.setattr(settings, "APP_ENV", "PROD")
    monkeypatch.setattr(settings, "APP_API_KEY", "")
    with pytest.raises(RuntimeError):
        validate()


def test_production_with_api_key_ok(monkeypatch):
    validate, _ = _fns()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "APP_API_KEY", "sk-long-random")
    validate()  # 不抛


def test_dev_missing_api_key_only_warns(monkeypatch):
    """本地开发保持宽容：不抛异常（启动侧只打 warning）。"""
    validate, _ = _fns()
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    monkeypatch.setattr(settings, "APP_API_KEY", "")
    validate()  # 不抛


def test_docs_default_dev_on_prod_off(monkeypatch):
    _, docs_enabled = _fns()
    monkeypatch.setattr(settings, "DOCS_ENABLED", None)
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    assert docs_enabled() is True
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert docs_enabled() is False


def test_docs_explicit_override(monkeypatch):
    _, docs_enabled = _fns()
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    monkeypatch.setattr(settings, "DOCS_ENABLED", False)
    assert docs_enabled() is False
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DOCS_ENABLED", True)
    assert docs_enabled() is True
