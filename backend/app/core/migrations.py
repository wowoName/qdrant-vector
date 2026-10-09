"""
数据库迁移脚本 - 处理表结构变更

支持 SQLite 和 PostgreSQL。
SQLite 不支持完整的 ALTER TABLE，需要通过「重建表 + 数据复制」的方式迁移。
PostgreSQL 直接用 ALTER TABLE 即可。

迁移列表：
- v1: conversations.collection_id 从 NOT NULL 改为 NULLABLE
"""

from __future__ import annotations

import os
from typing import Callable


# ---------- 工具函数 ----------

def _is_sqlite(db_url: str) -> bool:
    return "sqlite" in db_url


def _is_postgresql(db_url: str) -> bool:
    return "postgresql" in db_url or "postgres" in db_url


async def _check_column_nullable_async(engine, table_name: str, column_name: str) -> bool:
    """检查某列是否为 nullable（True=可空, False=NOT NULL）

    使用 SQLAlchemy 的 inspect 反射表结构，兼容所有数据库。
    """
    from sqlalchemy import inspect

    def _inspect(sync_conn):
        insp = inspect(sync_conn)
        cols = insp.get_columns(table_name)
        for c in cols:
            if c["name"] == column_name:
                return c.get("nullable", True)
        # 列不存在，按可空处理（新表会自动创建）
        return True

    async with engine.begin() as conn:
        nullable = await conn.run_sync(_inspect)
    return nullable


def _check_table_exists_sync(sync_conn, table_name: str) -> bool:
    from sqlalchemy import inspect
    insp = inspect(sync_conn)
    return insp.has_table(table_name)


async def _table_exists_async(engine, table_name: str) -> bool:
    async with engine.begin() as conn:
        return await conn.run_sync(_check_table_exists_sync, table_name)


# ---------- 迁移 v1：conversations.collection_id nullable ----------

