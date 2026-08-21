"""DSN 转换单测（backend/app/db/session.py `_make_dsn`）。"""
from app.db.session import _make_dsn


def test_postgres_to_asyncpg():
    dsn = "postgresql://nexus:nexus@postgres:5432/nexus"
    assert _make_dsn(dsn) == "postgresql+asyncpg://nexus:nexus@postgres:5432/nexus"


def test_already_asyncpg_unchanged():
    dsn = "postgresql+asyncpg://u:p@host:5432/db"
    assert _make_dsn(dsn) == dsn


def test_password_with_special_chars():
    dsn = "postgresql://user:p%40ss:word@host:5432/db"
    out = _make_dsn(dsn)
    assert out.startswith("postgresql+asyncpg://user:p%40ss:word@")
    assert out.count("asyncpg") == 1


def test_other_scheme_unchanged():
    dsn = "sqlite:///tmp/test.db"
    assert _make_dsn(dsn) == dsn
