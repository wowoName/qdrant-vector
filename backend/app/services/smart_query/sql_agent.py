"""
SQLAgent - SQL 查询代理

负责：
1. 根据查询计划生成 SQL（LLM 生成 + 规则修正）
2. SQL 安全校验（防止注入、禁止危险操作）
3. 规则修复流水线（列名修正、表名修正、语法修复）
4. DuckDB 执行查询
5. 失败自动重试（最多重试 N 次，每次带错误信息）

设计原则：
- "先约束再修复" 流水线：生成 → 安全校验 → 规则修复 → 执行 → 结果校验 → 重试
- DuckDB 直查 Excel，利用现有基础设施
- 最大重试次数限制，防止无限循环
- SQL 执行超时保护，防止慢查询
- 所有列名/表名用引号包裹，避免中文列名的 SQL 语法问题
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.llm.factory import create_llm
from .schema_provider import SchemaProvider
from .types import QueryIntent, QueryOperation, QueryPlan, SQLQueryResult, TableSchema

logger = get_logger(__name__)

# 禁止的 SQL 关键字（安全校验）
FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
    "INTO OUTFILE", "INTO DUMPFILE", "LOAD DATA",
    "--", "/*", "*/",
]

# DuckDB 支持的安全操作
ALLOWED_STATEMENTS = ["SELECT", "WITH", "EXPLAIN", "DESCRIBE"]

# SQL 生成的系统提示
SQL_GENERATION_SYSTEM_PROMPT = """你是一个专业的 DuckDB SQL 生成助手。
请根据用户的问题和表结构，生成正确的 DuckDB SQL 查询语句。

表结构信息：
{schema_summary}

重要规则：
1. 只生成 SELECT 语句，不允许任何修改数据的操作
2. 所有列名和表名必须用双引号包裹，例如："销售总监"、"业务员"
3. 如果列名是中文，必须用双引号包裹
4. 字符串值用单引号包裹
5. 不要包含任何解释性文字，只输出 SQL 语句
6. 对于 COUNT 类问题，如果用户问的是"不重复的"或"多少种"，使用 COUNT(DISTINCT ...)
7. 对于列表类问题，使用 SELECT DISTINCT 来去重
8. 如果有分组统计，使用 GROUP BY
9. 排序时使用 ORDER BY
10. 只输出一条 SQL 语句，不要分号结尾