async def migration_v1_collection_id_nullable(engine, db_url: str) -> bool:
    """
    迁移 v1: conversations.collection_id 从 NOT NULL 改为 NULLABLE
    """
    # 先检查表是否存在
    table_exists = await _table_exists_async(engine, "conversations")
    if not table_exists:
        # 表不存在，说明是全新的数据库，create_all 会直接创建正确结构
        return False

    # 检查 collection_id 是否已经是 nullable
    nullable = await _check_column_nullable_async(engine, "conversations", "collection_id")
    if nullable:
        return False

    print("[migration v1] Migrating conversations.collection_id to nullable...")

    if _is_postgresql(db_url):
        # PostgreSQL：直接 ALTER TABLE DROP NOT NULL
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "ALTER TABLE conversations ALTER COLUMN collection_id DROP NOT NULL"
            )
        print("[migration v1] PostgreSQL ALTER TABLE done.")

    elif _is_sqlite(db_url):
        # SQLite：重建表方式
        async with engine.begin() as conn:
            # 1. 创建新表
            await conn.exec_driver_sql("""
                CREATE TABLE conversations_new (
                    id VARCHAR(36) PRIMARY KEY,
                    session_id VARCHAR(64) NOT NULL UNIQUE,
                    collection_id VARCHAR(36),
                    user_id VARCHAR(64),
                    title VARCHAR(255),
                    message_count INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'active',
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)

            # 2. 复制数据
            await conn.exec_driver_sql("""
                INSERT INTO conversations_new
                    (id, session_id, collection_id, user_id, title, message_count,
                     total_tokens, version, status, created_at, updated_at)
                SELECT id, session_id, collection_id, user_id, title, message_count,
                       total_tokens, version, status, created_at, updated_at
                FROM conversations
            """)

            # 3. 删除旧表
            await conn.exec_driver_sql("DROP TABLE conversations")

            # 4. 重命名新表
            await conn.exec_driver_sql(
                "ALTER TABLE conversations_new RENAME TO conversations"
            )

            # 5. 重建索引
            await conn.exec_driver_sql(
                "CREATE INDEX idx_conversations_session_id ON conversations(session_id)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX idx_conversations_collection_id ON conversations(collection_id)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX idx_conversations_user_id ON conversations(user_id)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX idx_conversations_created_at ON conversations(created_at)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX idx_conversations_updated_at ON conversations(updated_at)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX idx_collection_user ON conversations(collection_id, user_id)"
            )

        print("[migration v1] SQLite table rebuild done.")
    else:
        # 未知数据库类型，尝试通用的 ALTER TABLE
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(
                    "ALTER TABLE conversations ALTER COLUMN collection_id DROP NOT NULL"
                )
            print("[migration v1] Generic ALTER TABLE done.")
        except Exception as e:
            print(f"[migration v1] Failed with generic ALTER TABLE: {e}")
            raise

    return True


# ---------- 迁移注册表 ----------
# 每个迁移签名: (name, async_fn(engine, db_url) -> bool)

MIGRATIONS: list[tuple[str, Callable]] = [
    ("v1_collection_id_nullable", migration_v1_collection_id_nullable),
]


# ---------- 入口函数 ----------

async def run_migrations_async(engine, db_url: str) -> list[str]:
    """
    异步运行所有未执行的迁移（使用 SQLAlchemy engine）

    Args:
        engine: SQLAlchemy 异步引擎
        db_url: 数据库 URL，用于判断数据库类型

    Returns:
        已执行的迁移名称列表
    """
    applied: list[str] = []

    for name, migration_fn in MIGRATIONS:
        try:
            migrated = await migration_fn(engine, db_url)
            if migrated:
                applied.append(name)
        except Exception as e:
            print(f"[migration] Migration {name} failed: {e}")
            raise

    return applied


def run_migrations(db_path: str | None = None) -> list[str]:
    """
    同步运行迁移（SQLite 本地文件用，向后兼容）

    此函数仅用于 SQLite 本地文件的命令行手动迁移。
    应用启动时使用 run_migrations_async。
    """
    import sqlite3

    if db_path is None:
        db_path = "data/vdm.db"

    if not os.path.exists(db_path):
        return []

    applied: list[str] = []

    conn = sqlite3.connect(db_path)
    try:
        # SQLite 版本的迁移（直接用同步连接跑）
        for name, _ in MIGRATIONS:
            if name == "v1_collection_id_nullable":
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(conversations);")
                cols = {row[1]: row for row in cursor.fetchall()}
                if "collection_id" not in cols:
                    continue
                notnull = cols["collection_id"][3]
                if notnull == 0:
                    continue

                print("[migration v1] Migrating conversations.collection_id to nullable...")

                cursor.execute("""
                    CREATE TABLE conversations_new (
                        id VARCHAR(36) PRIMARY KEY,
                        session_id VARCHAR(64) NOT NULL UNIQUE,
                        collection_id VARCHAR(36),
                        user_id VARCHAR(64),
                        title VARCHAR(255),
                        message_count INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        version INTEGER NOT NULL DEFAULT 0,
                        status VARCHAR(20) DEFAULT 'active',
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                """)
                cursor.execute("""
                    INSERT INTO conversations_new
                        (id, session_id, collection_id, user_id, title, message_count,
                         total_tokens, version, status, created_at, updated_at)
                    SELECT id, session_id, collection_id, user_id, title, message_count,
                           total_tokens, version, status, created_at, updated_at
                    FROM conversations
                """)
                cursor.execute("DROP TABLE conversations")
                cursor.execute("ALTER TABLE conversations_new RENAME TO conversations")
                cursor.execute("CREATE INDEX idx_conversations_session_id ON conversations(session_id)")
                cursor.execute("CREATE INDEX idx_conversations_collection_id ON conversations(collection_id)")
                cursor.execute("CREATE INDEX idx_conversations_user_id ON conversations(user_id)")
                cursor.execute("CREATE INDEX idx_conversations_created_at ON conversations(created_at)")
                cursor.execute("CREATE INDEX idx_conversations_updated_at ON conversations(updated_at)")
                cursor.execute("CREATE INDEX idx_collection_user ON conversations(collection_id, user_id)")
                conn.commit()

                row_count = cursor.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                print(f"[migration v1] Done. {row_count} rows migrated.")
                applied.append(name)
    finally:
        conn.close()

    return applied


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        applied = run_migrations(sys.argv[1])
    else:
        applied = run_migrations()
    if applied:
        print(f"Applied migrations: {applied}")
    else:
        print("No migrations needed.")
