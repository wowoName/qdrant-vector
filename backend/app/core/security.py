"""
安全工具：密码哈希（bcrypt）与 JWT 令牌
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# JWT payload 中的自定义声明
_CLAIM_UID = "uid"
_CLAIM_ROLE = "role"


# ---------- 密码 ----------

def hash_password(plain: str) -> str:
    """bcrypt 哈希密码"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ---------- JWT ----------

def create_access_token(user_id: str, role: str) -> str:
    """签发 JWT"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        _CLAIM_UID: user_id,
        _CLAIM_ROLE: role,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """解码并校验 JWT，无效/过期返回 None"""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:
        logger.debug(f"JWT decode failed: {e}")
        return None