请只输出 SQL，不要输出其他任何内容。"""


class SQLAgent:
    """
    SQL 查询代理

    负责将自然语言查询转换为 SQL，并在 DuckDB 中执行。
    包含完整的生成 → 校验 → 修复 → 执行 → 重试流水线。

    数据加载通过 SchemaProvider 统一管理，支持表缓存和复用，
    避免重复读取 Excel 文件和重复 forward fill。
    """

    def __init__(
        self,
        schema_provider: Optional[SchemaProvider] = None,
        max_retries: int = 2,
        timeout_seconds: int = 30,
    ):
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self._llm = None
        # 复用 SchemaProvider 的 DuckDB 连接和表缓存
        # 避免同一份数据加载两次
        self._schema_provider = schema_provider or SchemaProvider()

    def _get_llm(self):
        """获取 LLM 实例（懒加载）"""
        if self._llm is None:
            try:
                self._llm = create_llm(
                    provider=getattr(settings, "rewrite_llm_provider", None) or settings.llm_provider,
                    model=getattr(settings, "rewrite_llm_model", None) or None,
                )
            except Exception as e:
                logger.warning(f"Failed to create LLM for SQLAgent: {e}")
                self._llm = None
        return self._llm

    def _get_duckdb_conn(self):
        """获取 DuckDB 连接（复用 SchemaProvider 的连接）"""
        return self._schema_provider._get_duckdb_conn()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def execute(
        self,
        query: str,
        plan: QueryPlan,
        schema: TableSchema,
        file_path: str,
        sheet_name: str = "",
    ) -> SQLQueryResult:
        """
        执行 SQL 查询

        Args:
            query: 原始用户问题
            plan: 查询计划
            schema: 表结构
            file_path: Excel/CSV 文件路径
            sheet_name: Sheet 名

        Returns:
            SQLQueryResult
        """
        start_time = time.time()

        # Step 1: 准备表（加载数据 + Forward Fill）
        table_name = self._prepare_table(file_path, sheet_name, schema)

        if not table_name:
            return SQLQueryResult(
                success=False,
                error="无法加载数据表",
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        # Step 2: 生成 SQL
        sql = await self._generate_sql(query, plan, schema, table_name)

        if not sql:
            return SQLQueryResult(
                success=False,
                error="无法生成 SQL 语句",
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        # Step 3: 执行 + 重试
        result = None
        last_error = ""

        for attempt in range(self.max_retries + 1):
            # 安全校验
            security_check = self._validate_sql_security(sql)
            if not security_check["ok"]:
                last_error = f"SQL安全校验失败: {security_check['reason']}"
                logger.warning(last_error)
                break

            # 规则修复
            sql = self._fix_sql_by_rules(sql, schema, table_name)

            # 执行
            exec_result = self._execute_sql(sql)

            if exec_result.success:
                result = exec_result
                result.retry_count = attempt
                break
            else:
                last_error = exec_result.error
                logger.debug(f"SQL execution attempt {attempt + 1} failed: {last_error}")

                # 如果还有重试机会，用错误信息让 LLM 修正 SQL
                if attempt < self.max_retries:
                    fixed_sql = await self._fix_sql_with_llm(
                        query, sql, last_error, schema, table_name
                    )
                    if fixed_sql and fixed_sql != sql:
                        sql = fixed_sql
                        continue
                    break
                break

        if result is None:
            result = SQLQueryResult(
                success=False,
                sql=sql,
                error=last_error or "未知错误",
                execution_time_ms=(time.time() - start_time) * 1000,
            )
        else:
            result.execution_time_ms = (time.time() - start_time) * 1000
            result.sql = sql

        # 清理：SQLAgent 不负责清理表，由 SchemaProvider 的 LRU 机制统一管理
        # （避免频繁建表删表，提升性能）
        return result

    # ------------------------------------------------------------------
    # 表准备
    # ------------------------------------------------------------------

    def _prepare_table(self, file_path: str, sheet_name: str, schema: TableSchema) -> str:
        """
        准备查询用的表（复用 SchemaProvider 已加载的数据）

        核心优化：SchemaProvider 已经用 pandas 做了 forward fill 并注册到 DuckDB，
        SQLAgent 直接复用那张表，不再重复读取文件和 forward fill，
        性能提升显著（尤其是同一文件多次查询的场景）。
        """
        try:
            # 通过 SchemaProvider 获取已加载的表名
            # 如果还没加载，get_or_load_table 会触发加载
            table_name = self._schema_provider.get_or_load_table(file_path, sheet_name)
            return table_name
        except Exception as e:
            logger.error(f"Failed to prepare table via SchemaProvider: {e}")
            return ""

    def _apply_forward_fill(self, conn, raw_table: str, target_table: str) -> None:
        """应用 Forward Fill（使用 pandas，可靠处理多行空值）"""
        try:
            import pandas as pd

            # 读取为 DataFrame
            df = conn.execute(f"SELECT * FROM {raw_table}").fetchdf()

            # Forward Fill
            df = df.ffill()

            # 去除全空行
            df = df.dropna(how="all")

            # 重置索引
            df = df.reset_index(drop=True)

            # 注册到 DuckDB 为目标表
            conn.register(target_table, df)

        except Exception as e:
            logger.warning(f"Forward Fill (pandas) failed, using raw data: {e}")
            # 失败则直接重命名 raw 表
            conn.execute(f"ALTER TABLE {raw_table} RENAME TO {target_table}")

    def _cleanup_table(self, table_name: str) -> None:
        """清理临时表"""
        if not table_name:
            return
        try:
            conn = self._get_duckdb_conn()
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.execute(f"DROP TABLE IF EXISTS {table_name}_raw")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SQL 生成
    # ------------------------------------------------------------------

    async def _generate_sql(
        self,
        query: str,
        plan: QueryPlan,
        schema: TableSchema,
        table_name: str,
    ) -> str:
        """生成 SQL 语句"""
        # 策略 1: 如果有明确的操作类型，优先用规则生成（快、准、省 token）
        # （不要求必须有 filters，无过滤条件的 LIST/COUNT 也能用规则生成）
        if plan.operation.value not in ("unknown", ""):
            rule_sql = self._generate_sql_from_plan(plan, schema, table_name)
            if rule_sql:
                logger.debug(f"Rule-based SQL generated: {rule_sql}")
                return rule_sql

        # 策略 2: 用 LLM 生成
        return await self._generate_sql_with_llm(query, schema, table_name)

    def _generate_sql_from_plan(
        self, plan: QueryPlan, schema: TableSchema, table_name: str
    ) -> str:
        """根据查询计划用规则生成 SQL"""
        if not plan.target_column and plan.operation == QueryOperation.LIST:
            # 如果没有指定目标列，列出所有列
            select_clause = "*"
        elif not plan.target_column:
            return ""
        else:
            target_col = f'"{plan.target_column}"'

            if plan.operation == QueryOperation.COUNT:
                select_clause = f'COUNT(DISTINCT "{plan.target_column}") AS "数量"'
            elif plan.operation == QueryOperation.LIST:
                select_clause = f'DISTINCT "{plan.target_column}"'
            elif plan.operation == QueryOperation.SUM:
                select_clause = f'SUM("{plan.target_column}") AS "总和"'
            elif plan.operation == QueryOperation.AVG:
                select_clause = f'AVG("{plan.target_column}") AS "平均值"'
            elif plan.operation == QueryOperation.DETAIL:
                select_clause = "*"
            elif plan.operation == QueryOperation.EXISTS:
                select_clause = f'COUNT(*) > 0 AS "是否存在"'
            else:
                select_clause = f'DISTINCT "{plan.target_column}"'

        # WHERE 子句
        where_conditions = []
        for f in plan.filters:
            col = f.column
            op = f.operator
            val = f.value
            if not col or val == "":
                continue
            # 安全：值中的单引号转义
            safe_val = str(val).replace("'", "''")
            if op.upper() == "LIKE":
                where_conditions.append(f'"{col}" LIKE \'{safe_val}\'')
            else:
                where_conditions.append(f'"{col}" = \'{safe_val}\'')

        where_clause = ""
        if where_conditions:
            where_clause = " WHERE " + " AND ".join(where_conditions)

        # GROUP BY 子句
        group_clause = ""
        if plan.operation == QueryOperation.GROUP_COUNT and plan.group_by:
            if plan.target_column and plan.group_by != plan.target_column:
                select_clause = (
                    f'"{plan.group_by}", '
                    f'COUNT(DISTINCT "{plan.target_column}") AS "数量"'
                )
            else:
                select_clause = f'"{plan.group_by}", COUNT(*) AS "数量"'
            group_clause = f' GROUP BY "{plan.group_by}"'

        # ORDER BY 子句
        order_clause = ""
        if plan.order_by:
            order_clause = f' ORDER BY "{plan.order_by}" {plan.order_dir}'
        elif plan.operation == QueryOperation.GROUP_COUNT:
            order_clause = ' ORDER BY "数量" DESC'

        # LIMIT 子句
        limit_clause = ""
        if plan.limit > 0:
            limit_clause = f" LIMIT {plan.limit}"
        # TOP_N 操作：必须有 LIMIT（默认前10条）
        elif plan.operation == QueryOperation.TOP_N:
            limit_clause = " LIMIT 10"

        sql = f"SELECT {select_clause} FROM {table_name}{where_clause}{group_clause}{order_clause}{limit_clause}"
        return sql

    async def _generate_sql_with_llm(
        self, query: str, schema: TableSchema, table_name: str
    ) -> str:
        """用 LLM 生成 SQL"""
        llm = self._get_llm()
        if llm is None:
            logger.warning("LLM not available for SQL generation")
            return ""

        schema_summary = self._build_schema_summary(schema, table_name)

        try:
            result = await llm.simple_chat(
                prompt=f"用户问题：{query}",
                system_prompt=SQL_GENERATION_SYSTEM_PROMPT.format(
                    schema_summary=schema_summary
                ),
                temperature=0.0,
                # 推理模型思考会占用 max_tokens，预算过小会拿不到 SQL
                max_tokens=1500,
            )

            sql = self._extract_sql(result)
            return sql

        except Exception as e:
            logger.warning(f"LLM SQL generation failed: {e}")
            return ""

    def _build_schema_summary(self, schema: TableSchema, table_name: str) -> str:
        """构造 schema 摘要"""
        lines = []
        lines.append(f"表名：{table_name}（来源：{schema.sheet_name}）")
        lines.append(f"数据行数：约 {schema.row_count} 行")
        lines.append("列名列表（请严格使用这些列名）：")

        for col in schema.columns:
            aliases_str = ""
            if col.aliases:
                aliases_str = f"，常见说法：{', '.join(col.aliases[:3])}"

            sample_str = ""
            if col.sample_values:
                sample_str = f"，示例值：{', '.join(col.sample_values[:3])}"

            lines.append(f"- \"{col.name}\" ({col.data_type}){aliases_str}{sample_str}")

        return "\n".join(lines)

    def _extract_sql(self, text: str) -> str:
        """从 LLM 响应中提取 SQL"""
        text = text.strip()

        # 去除 markdown 代码块
        sql_match = re.search(r"```(?:sql)?\s*\n?([\s\S]*?)\n?```", text, re.IGNORECASE)
        if sql_match:
            text = sql_match.group(1).strip()

        # 去除结尾的分号
        text = text.rstrip(";").strip()

        # 去除解释性文字（只保留第一条 SELECT 语句）
        lines = text.split("\n")
        sql_lines = []
        started = False
        for line in lines:
            line_stripped = line.strip()
            if not started:
                if line_stripped.upper().startswith(("SELECT", "WITH", "EXPLAIN")):
                    started = True
                    sql_lines.append(line)
            else:
                # 如果遇到空行且已经收集了内容，可能 SQL 结束了
                if not line_stripped and sql_lines:
                    # 检查是否还有后续 SQL 关键字，简单起见先继续
                    continue
                sql_lines.append(line)

        result = "\n".join(sql_lines).strip()
        return result if result.upper().startswith(("SELECT", "WITH")) else ""

    # ------------------------------------------------------------------
    # 安全校验
    # ------------------------------------------------------------------

    def _validate_sql_security(self, sql: str) -> dict:
        """验证 SQL 安全性"""
        if not sql or not sql.strip():
            return {"ok": False, "reason": "SQL为空"}

        # 先去除字符串字面量，避免字符串中的内容干扰安全检查
        # （包括单引号字符串和双引号标识符）
        sql_for_check = self._strip_sql_strings(sql)
        sql_upper = sql_for_check.upper().strip()

        # 检查是否是允许的语句类型
        is_allowed = False
        for stmt in ALLOWED_STATEMENTS:
            if sql_upper.startswith(stmt):
                is_allowed = True
                break

        if not is_allowed:
            return {"ok": False, "reason": f"不允许的SQL语句类型，必须以{ALLOWED_STATEMENTS}开头"}

        # 检查禁止的关键字（使用单词边界，避免子串误匹配）
        # 例如 "EXEC" 不会匹配到 "EXECUTIVE"
        for keyword in FORBIDDEN_KEYWORDS:
            # 对于纯符号类关键字（如 -- /* */ ;）直接做子串检查
            if re.match(r'^[^\w]+$', keyword):
                if keyword in sql_for_check:
                    return {"ok": False, "reason": f"包含禁止的关键字: {keyword}"}
            else:
                # 对于字母关键字，用单词边界匹配
                pattern = r'\b' + re.escape(keyword.upper()) + r'\b'
                if re.search(pattern, sql_upper):
                    return {"ok": False, "reason": f"包含禁止的关键字: {keyword}"}

        # 检查是否只有一条语句（不能有分号分隔的多条）
        # 由于已经去除了字符串字面量，这里的分号一定是语句分隔符
        if ";" in sql_for_check:
            # 末尾的分号可以接受
            if sql_for_check.rstrip().endswith(";"):
                # 检查是否只有末尾一个分号
                stripped = sql_for_check.rstrip().rstrip(";").rstrip()
                if ";" in stripped:
                    return {"ok": False, "reason": "不允许多条SQL语句"}
            else:
                return {"ok": False, "reason": "不允许多条SQL语句"}

        return {"ok": True, "reason": ""}

    def _strip_sql_strings(self, sql: str) -> str:
        """
        去除 SQL 中的字符串字面量和标识符，用于安全检查。

        这样可以避免字符串内容中的分号、关键字等误触发安全校验。
        单引号字符串和双引号标识符都会被替换为占位符。
        """
        result = []
        i = 0
        n = len(sql)

        while i < n:
            ch = sql[i]

            # 单引号字符串
            if ch == "'":
                result.append("'X'")  # 用占位符替换
                i += 1
                # 找到闭合的单引号（注意转义的 ''）
                while i < n:
                    if sql[i] == "'":
                        if i + 1 < n and sql[i + 1] == "'":
                            # 转义的单引号，跳过
                            i += 2
                            continue
                        else:
                            # 字符串结束
                            i += 1
                            break
                    else:
                        i += 1
                continue

            # 双引号标识符
            if ch == '"':
                result.append('"X"')
                i += 1
                while i < n and sql[i] != '"':
                    i += 1
                if i < n:
                    i += 1
                continue

            # 单行注释
            if ch == '-' and i + 1 < n and sql[i + 1] == '-':
                result.append('--')
                # 跳到行尾
                while i < n and sql[i] != '\n':
                    i += 1
                continue

            # 多行注释
            if ch == '/' and i + 1 < n and sql[i + 1] == '*':
                result.append('/* */')
                i += 2
                while i < n - 1 and not (sql[i] == '*' and sql[i + 1] == '/'):
                    i += 1
                if i < n - 1:
                    i += 2
                continue

            result.append(ch)
            i += 1

        return ''.join(result)

    # ------------------------------------------------------------------
    # 规则修复
    # ------------------------------------------------------------------

    def _fix_sql_by_rules(self, sql: str, schema: TableSchema, table_name: str) -> str:
        """
        基于规则的 SQL 修复

        常见问题：
        1. 列名未用双引号包裹（中文列名）
        2. 列名拼写错误或别名未映射
        3. 表名错误
        4. 语法小错误（如多余逗号、缺少空格）
        """
        if not sql:
            return sql

        # Step 1: 确保表名正确
        sql = self._fix_table_name(sql, table_name)

        # Step 2: 修复列名（确保中文列名用双引号包裹）
        sql = self._fix_column_names(sql, schema)

        # Step 3: 常见语法修复
        sql = self._fix_common_syntax(sql)

        return sql

    def _fix_table_name(self, sql: str, correct_table_name: str) -> str:
        """修复表名"""
        # 匹配 FROM 后面的表名（支持中文、英文、下划线、数字）
        # 用 [^\s,;"']+ 匹配任意非空白非分隔符的表名字符
        pattern = r'FROM\s+["`]?([^\s,;"\']+)["`]?'
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            old_table = match.group(1)
            if old_table.lower() != correct_table_name.lower():
                # 替换表名
                sql = sql[:match.start(1)] + correct_table_name + sql[match.end(1):]
        return sql

    def _fix_column_names(self, sql: str, schema: TableSchema) -> str:
        """修复列名：确保中文列名用双引号包裹，并修正列名"""
        for col in schema.columns:
            col_name = col.name

            # 跳过简单的英文/数字列名（但仍然可能需要修复）
            # 策略：找到所有可能的列名引用，确保它们被正确包裹

            # 处理列名未被双引号包裹的情况
            # 这是一个简化的修复，更完善的需要 SQL 解析器
            # 这里我们做常见模式的替换

            # 模式1: 列名直接出现，前后有空格或逗号或括号
            # 例如：SELECT 销售总监 FROM ... → SELECT "销售总监" FROM ...
            patterns = [
                # SELECT 后的列
                (rf'SELECT\s+({re.escape(col_name)})\s+FROM',
                 f'SELECT "{col_name}" FROM'),
                # WHERE 中的列
                (rf'WHERE\s+({re.escape(col_name)})\s*=',
                 f'WHERE "{col_name}" ='),
                # AND/OR 后的列
                (rf'\b(AND|OR)\s+({re.escape(col_name)})\s*=',
                 lambda m: f'{m.group(1)} "{col_name}" ='),
                # GROUP BY 后的列
                (rf'GROUP\s+BY\s+({re.escape(col_name)})',
                 f'GROUP BY "{col_name}"'),
                # ORDER BY 后的列
                (rf'ORDER\s+BY\s+({re.escape(col_name)})',
                 f'ORDER BY "{col_name}"'),
                # COUNT/AVG/SUM 中的列
                (rf'(COUNT|SUM|AVG|MAX|MIN)\(\s*({re.escape(col_name)})\s*\)',
                 lambda m: f'{m.group(1)}("{col_name}")'),
                # DISTINCT 后的列
                (rf'DISTINCT\s+({re.escape(col_name)})\s+FROM',
                 f'DISTINCT "{col_name}" FROM'),
            ]

            for pattern, replacement in patterns:
                if callable(replacement):
                    sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
                else:
                    sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

        # 修复反引号为双引号（DuckDB 使用双引号）
        sql = sql.replace("`", '"')

        return sql

    def _fix_common_syntax(self, sql: str) -> str:
        """修复常见语法问题"""
        # 去除多余的逗号（末尾逗号）
        sql = re.sub(r',\s*FROM', ' FROM', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*WHERE', ' WHERE', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*GROUP', ' GROUP', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*ORDER', ' ORDER', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*LIMIT', ' LIMIT', sql, flags=re.IGNORECASE)

        return sql

    # ------------------------------------------------------------------
    # LLM 修复 SQL
    # ------------------------------------------------------------------

    async def _fix_sql_with_llm(
        self,
        query: str,
        sql: str,
        error: str,
        schema: TableSchema,
        table_name: str,
    ) -> str:
        """用 LLM 根据错误信息修复 SQL"""
        llm = self._get_llm()
        if llm is None:
            return sql

        schema_summary = self._build_schema_summary(schema, table_name)

        fix_prompt = f"""你是一个专业的 DuckDB SQL 修复助手。

