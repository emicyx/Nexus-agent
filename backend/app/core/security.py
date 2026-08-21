"""API 鉴权（P0-2）：X-API-Key 静态密钥校验。

私人部署的最小鉴权：.env 设置 APP_API_KEY 后，所有挂载该依赖的 /v1/* 接口
强制校验请求头 X-API-Key；留空则放行（本地开发兼容，启动时告警）。
前端需同步配置 NEXT_PUBLIC_API_KEY（docker-compose 已自动透传）。
"""
import logging
import secrets

from fastapi import Header, HTTPException

from app.config import settings

logger = logging.getLogger("security")


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """FastAPI 依赖：校验 X-API-Key。APP_API_KEY 未配置时放行。"""
    expected = settings.APP_API_KEY
    if not expected:
        return
    # 常量时间比较：普通 == 可被逐字节计时侧信道爆破，密钥比对必须用 compare_digest
    if not x_api_key or not secrets.compare_digest(
        x_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="无效或缺失的 X-API-Key")
