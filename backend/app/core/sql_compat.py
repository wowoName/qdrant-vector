"""PostgreSQL 的 JSON 取值 SQL 片段构造，以及 DB 会话健壮性工具。

背景：
    检索层需要从 JSON 文本列（document_records.data_json）中取值。
    PostgreSQL 没有 SQLite 的 json_extract()，且 data_json 是 text 列，
    因此必须先 cast 为 jsonb 再用 `->>` 取字段值。

用法：
    json_text("data_json", "姓名")   -> "(data_json::jsonb ->> '姓名')"
    json_number("data_json", "金额") -> 带非数值守卫的数值表达式
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


def _escape(key: str) -> str:
    """转义 JSON key 中的单引号（列名本身还会经过 schema 白名单校验）"""
    return key.replace("'", "''")


def json_text(col: str, key: str) -> str:
    """从 JSON 列取字符串值，返回 SQL 片段"""
    return f"({col}::jsonb ->> '{_escape(key)}')"


def json_number(col: str, key: str) -> str:
    """从 JSON 列取数值，返回 SQL 片段。

    用正则守卫，非数值/空值按 0 处理，避免脏数据触发 cast 异常把整条查询打挂。
    """
    raw = f"({col}::jsonb ->> '{_escape(key)}')"
    guard = "'^-?[0-9]+(\\.[0-9]+)?$'"
    return f"CASE WHEN {raw} ~ {guard} THEN CAST({raw} AS DOUBLE PRECISION) ELSE 0 END"


async def safe_rollback(session) -> None:
    """回滚会话中已失败的事务。

    检索是三路并行且共用同一个 session，任意一条语句失败后如果不回滚，
    PostgreSQL 会把整个事务标记为 aborted，导致同 session 后续所有查询
    报「当前事务被终止」，把其它检索路径一起打挂。
    """
    if session is None:
        return
    try:
        await session.rollback()
    except Exception as e:
        logger.warning(f"Rollback after query failure failed: {e}")