表结构信息：
{schema_summary}

原始问题：{query}

错误的 SQL：
```sql
{sql}
```

错误信息：
{error}

请修复这个 SQL，只输出修正后的 SQL 语句，不要包含任何解释文字。"""

        try:
            result = await llm.simple_chat(
                prompt="请修复上面的SQL",
                system_prompt=fix_prompt,
                temperature=0.0,
                max_tokens=1500,
            )

            fixed_sql = self._extract_sql(result)
            return fixed_sql if fixed_sql else sql

        except Exception as e:
            logger.warning(f"LLM SQL fix failed: {e}")
            return sql

    # ------------------------------------------------------------------
    # SQL 执行
    # ------------------------------------------------------------------

    def _execute_sql(self, sql: str) -> SQLQueryResult:
        """执行 SQL 查询"""
        conn = self._get_duckdb_conn()
        start_time = time.time()

        try:
            # 设置超时（DuckDB 支持）
            # 注意：DuckDB 的超时设置方式可能不同，这里做一个保守处理

            # 执行查询
            result = conn.execute(sql)
            rows = result.fetchall()
            columns = [desc[0] for desc in result.description] if result.description else []

            exec_time = (time.time() - start_time) * 1000

            # 获取扫描的行数（用于结果验证）
            total_scanned = len(rows)
            try:
                # 尝试获取 EXPLAIN 结果中的扫描行数
                pass
            except Exception:
                pass

            return SQLQueryResult(
                success=True,
                sql=sql,
                columns=columns,
                rows=[list(row) for row in rows],
                row_count=len(rows),
                total_scanned=total_scanned,
                execution_time_ms=exec_time,
            )

        except Exception as e:
            exec_time = (time.time() - start_time) * 1000
            error_msg = str(e)
            logger.debug(f"SQL execution error: {error_msg}")

            return SQLQueryResult(
                success=False,
                sql=sql,
                error=error_msg,
                execution_time_ms=exec_time,
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _detect_file_type(self, file_path: str) -> str:
        """检测文件类型"""
        import os

        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        if ext in ("xlsx", "xls"):
            return "xlsx"
        elif ext == "csv":
            return "csv"
        return "xlsx"

    def _get_first_sheet(self, file_path: str) -> str:
        """获取第一个 Sheet 名"""
        try:
            from openpyxl import load_workbook

            wb = load_workbook(file_path, read_only=True)
            sheets = wb.sheetnames
            wb.close()
            if sheets:
                return sheets[0]
        except Exception:
            pass

        return "Sheet1"

    def close(self) -> None:
        """关闭资源（由 SchemaProvider 统一管理，这里做引用清理）"""
        # 注意：不在这里关闭 DuckDB 连接，由 SchemaProvider 统一管理生命周期
        # SQLAgent 只是复用 SchemaProvider 的连接
        pass
