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


# ---------- 迁移 v2：collections 增加 owner_user_id（SaaS 归属） ----------

async def migration_v2_collection_owner(engine, db_url: str) -> bool:
    """迁移 v2: collections 表增加 owner_user_id 列（旧数据 owner 为 NULL）"""
    table_exists = await _table_exists_async(engine, "collections")
    if not table_exists:
        return False

    from sqlalchemy import inspect

    def _has_col(sync_conn):
        insp = inspect(sync_conn)
        cols = [c["name"] for c in insp.get_columns("collections")]
        return "owner_user_id" in cols

    async with engine.begin() as conn:
        has = await conn.run_sync(_has_col)
    if has:
        return False

    print("[migration v2] Adding collections.owner_user_id...")

    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "ALTER TABLE collections ADD COLUMN owner_user_id VARCHAR(36)"
        )
        try:
            await conn.exec_driver_sql(
                "CREATE INDEX ix_collections_owner_user_id ON collections (owner_user_id)"
            )
        except Exception:
            # 索引已存在或创建失败不影响主流程
            pass

    print("[migration v2] Done.")
    return True


# ---------- 迁移 v3：email_codes 增加 failed_attempts 列 ----------

async def migration_v3_email_code_attempts(engine, db_url: str) -> bool:
    """迁移 v3: email_codes 表增加 failed_attempts 列（防暴力破解）"""
    table_exists = await _table_exists_async(engine, "email_codes")
    if not table_exists:
        return False

    from sqlalchemy import inspect

    def _has_col(sync_conn):
        insp = inspect(sync_conn)
        cols = [c["name"] for c in insp.get_columns("email_codes")]
        return "failed_attempts" in cols

    async with engine.begin() as conn:
        has = await conn.run_sync(_has_col)
    if has:
        return False

    print("[migration v3] Adding email_codes.failed_attempts...")

    async with engine.begin() as conn:
        if _is_sqlite(db_url):
            await conn.exec_driver_sql(
                "ALTER TABLE email_codes ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0"
            )
        else:
            await conn.exec_driver_sql(
                "ALTER TABLE email_codes ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0"
            )

    print("[migration v3] Done.")
    return True


# ---------- 迁移 v4：用户角色 admin→super_admin（三级层级改造） ----------

async def migration_v4_user_roles_hierarchy(engine, db_url: str) -> bool:
    """迁移 v4: 将 role='admin' 更新为 'super_admin'（三级层级改造）

    旧模型只有 admin/user 两种角色，新模型改为 super_admin/tenant_admin/user。
    - 旧 admin → super_admin（首个管理员成为总管理员）
    - 旧 user（owner_admin_id 指向旧 admin）→ 保持 user（作为子用户）
    """
    table_exists = await _table_exists_async(engine, "users")
    if not table_exists:
        return False

    print("[migration v4] Updating user roles to three-tier hierarchy...")

    async with engine.begin() as conn:
        # 将 role='admin' 的用户更新为 super_admin
        await conn.exec_driver_sql(
            "UPDATE users SET role = 'super_admin' WHERE role = 'admin'"
        )

    print("[migration v4] Done.")
    return True


# ---------- 迁移 v5：tasks 增加 user_id 列（SaaS 隔离） ----------

async def migration_v5_task_user_id(engine, db_url: str) -> bool:
    """迁移 v5: tasks 表增加 user_id 列并回填历史任务归属（SaaS 隔离）

    - 新增 user_id 列（若不存在）
    - 回填历史任务 user_id：依据 payload.collection_id 关联 collections.owner_user_id
      （幂等，仅处理 user_id 仍为 NULL 的行；无法确定归属的保持 NULL）
    """
    table_exists = await _table_exists_async(engine, "tasks")
    if not table_exists:
        return False

    from sqlalchemy import inspect

    def _has_col(sync_conn):
        insp = inspect(sync_conn)
        cols = [c["name"] for c in insp.get_columns("tasks")]
        return "user_id" in cols

    async with engine.begin() as conn:
        has = await conn.run_sync(_has_col)

    added = False
    if not has:
        print("[migration v5] Adding tasks.user_id...")
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN user_id VARCHAR(36)"
            )
            try:
                await conn.exec_driver_sql(
                    "CREATE INDEX ix_tasks_user_id ON tasks (user_id)"
                )
            except Exception:
                pass
        added = True

    # 回填历史任务的 user_id（幂等：只处理仍为 NULL 的行）
    if _is_postgresql(db_url):
        backfill_sql = """
            UPDATE tasks t
            SET user_id = c.owner_user_id
            FROM collections c
            WHERE t.user_id IS NULL
              AND t.payload ->> 'collection_id' = c.id
              AND c.owner_user_id IS NOT NULL
        """
    elif _is_sqlite(db_url):
        backfill_sql = """
            UPDATE tasks
            SET user_id = (
                SELECT c.owner_user_id FROM collections c
                WHERE c.id = json_extract(tasks.payload, '$.collection_id')
            )
            WHERE user_id IS NULL
              AND json_extract(payload, '$.collection_id') IS NOT NULL
        """
    else:
        backfill_sql = None

    if backfill_sql:
        try:
            async with engine.begin() as conn:
                result = await conn.exec_driver_sql(backfill_sql)
            print(f"[migration v5] Backfilled task user_id: {getattr(result, 'rowcount', '?')} rows.")
        except Exception as e:
            print(f"[migration v5] Backfill skipped: {e}")

    print("[migration v5] Done.")
    return added


# ---------- 迁移注册表 ----------
# 每个迁移签名: (name, async_fn(engine, db_url) -> bool)

MIGRATIONS: list[tuple[str, Callable]] = [
    ("v1_collection_id_nullable", migration_v1_collection_id_nullable),
    ("v2_collection_owner_user_id", migration_v2_collection_owner),
    ("v3_email_code_failed_attempts", migration_v3_email_code_attempts),
    ("v4_user_roles_hierarchy", migration_v4_user_roles_hierarchy),
    ("v5_task_user_id", migration_v5_task_user_id),
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
    同步运行迁移（SQLite 本地文件用，命令行手动迁移）

    为避免与启动时的异步迁移逻辑不一致，此入口直接复用 run_migrations_async，
    对指定 SQLite 文件执行全部迁移（v1–v5），不再单独维护一份同步实现。
    """
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    if db_path is None:
        db_path = "data/vdm.db"

    if not os.path.exists(db_path):
        return []

    db_url = f"sqlite+aiosqlite:///{db_path}"

    async def _run() -> list[str]:
        engine = create_async_engine(db_url)
        try:
            return await run_migrations_async(engine, db_url)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


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
