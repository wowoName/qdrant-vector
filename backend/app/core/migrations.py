"""
数据库迁移脚本 - 处理表结构变更

PostgreSQL 专用：表结构变更直接使用 ALTER TABLE。

迁移列表：
- v1: conversations.collection_id 从 NOT NULL 改为 NULLABLE
- v2: collections 增加 owner_user_id（SaaS 归属）
- v3: email_codes 增加 failed_attempts（防暴力破解）
- v4: 用户角色 admin → super_admin（三级层级）
- v5: tasks 增加 user_id 并回填归属（SaaS 隔离）
- v6: users 增加 storage_quota 并回填默认租户配额（租户磁盘空间限制）
- v7: users 增加 register_ip（限制同一 IP 只能注册一个租户）
- v8: conversations 增加 state_json（结构化会话状态）
- v9: 历史 UTC 时间戳统一平移为北京时间（+8h，一次性；由标记表保证幂等）
"""

from __future__ import annotations

from typing import Callable


# ---------- 工具函数 ----------

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

    # PostgreSQL：直接 ALTER TABLE DROP NOT NULL
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "ALTER TABLE conversations ALTER COLUMN collection_id DROP NOT NULL"
        )
    print("[migration v1] ALTER TABLE done.")

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
    backfill_sql = """
        UPDATE tasks t
        SET user_id = c.owner_user_id
        FROM collections c
        WHERE t.user_id IS NULL
          AND t.payload ->> 'collection_id' = c.id
          AND c.owner_user_id IS NOT NULL
    """
    try:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(backfill_sql)
        print(f"[migration v5] Backfilled task user_id: {getattr(result, 'rowcount', '?')} rows.")
    except Exception as e:
        print(f"[migration v5] Backfill skipped: {e}")

    print("[migration v5] Done.")
    return added


# ---------- 迁移 v6：users 增加 storage_quota（租户磁盘空间限制） ----------

async def migration_v6_user_storage_quota(engine, db_url: str) -> bool:
    """迁移 v6: users 表增加 storage_quota 列并回填默认配额

    - 新增 storage_quota 列（字节，可空）
    - 已有租户（tenant_admin）与普通用户回填默认配额（由配置 tenant_default_quota_mb 决定）
    - super_admin（平台方）保持 NULL，表示不限制
    """
    table_exists = await _table_exists_async(engine, "users")
    if not table_exists:
        return False

    from sqlalchemy import inspect

    def _has_col(sync_conn):
        insp = inspect(sync_conn)
        cols = [c["name"] for c in insp.get_columns("users")]
        return "storage_quota" in cols

    async with engine.begin() as conn:
        has = await conn.run_sync(_has_col)
    if has:
        return False

    print("[migration v6] Adding users.storage_quota...")

    from app.core.config import settings

    default_quota = settings.tenant_default_quota_mb * 1024 * 1024
    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN storage_quota BIGINT")
        # 已有租户与普通用户回填默认配额；super_admin 保持 NULL（不限制）
        await conn.exec_driver_sql(
            "UPDATE users SET storage_quota = "
            f"{int(default_quota)} "
            "WHERE role IN ('tenant_admin', 'user') AND storage_quota IS NULL"
        )

    print("[migration v6] Done.")
    return True


# ---------- 迁移 v7：users 增加 register_ip（限制同一 IP 只能注册一个租户） ----------

async def migration_v7_user_register_ip(engine, db_url: str) -> bool:
    """迁移 v7: users 表增加 register_ip 列（记录注册来源 IP）

    历史数据无法追溯来源 IP，保持 NULL（不纳入同 IP 限制）。
    """
    table_exists = await _table_exists_async(engine, "users")
    if not table_exists:
        return False

    from sqlalchemy import inspect

    def _has_col(sync_conn):
        insp = inspect(sync_conn)
        cols = [c["name"] for c in insp.get_columns("users")]
        return "register_ip" in cols

    async with engine.begin() as conn:
        has = await conn.run_sync(_has_col)
    if has:
        return False

    print("[migration v7] Adding users.register_ip...")

    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN register_ip VARCHAR(64)")
        try:
            await conn.exec_driver_sql(
                "CREATE INDEX ix_users_register_ip ON users (register_ip)"
            )
        except Exception:
            # 索引已存在或创建失败不影响主流程
            pass

    print("[migration v7] Done.")
    return True


# ---------- 迁移 v8：conversations 增加 state_json（结构化会话状态） ----------

async def migration_v8_conversation_state(engine, db_url: str) -> bool:
    """迁移 v8: conversations 表增加 state_json 列（多轮范围继承的结构化状态）"""
    table_exists = await _table_exists_async(engine, "conversations")
    if not table_exists:
        return False

    from sqlalchemy import inspect

    def _has_col(sync_conn):
        insp = inspect(sync_conn)
        cols = [c["name"] for c in insp.get_columns("conversations")]
        return "state_json" in cols

    async with engine.begin() as conn:
        has = await conn.run_sync(_has_col)
    if has:
        return False

    print("[migration v8] Adding conversations.state_json...")

    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "ALTER TABLE conversations ADD COLUMN state_json TEXT"
        )

    print("[migration v8] Done.")
    return True


