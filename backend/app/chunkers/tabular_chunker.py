"""
表格专用切片器（TabularChunker）
基于 DuckDB 高速分析引擎，专为 Excel / CSV 等表格数据提供结构化切片。

核心优势：
1. 保留表格语义：每个 chunk 都带有完整表头上下文，向量检索语义更准
2. SQL 切片：支持按行、按行组、按列分组、自定义 SQL 四种切片模式
3. 高性能：DuckDB 列式引擎 + 向量化执行，百万行级数据也很快
4. 虚拟表：直接从文件查询，无需先全量加载到内存

切片模式：
- row: 行级切片 —— 每行一个 chunk（"列名: 值"格式）
- row_group: 行组切片 —— 每 N 行一个 chunk（适合宽表/数据密集场景）
- column_group: 列组切片 —— 按列分组，每组一个 chunk（适合字段很多的宽表）
- sql: 自定义 SQL 切片 —— 用 SQL 先筛选/聚合，再对结果切片
"""

import time
from typing import List, Iterator, Optional

from app.core.config import settings
from app.core.logging import get_logger

from .base import BaseChunker, ChunkItem

logger = get_logger(__name__)


class TabularChunker(BaseChunker):
    """
    表格专用切片器

    针对 Excel/CSV 等结构化表格数据，提供保留表格语义的切片策略。
    底层基于 DuckDB 高速分析引擎，支持 SQL 查询驱动的灵活切片。
    """

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        mode: str = None,
        rows_per_chunk: int = None,
        columns_per_group: int = None,
        group_by_column: str = None,
        sql_template: str = None,
        include_headers: bool = True,
    ):
        """
        Args:
            chunk_size: 单个 chunk 的大致字符数上限（行组模式下用于自适应行数估算）
            chunk_overlap: 行组之间重叠行数（仅 row_group 模式）
            mode: 切片模式：row / row_group / column_group / sql
            rows_per_chunk: 行组模式下每组行数（默认自动则按 chunk_size 估算）
            columns_per_group: 列组模式下每组列数
            group_by_column: 按某列分组后切片（类似 GROUP BY 语义）
            sql_template: 自定义 SQL 模板，用 {table} 占位符表示虚拟表名
            include_headers: 每个 chunk 是否包含表头信息
        """
        self.chunk_size = chunk_size or settings.chunk_size
        # 表格数据行之间没有上下文依赖，默认不重叠；如需重叠可显式传入
        # 显式传 0 也应使用 0（而不是回退到 settings.chunk_overlap）
        if chunk_overlap is not None:
            self.chunk_overlap = chunk_overlap
        else:
            self.chunk_overlap = getattr(settings, "tabular_chunk_overlap", 0)
        self.mode = (mode or getattr(settings, "tabular_chunk_mode", "row")).lower()
        self.rows_per_chunk = rows_per_chunk or getattr(settings, "tabular_rows_per_chunk", None)
        self.columns_per_group = columns_per_group or getattr(
            settings, "tabular_columns_per_group", 10
        )
        self.group_by_column = group_by_column
        self.sql_template = sql_template
        self.include_headers = include_headers

        # DuckDB 连接（懒加载，避免不使用时不引入依赖）
        self._duckdb_conn = None

    # ------------------------------------------------------------------
    # 对外主接口（兼容 BaseChunker 抽象）
    # ------------------------------------------------------------------
    def chunk(self, text: str) -> list[ChunkItem]:
        """
        纯文本模式下退化为按行分块（兼容接口一致性）。
        对于表格数据，请使用 chunk_tables() 或 chunk_tables_stream()。
        """
        logger.warning(
            "TabularChunker.chunk(text) called with plain text, "
            "falling back to line-based chunking. "
            "Use chunk_tables() for structured table data."
        )
        return self._fallback_text_chunk(text)

    def chunk_with_pages(self, pages: list[str]) -> list[ChunkItem]:
        """按页切分表格（每页是一个 sheet / 数据块）"""
        all_chunks = []
        global_offset = 0
        chunk_idx = 0
        for page_num, page_text in enumerate(pages):
            if not page_text.strip():
                global_offset += len(page_text)
                continue
            page_chunks = self._fallback_text_chunk(page_text)
            for c in page_chunks:
                c.index = chunk_idx
                c.char_start += global_offset
                c.char_end += global_offset
                c.page_num = page_num
                all_chunks.append(c)
                chunk_idx += 1
            global_offset += len(page_text)
        return all_chunks

    # ------------------------------------------------------------------
    # 表格专用接口：直接从表格数据切片
    # ------------------------------------------------------------------
    def chunk_tables(
        self,
        tables: List[List[List[str]]],
        sheet_names: Optional[List[str]] = None,
        file_path: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> list[ChunkItem]:
        """
        从结构化表格数据生成 Chunk。

        Args:
            tables: 表格列表，每个表为 [ [header_row], [data_row1], ... ]
            sheet_names: 对应的 sheet 名称列表（可选）
            file_path: 源文件路径（用于 DuckDB 直接读取模式）
            file_type: 文件类型（xlsx / csv）

        Returns:
            ChunkItem 列表
        """
        return list(
            self.chunk_tables_stream(
                tables=tables,
                sheet_names=sheet_names,
                file_path=file_path,
                file_type=file_type,
            )
        )

    def chunk_tables_stream(
        self,
        tables: List[List[List[str]]],
        sheet_names: Optional[List[str]] = None,
        file_path: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> Iterator[ChunkItem]:
        """
        流式生成表格 Chunk。

        混合策略（优先 DuckDB，失败回退内存）：
        - 有 file_path 且 DuckDB 可用时，逐 sheet 尝试 DuckDB 高速处理
        - 单个 sheet 加载失败时，用内存中的 tables 数据补充（不整体回退）
        - 没有 file_path 或 DuckDB 不可用时，全部走内存模式
        """
        sheet_names = sheet_names or []
        tables = tables or []

        # ---- 没有 DuckDB 或没有文件路径：全部走内存模式 ----
        use_duckdb = bool(file_path and file_type and self._try_init_duckdb())

        if not use_duckdb:
            reason = []
            if not file_path:
                reason.append("no file_path")
            if not file_type:
                reason.append("no file_type")
            if file_path and file_type and not self._duckdb_conn:
                reason.append("duckdb package not available")
            reason_str = ", ".join(reason) if reason else "unknown"
            logger.warning(
                "\n"
                "============================================================\n"
                "  🐌  TABULAR CHUNKER MODE: IN-MEMORY (内存模式)\n"
                f"  原因: {reason_str}\n"
                f"  文件: {file_path or 'in-memory tables'}\n"
                f"  切片模式: {self.mode}\n"
                "============================================================"
            )
            t_mem_start = time.perf_counter()
            idx = 0
            char_offset = 0
            for sheet_idx, table in enumerate(tables):
                if not table or len(table) < 2:
                    continue
                headers = table[0]
                data_rows = table[1:]
                sheet_name = (
                    sheet_names[sheet_idx]
                    if sheet_idx < len(sheet_names)
                    else f"Sheet{sheet_idx + 1}"
                )
                for chunk in self._chunk_table_in_memory(
                    headers, data_rows, sheet_name, idx, char_offset, sheet_idx
                ):
                    idx += 1
                    char_offset += len(chunk.text)
                    yield chunk

            t_mem = time.perf_counter() - t_mem_start
            logger.info(
                f"[TabularChunker] In-memory mode: {file_path or 'tables'}, "
                f"chunking={t_mem:.2f}s, total_chunks={idx}, mode={self.mode}"
            )
            return

        # ---- 混合模式：DuckDB 优先，失败用内存补充 ----
        conn = self._duckdb_conn
        safe_path = file_path.replace("'", "''")

        logger.info(
            "\n"
            "============================================================\n"
            "  🚀  TABULAR CHUNKER MODE: DUCKDB (DuckDB 高速模式)\n"
            f"  文件: {file_path}\n"
            f"  文件类型: {file_type}\n"
            f"  切片模式: {self.mode}\n"
            f"  连接类型: in-memory DuckDB + 文件虚拟表\n"
            "============================================================"
        )

        # 1. 先确定 sheet 列表（优先用传入的，否则从文件读取）
        if sheet_names:
            all_sheets = list(sheet_names)
        else:
            all_sheets = self._get_excel_sheets_duckdb(conn, safe_path, file_path)

        # 建立 sheet_name -> 内存 table 的映射（用于回退）
        mem_table_map: dict[str, list] = {}
        for i, sname in enumerate(sheet_names):
            if i < len(tables) and tables[i]:
                mem_table_map[sname] = tables[i]

        t_start = time.perf_counter()
        idx = 0
        char_offset = 0
        duckdb_sheets = 0
        memory_sheets = 0
        failed_sheets = 0

        for sheet_idx, sheet_name in enumerate(all_sheets):
            table_alias = f"sheet_{sheet_idx}"
            processed = False

            # 尝试 DuckDB
            try:
                if file_type in ("xlsx", "xls", "excel"):
                    conn.execute(
                        f"CREATE OR REPLACE TABLE {table_alias} AS "
                        f"SELECT * FROM read_xlsx('{safe_path}', sheet = '{sheet_name}')"
                    )
                elif file_type == "csv":
                    conn.execute(
                        f"CREATE OR REPLACE TABLE {table_alias} AS "
                        f"SELECT * FROM read_csv_auto('{safe_path}')"
                    )
                else:
                    conn.execute(
                        f"CREATE OR REPLACE TABLE {table_alias} AS "
                        f"SELECT * FROM read_csv_auto('{safe_path}')"
                    )

                # 获取列名
                cols_result = conn.execute(
                    f"SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table_alias}' ORDER BY ordinal_position"
                ).fetchall()
                headers = [c[0] for c in cols_result]

                if headers:
                    # 根据模式生成 chunks（DuckDB 路径）
                    for chunk in self._duckdb_chunk_by_mode(
                        conn, table_alias, headers, sheet_name,
                        idx, char_offset, sheet_idx,
                    ):
                        idx += 1
                        char_offset += len(chunk.text)
                        yield chunk
                    duckdb_sheets += 1
                    processed = True
            except Exception as e:
                logger.debug(
                    f"DuckDB failed to load sheet '{sheet_name}': {e}, "
                    f"falling back to in-memory table"
                )

            # DuckDB 失败，尝试内存回退
            if not processed:
                mem_table = mem_table_map.get(sheet_name)
                if mem_table and len(mem_table) >= 2:
                    headers = mem_table[0]
                    data_rows = mem_table[1:]
                    for chunk in self._chunk_table_in_memory(
                        headers, data_rows, sheet_name, idx, char_offset, sheet_idx
                    ):
                        idx += 1
                        char_offset += len(chunk.text)
                        yield chunk
                    memory_sheets += 1
                    processed = True

            if not processed:
                failed_sheets += 1

            # 清理临时表
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_alias}")
            except Exception:
                pass

        total_time = time.perf_counter() - t_start
        logger.info(
            f"[TabularChunker] Hybrid mode: {file_path}, "
            f"total={total_time:.2f}s, chunks={idx}, mode={self.mode}, "
            f"sheets: duckdb={duckdb_sheets}, memory={memory_sheets}, failed={failed_sheets}"
        )

    # ------------------------------------------------------------------
    # DuckDB 高速查询模式
    # ------------------------------------------------------------------
    def _try_init_duckdb(self) -> bool:
        """尝试初始化 DuckDB 连接，成功返回 True"""
        if self._duckdb_conn is not None:
            return True
        try:
            import duckdb

            self._duckdb_conn = duckdb.connect()
            return True
        except ImportError:
            logger.debug("duckdb not available, will use in-memory table processing")
            return False

    def _chunk_with_duckdb(
        self, file_path: str, file_type: str
    ) -> Iterator[ChunkItem]:
        """
        使用 DuckDB 直接从文件查询并切片。

        支持：
        - xlsx: 用 read_xlsx() 函数
        - csv: 用 read_csv_auto() 函数
        """
        conn = self._duckdb_conn

        # 安全化文件名（避免 SQL 注入）
        safe_path = file_path.replace("'", "''")

        # 获取 sheet 列表（Excel）
        if file_type in ("xlsx", "xls", "excel"):
            sheets = self._get_excel_sheets_duckdb(conn, safe_path, file_path)
        elif file_type == "csv":
            sheets = ["csv_data"]
        else:
            sheets = ["data"]

        chunk_idx = 0
        char_offset = 0

        for sheet_idx, sheet_name in enumerate(sheets):
            table_alias = f"sheet_{sheet_idx}"

            try:
                # 注册虚拟表
                if file_type in ("xlsx", "xls", "excel"):
                    conn.execute(
                        f"CREATE OR REPLACE TABLE {table_alias} AS "
                        f"SELECT * FROM read_xlsx('{safe_path}', sheet = '{sheet_name}')"
                    )
                elif file_type == "csv":
                    conn.execute(
                        f"CREATE OR REPLACE TABLE {table_alias} AS "
                        f"SELECT * FROM read_csv_auto('{safe_path}')"
                    )
            except Exception as e:
                logger.debug(f"DuckDB failed to load sheet '{sheet_name}': {e}")
                continue

            # 获取列名
            try:
                cols_result = conn.execute(
                    f"SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table_alias}' ORDER BY ordinal_position"
                ).fetchall()
                headers = [c[0] for c in cols_result]
            except Exception:
                headers = []

            if not headers:
                try:
                    conn.execute(f"DROP TABLE IF EXISTS {table_alias}")
                except Exception:
                    pass
                continue

            # 根据模式生成 chunks
            if self.mode == "row":
                yield from self._duckdb_chunk_rows(
                    conn, table_alias, headers, sheet_name,
                    chunk_idx, char_offset, sheet_idx,
                )
            elif self.mode == "row_group":
                yield from self._duckdb_chunk_row_groups(
                    conn, table_alias, headers, sheet_name,
                    chunk_idx, char_offset, sheet_idx,
                )
            elif self.mode == "column_group":
                yield from self._duckdb_chunk_column_groups(
                    conn, table_alias, headers, sheet_name,
                    chunk_idx, char_offset, sheet_idx,
                )
            elif self.mode == "sql" and self.sql_template:
                yield from self._duckdb_chunk_sql(
                    conn, table_alias, headers, sheet_name,
                    chunk_idx, char_offset, sheet_idx,
                )
            else:
                yield from self._duckdb_chunk_rows(
                    conn, table_alias, headers, sheet_name,
                    chunk_idx, char_offset, sheet_idx,
                )

            # 清理临时表
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_alias}")
            except Exception:
                pass

    def _verify_duckdb_can_load_all_sheets(
        self, file_path: str, file_type: str, sheet_names: list[str]
    ) -> bool:
        """
        预检查：验证所有 sheet 都能被 DuckDB 成功加载。

        如果有任何一个 sheet 加载失败，返回 False，
        调用方应该回退到内存表格处理模式。

        这样做的目的是避免 DuckDB 模式下部分 sheet 被静默跳过，
        导致切片数据不完整（大 sheet 因类型推断失败被跳过的问题）。
        """
        if file_type not in ("xlsx", "xls", "excel"):
            return True  # CSV 等一般不会有问题

        conn = self._duckdb_conn
        if conn is None:
            return False

        safe_path = file_path.replace("'", "''")

        for sheet_name in sheet_names:
            try:
                # 尝试读取 1 行来验证 sheet 是否能被成功加载
                conn.execute(
                    f"SELECT 1 FROM read_xlsx('{safe_path}', sheet = '{sheet_name}') LIMIT 1"
                ).fetchone()
            except Exception as e:
                logger.debug(
                    f"DuckDB failed to load sheet '{sheet_name}': {e}. "
                    f"Will fall back to in-memory tables for all sheets."
                )
                return False

        return True

    def _get_excel_sheets_duckdb(
        self, conn, safe_path: str, file_path: str
    ) -> list[str]:
        """获取 Excel 文件的 sheet 列表"""
        # 方法1：尝试 DuckDB 列出 sheet
        try:
            result = conn.execute(
                f"SELECT sheet_name FROM (SELECT * FROM read_xlsx('{safe_path}')) "
                f"LIMIT 0"
            ).description
            # 上面的方法拿不到 sheet 列表，改用 openpyxl
        except Exception:
            pass

        # 方法2：用 openpyxl 读取 sheet 名（稳定可靠）
        try:
            from openpyxl import load_workbook

            wb = load_workbook(file_path, read_only=True)
            sheets = wb.sheetnames
            wb.close()
            if sheets:
                return sheets
        except Exception:
            pass

        # 兜底
        return ["Sheet1"]

    def _duckdb_chunk_by_mode(
        self, conn, table_alias: str, headers: list, sheet_name: str,
        start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """单个 sheet 的 DuckDB 模式分块（根据 self.mode 分发）"""
        if self.mode == "row":
            yield from self._duckdb_chunk_rows(
                conn, table_alias, headers, sheet_name,
                start_idx, char_offset, sheet_idx,
            )
        elif self.mode == "row_group":
            yield from self._duckdb_chunk_row_groups(
                conn, table_alias, headers, sheet_name,
                start_idx, char_offset, sheet_idx,
            )
        elif self.mode == "column_group":
            yield from self._duckdb_chunk_column_groups(
                conn, table_alias, headers, sheet_name,
                start_idx, char_offset, sheet_idx,
            )
        elif self.mode == "sql" and self.sql_template:
            yield from self._duckdb_chunk_sql(
                conn, table_alias, headers, sheet_name,
                start_idx, char_offset, sheet_idx,
            )
        else:
            yield from self._duckdb_chunk_rows(
                conn, table_alias, headers, sheet_name,
                start_idx, char_offset, sheet_idx,
            )

    def _duckdb_chunk_rows(
        self, conn, table_alias: str, headers: list,
        sheet_name: str, start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """DuckDB 行级切片：每行一个 chunk"""
        query = f"SELECT * FROM {table_alias}"
        rows = conn.execute(query).fetchall()

        idx = start_idx
        offset = char_offset

        for row in rows:
            row_parts = []
            for i, val in enumerate(row):
                if val is not None and str(val).strip() and i < len(headers):
                    row_parts.append(f"{headers[i]}: {val}")
            if not row_parts:
                continue

            if self.include_headers:
                text = (
                    f"# Sheet: {sheet_name}\n"
                    f"表头: {' | '.join(str(h) for h in headers)}\n"
                    + "; ".join(row_parts)
                )
            else:
                text = "; ".join(row_parts)

            yield ChunkItem(
                text=text.strip(),
                index=idx,
                char_start=offset,
                char_end=offset + len(text),
                page_num=sheet_idx,
            )
            idx += 1
            offset += len(text)

    def _duckdb_chunk_row_groups(
        self, conn, table_alias: str, headers: list,
        sheet_name: str, start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """DuckDB 行组切片：每 N 行一个 chunk"""
        rows_per_chunk = self.rows_per_chunk or self._estimate_rows_per_chunk(headers)

        # 获取总行数
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {table_alias}").fetchone()[0]
        if total_rows == 0:
            return

        idx = start_idx
        offset = char_offset
        overlap = max(0, self.chunk_overlap // max(1, rows_per_chunk))

        start = 0
        while start < total_rows:
            query = (
                f"SELECT * FROM {table_alias} "
                f"LIMIT {rows_per_chunk} OFFSET {start}"
            )
            rows = conn.execute(query).fetchall()
            if not rows:
                break

            text = self._build_group_text(
                headers, rows, sheet_name,
                row_start=start, row_end=start + len(rows),
            )
            yield ChunkItem(
                text=text.strip(),
                index=idx,
                char_start=offset,
                char_end=offset + len(text),
                page_num=sheet_idx,
            )
            idx += 1
            offset += len(text)

            end = start + len(rows)
            if end >= total_rows:
                break
            start = end - overlap
            if start <= 0 or start >= end:
                start = end

    def _duckdb_chunk_column_groups(
        self, conn, table_alias: str, headers: list,
        sheet_name: str, start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """DuckDB 列组切片：按列分组，每组包含所有行的部分列"""
        if not headers:
            return

        cols_per_group = self.columns_per_group
        idx = start_idx
        offset = char_offset
        group_idx = 0
        col_start = 0

        while col_start < len(headers):
            group_headers = headers[col_start:col_start + cols_per_group]
            # 列名转义
            safe_headers = [f'"{h}"' for h in group_headers]
            query = f"SELECT {', '.join(safe_headers)} FROM {table_alias}"
            rows = conn.execute(query).fetchall()

            if rows:
                text = self._build_group_text(
                    group_headers, rows, sheet_name,
                    group_idx=group_idx, col_group=True,
                )
                yield ChunkItem(
                    text=text.strip(),
                    index=idx,
                    char_start=offset,
                    char_end=offset + len(text),
                    page_num=sheet_idx,
                )
                idx += 1
                offset += len(text)

            col_start += cols_per_group
            group_idx += 1

    def _duckdb_chunk_sql(
        self, conn, table_alias: str, headers: list,
        sheet_name: str, start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """DuckDB 自定义 SQL 切片"""
        sql = self.sql_template.replace("{table}", table_alias)
        try:
            result = conn.execute(sql).fetchall()
            col_names = [d[0] for d in conn.description]
        except Exception as e:
            logger.warning(f"Custom SQL execution failed: {e}")
            return

        idx = start_idx
        offset = char_offset

        for row in result:
            row_parts = []
            for i, val in enumerate(row):
                if val is not None and str(val).strip():
                    col_name = col_names[i] if i < len(col_names) else f"col_{i}"
                    row_parts.append(f"{col_name}: {val}")
            if not row_parts:
                continue

            if self.include_headers:
                sql_preview = self.sql_template[:80] if self.sql_template else ""
                text = (
                    f"# Sheet: {sheet_name} (SQL result)\n"
                    f"查询: {sql_preview}\n"
                    + "; ".join(row_parts)
                )
            else:
                text = "; ".join(row_parts)

            yield ChunkItem(
                text=text.strip(),
                index=idx,
                char_start=offset,
                char_end=offset + len(text),
                page_num=sheet_idx,
            )
            idx += 1
            offset += len(text)

    # ------------------------------------------------------------------
    # 内存表格处理模式（DuckDB 不可用时的回退方案）
    # ------------------------------------------------------------------
    def _chunk_rows(
        self, headers: list, data_rows: list, sheet_name: str,
        start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """行级切片：每行一个 chunk"""
        idx = start_idx
        offset = char_offset

        for row in data_rows:
            row_parts = []
            for i, val in enumerate(row):
                if val and i < len(headers):
                    row_parts.append(f"{headers[i]}: {val}")
            if not row_parts:
                continue

            if self.include_headers:
                text = (
                    f"# Sheet: {sheet_name}\n"
                    f"表头: {' | '.join(headers)}\n"
                    + "; ".join(row_parts)
                )
            else:
                text = "; ".join(row_parts)

            yield ChunkItem(
                text=text.strip(),
                index=idx,
                char_start=offset,
                char_end=offset + len(text),
                page_num=sheet_idx,
            )
            idx += 1
            offset += len(text)

    def _chunk_table_in_memory(
        self, headers: list, data_rows: list, sheet_name: str,
        start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """单个 sheet 的内存模式分块（根据 self.mode 分发）"""
        if self.mode == "row_group":
            yield from self._chunk_row_groups(
                headers, data_rows, sheet_name, start_idx, char_offset, sheet_idx
            )
        elif self.mode == "column_group":
            yield from self._chunk_column_groups(
                headers, data_rows, sheet_name, start_idx, char_offset, sheet_idx
            )
        else:
            yield from self._chunk_rows(
                headers, data_rows, sheet_name, start_idx, char_offset, sheet_idx
            )

    def _chunk_row_groups(
        self, headers: list, data_rows: list, sheet_name: str,
        start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """行组切片：每 N 行一个 chunk"""
        if not data_rows:
            return

        rows_per_chunk = self.rows_per_chunk or self._estimate_rows_per_chunk(headers)
        total = len(data_rows)
        idx = start_idx
        offset = char_offset
        overlap_rows = max(0, self.chunk_overlap // max(1, rows_per_chunk))

        start = 0
        while start < total:
            end = min(start + rows_per_chunk, total)
            group = data_rows[start:end]
            text = self._build_group_text(
                headers, group, sheet_name,
                row_start=start, row_end=end,
            )
            yield ChunkItem(
                text=text.strip(),
                index=idx,
                char_start=offset,
                char_end=offset + len(text),
                page_num=sheet_idx,
            )
            idx += 1
            offset += len(text)
            if end >= total:
                break
            start = end - overlap_rows
            if start <= 0 or start >= end:
                start = end

    def _chunk_column_groups(
        self, headers: list, data_rows: list, sheet_name: str,
        start_idx: int, char_offset: int, sheet_idx: int,
    ) -> Iterator[ChunkItem]:
        """列组切片：按列分组，每组包含所有行的部分列"""
        if not headers or not data_rows:
            return

        cols_per_group = self.columns_per_group
        idx = start_idx
        offset = char_offset
        group_idx = 0
        col_start = 0

        while col_start < len(headers):
            group_headers = headers[col_start:col_start + cols_per_group]
            group_rows = []
            for row in data_rows:
                group_row = row[col_start:col_start + cols_per_group]
                group_rows.append(group_row)

            text = self._build_group_text(
                group_headers, group_rows, sheet_name,
                group_idx=group_idx, col_group=True,
            )
            yield ChunkItem(
                text=text.strip(),
                index=idx,
                char_start=offset,
                char_end=offset + len(text),
                page_num=sheet_idx,
            )
            idx += 1
            offset += len(text)
            col_start += cols_per_group
            group_idx += 1

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _estimate_rows_per_chunk(self, headers: list) -> int:
        """根据 chunk_size 估算每组行数"""
        if not headers:
            return 10
        # 假设每行平均字符数 = 表头总字符数 * 2（粗略估算）
        avg_header_len = sum(len(str(h)) for h in headers) / len(headers)
        est_row_chars = avg_header_len * 2 + 30  # 加上值的平均长度
        est_rows = max(1, int(self.chunk_size / max(1, est_row_chars)))
        # 合理范围：1 ~ 200
        return max(1, min(200, est_rows))

    def _build_group_text(
        self,
        headers: list,
        rows: list,
        sheet_name: str,
        row_start: int = 0,
        row_end: int = 0,
        group_idx: int = 0,
        col_group: bool = False,
    ) -> str:
        """构建行组/列组文本（性能优化版）"""
        parts = []
        n_cols = len(headers)

        if self.include_headers:
            parts.append(f"# Sheet: {sheet_name}")
            if col_group:
                parts.append(f"列组 {group_idx + 1}（共 {n_cols} 列）")
            else:
                parts.append(f"第 {row_start + 1}-{row_end} 行（共 {len(rows)} 行）")
            parts.append("表头: " + " | ".join(str(h) for h in headers))

        # 预计算表头前缀，每行直接复用（避免每行重复格式化列名）
        header_prefixes = [f"{h}: " for h in headers]

        for row in rows:
            # 用 zip 绑定列，减少索引边界检查；直接用 val（table_data 已保证是 str）
            row_parts = [
                prefix + val
                for prefix, val in zip(header_prefixes, row)
                if val and val.strip()
            ]
            if row_parts:
                parts.append("; ".join(row_parts))

        return "\n".join(parts)

    def _fallback_text_chunk(self, text: str) -> list[ChunkItem]:
        """纯文本回退：简单按行分块"""
        if not text:
            return []
        lines = text.split("\n")
        chunks = []
        current = []
        current_len = 0
        idx = 0
        offset = 0

        for line in lines:
            line_len = len(line) + 1
            if current_len + line_len > self.chunk_size and current:
                chunk_text = "\n".join(current)
                chunks.append(ChunkItem(
                    text=chunk_text,
                    index=idx,
                    char_start=offset,
                    char_end=offset + len(chunk_text),
                ))
                idx += 1
                offset += len(chunk_text) + 1
                # 重叠：保留最后几行
                if self.chunk_overlap > 0 and len(current) > 1:
                    overlap_count = max(1, self.chunk_overlap // 20)
                    current = current[-overlap_count:]
                    current_len = sum(len(l) + 1 for l in current)
                else:
                    current = []
                    current_len = 0
            current.append(line)
            current_len += line_len

        if current:
            chunk_text = "\n".join(current)
            if chunk_text.strip():
                chunks.append(ChunkItem(
                    text=chunk_text,
                    index=idx,
                    char_start=offset,
                    char_end=offset + len(chunk_text),
                ))

        return chunks

    def close(self):
        """关闭 DuckDB 连接"""
        if self._duckdb_conn:
            try:
                self._duckdb_conn.close()
            except Exception:
                pass
            self._duckdb_conn = None
