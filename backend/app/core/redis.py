"""
Redis 连接与热存储封装

- 提供全局 Redis 客户端（连接池）
- 提供会话热缓存的便捷方法（读回源、写穿）
- Redis 不可用时自动降级到纯 DB 模式，不影响主流程
"""

from typing import Optional
import json

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis 客户端（懒加载）
# 注意：在 asyncio 单线程事件循环中，懒加载不会有并发竞态问题
_redis_client = None
_redis_available = False
_redis_initialized = False


def get_redis():
    """获取 Redis 客户端（懒加载，失败则返回 None）

    调用方需判断返回值是否为 None 以决定是否降级。
    """
    global _redis_client, _redis_available, _redis_initialized
    if not settings.redis_enabled:
        return None
    if _redis_initialized:
        return _redis_client if _redis_available else None

    try:
        import redis.asyncio as redis_async

        _redis_client = redis_async.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
        )
        _redis_available = True
        _redis_initialized = True
        logger.info(f"Redis connected: {settings.redis_host}:{settings.redis_port}")
        return _redis_client
    except Exception as e:
        _redis_available = False
        _redis_initialized = True
        logger.warning(f"Redis unavailable, falling back to DB only: {e}")
        return None


async def redis_healthcheck() -> bool:
    """检查 Redis 是否可用"""
    r = get_redis()
    if r is None:
        return False
    try:
        return await r.ping()
    except Exception:
        return False


def _prefixed_key(key: str) -> str:
    return f"{settings.redis_key_prefix}{key}"


# ---------- 会话热缓存便捷方法 ----------

SESSION_HASH_FIELD_MESSAGES = "messages"
SESSION_HASH_FIELD_META = "meta"


async def cache_get_session_messages(session_id: str) -> Optional[list]:
    """从 Redis 读取会话消息列表（JSON 反序列化）

    Returns:
        list: 消息列表，未命中返回 None（表示需要回源 DB）
    """
    r = get_redis()
    if r is None:
        return None
    try:
        key = _prefixed_key(f"session:{session_id}")
        data = await r.hget(key, SESSION_HASH_FIELD_MESSAGES)
        if data is None:
            return None
        return json.loads(data)
    except Exception as e:
        logger.debug(f"Redis cache miss/error for session {session_id}: {e}")
        return None


async def cache_set_session_messages(session_id: str, messages: list, ttl: Optional[int] = None) -> None:
    """写会话消息到 Redis 缓存（写穿）"""
    r = get_redis()
    if r is None:
        return
    try:
        key = _prefixed_key(f"session:{session_id}")
        await r.hset(key, SESSION_HASH_FIELD_MESSAGES, json.dumps(messages, ensure_ascii=False))
        if ttl is None:
            ttl = settings.redis_session_ttl
        await r.expire(key, ttl)
    except Exception as e:
        logger.debug(f"Redis cache write error for session {session_id}: {e}")


async def cache_append_session_message(session_id: str, message: dict, ttl: Optional[int] = None) -> None:
    """追加单条消息到 Redis 缓存（读-改-写，非原子但足够）"""
    r = get_redis()
    if r is None:
        return
    try:
        messages = await cache_get_session_messages(session_id)
        if messages is None:
            messages = []
        messages.append(message)
        await cache_set_session_messages(session_id, messages, ttl)
    except Exception as e:
        logger.debug(f"Redis cache append error for session {session_id}: {e}")


async def cache_delete_session(session_id: str) -> None:
    """删除会话缓存"""
    r = get_redis()
    if r is None:
        return
    try:
        key = _prefixed_key(f"session:{session_id}")
        await r.delete(key)
    except Exception as e:
        logger.debug(f"Redis cache delete error for session {session_id}: {e}")