# ---------- 迁移 v9：历史 UTC 时间戳平移为北京时间（+8h，一次性） ----------

async def migration_v9_beijing_time_shift(engine, db_url: str) -> bool:
    """迁移 v9: 将历史 UTC 时间戳统一平移为北京时间

    旧版本所有时间字段以 UTC 存储，而应用层/前端按本地（北京）时间展示，
    导致历史记录整体早 8 小时。本迁移把已有数据整体平移 UTC→应用时区的偏移量，
    与新代码"应用层统一生成北京时间"保持一致。

    幂等保障：数据平移无法通过结构自检判断，故使用 _schema_migration_marks
    标记表记录执行状态，保证每个库只执行一次（主库 / 对话库各自独立标记）。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sqlalchemy import inspect

    from app.core.config import settings

    mark = "v9_beijing_time_shift"
    # 需要平移的表 -> 时间列（email_codes 为短时效验证码，为避免延长已失效验证码有效期，不参与平移）
    targets: dict[str, list[str]] = {
        "conversations": ["created_at", "updated_at"],
        "messages": ["created_at"],
        "conversation_summaries": ["created_at", "updated_at"],
        "user_memories": ["created_at", "updated_at"],
        "users": ["created_at", "expires_at", "last_login_at"],
        "upload_sessions": ["created_at", "updated_at", "expires_at"],
        "directories": ["created_at", "updated_at"],
        "collections": ["created_at", "updated_at"],
        "tasks": ["created_at", "updated_at", "started_at", "completed_at"],
        "documents": ["created_at", "updated_at"],
        "document_versions": ["created_at"],
        "chunks": ["created_at"],
        "document_records": ["created_at"],
    }

    # 计算 UTC → 应用时区的偏移小时数（历史数据均为 UTC）
    offset = datetime.now(ZoneInfo(settings.app_timezone)).utcoffset()
    hours = offset.total_seconds() / 3600 if offset else 0.0

    def _collect(sync_conn):
        insp = inspect(sync_conn)
        existing_tables = set(insp.get_table_names())
        plan: dict[str, list[str]] = {}
        for table, cols in targets.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in insp.get_columns(table)}
            hit = [c for c in cols if c in present]
            if hit:
                plan[table] = hit
        return plan

    async with engine.begin() as conn:
        # 标记表：记录一次性数据迁移，避免重复平移
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS _schema_migration_marks ("
            "name VARCHAR(100) PRIMARY KEY, "
            "applied_at TIMESTAMP DEFAULT now())"
        )
        marked = await conn.exec_driver_sql(
            f"SELECT 1 FROM _schema_migration_marks WHERE name = '{mark}'"
        )
        if marked.first() is not None:
            return False

        if hours == 0:
            await conn.exec_driver_sql(
                f"INSERT INTO _schema_migration_marks (name) VALUES ('{mark}')"
            )
            return False

        plan = await conn.run_sync(_collect)
        print(f"[migration v9] Shifting historical timestamps by +{hours}h ...")
        for table, cols in plan.items():
            for col in cols:
                await conn.exec_driver_sql(
                    f'UPDATE "{table}" SET "{col}" = "{col}" + '
                    f"INTERVAL '{hours} hours' WHERE \"{col}\" IS NOT NULL"
                )
        await conn.exec_driver_sql(
            f"INSERT INTO _schema_migration_marks (name) VALUES ('{mark}')"
        )

    print(f"[migration v9] Done. Shifted tables: {list(plan.keys())}")
    return True


# ---------- 迁移注册表 ----------
# 每个迁移签名: (name, async_fn(engine, db_url) -> bool)

MIGRATIONS: list[tuple[str, Callable]] = [
    ("v1_collection_id_nullable", migration_v1_collection_id_nullable),
    ("v2_collection_owner_user_id", migration_v2_collection_owner),
    ("v3_email_code_failed_attempts", migration_v3_email_code_attempts),
    ("v4_user_roles_hierarchy", migration_v4_user_roles_hierarchy),
    ("v5_task_user_id", migration_v5_task_user_id),
    ("v6_user_storage_quota", migration_v6_user_storage_quota),
    ("v7_user_register_ip", migration_v7_user_register_ip),
    ("v8_conversation_state_json", migration_v8_conversation_state),
    ("v9_beijing_time_shift", migration_v9_beijing_time_shift),
]


# ---------- 入口函数 ----------

async def run_migrations_async(engine, db_url: str) -> list[str]:
    """
    异步运行所有未执行的迁移（使用 SQLAlchemy engine）

    Args:
        engine: SQLAlchemy 异步引擎（PostgreSQL）
        db_url: 数据库 URL（保留用于日志/诊断）

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


if __name__ == "__main__":
    """命令行手动执行迁移（针对当前配置的 PostgreSQL）：

        uv run python -m app.core.migrations
    """
    import asyncio

    from app.core.config import settings
    from app.core.database import engine

    async def _main() -> None:
        applied = await run_migrations_async(engine, settings.database_url)
        print(f"Applied migrations: {applied}" if applied else "No migrations needed.")
        await engine.dispose()

    asyncio.run(_main())
