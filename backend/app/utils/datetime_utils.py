"""统一时间工具：全应用使用北京时间（默认 Asia/Shanghai）。

数据库时间列均为 TIMESTAMP WITHOUT TIME ZONE，应用层统一写入 naive 北京时间，
避免出现"存 UTC、前端按本地解析"导致的 8 小时偏差。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def app_tz() -> ZoneInfo:
    """当前应用时区对象"""
    return ZoneInfo(settings.app_timezone)


def now() -> datetime:
    """返回当前北京时间（naive，匹配 TIMESTAMP WITHOUT TIME ZONE 列）"""
    return datetime.now(app_tz()).replace(tzinfo=None)
