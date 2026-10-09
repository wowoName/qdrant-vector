"""
CSV 解析器
自动检测编码，解析 CSV 为描述性文本和表格数据
"""

import asyncio
import csv
import io
from typing import List

from .base import BaseParser, ParseResult
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text
from app.utils.file_utils import read_text_file

logger = get_logger(__name__)


class CSVParser(BaseParser):
    """CSV 表格解析器"""

    def supported_types(self) -> List[str]:
        return ["csv"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing CSV: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        # 自动检测编码读取
        content, encoding = read_text_file(file_path)
        content = normalize_text(content)

        # 尝试自动检测分隔符
        delimiter = self._detect_delimiter(content)

        # 解析 CSV
        rows = []
        try:
            reader = csv.reader(io.StringIO(content), delimiter=delimiter)
            for row in reader:
                # 清理每个单元格
                cleaned = [cell.strip() for cell in row]
                # 跳过全空行
                if any(cell for cell in cleaned):
                    rows.append(cleaned)
        except csv.Error as e:
            logger.warning(f"CSV parse error with delimiter '{delimiter}': {e}, falling back to comma")
            # 回退到逗号
            try:
                reader = csv.reader(io.StringIO(content))
                rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
            except Exception as e2:
                logger.error(f"CSV parse failed: {e2}")
                rows = []

        if not rows:
            return ParseResult(
                text="",
                pages=[],
                metadata={"encoding": encoding, "delimiter": delimiter, "row_count": 0},
                tables=[],
            )

        # 转换为描述性文本
        headers = rows[0] if rows else []
        data_rows = rows[1:] if len(rows) > 1 else []

        # 构建全文（每行：列名: 值）
        text_parts = []
        if headers and data_rows:
            text_parts.append("表头: " + " | ".join(headers))
            for row in data_rows:
                line_parts = []
                for i, val in enumerate(row):
                    if val and i < len(headers):
                        line_parts.append(f"{headers[i]}: {val}")
                if line_parts:
                    text_parts.append("; ".join(line_parts))
        else:
            # 无表头，直接拼接
            for row in rows:
                text_parts.append(" | ".join(row))

        full_text = "\n".join(text_parts)

        # 分页：按行分组，每页 50 行
        page_size = 50
        pages = []
        if data_rows:
            for i in range(0, len(data_rows), page_size):
                chunk = data_rows[i:i + page_size]
                page_parts = [f"# 第 {i // page_size + 1} 部分（共 {len(data_rows)} 行）"]
                page_parts.append("表头: " + " | ".join(headers))
                for row in chunk:
                    line_parts = []
                    for j, val in enumerate(row):
                        if val and j < len(headers):
                            line_parts.append(f"{headers[j]}: {val}")
                    if line_parts:
                        page_parts.append("; ".join(line_parts))
                pages.append("\n".join(page_parts))
        else:
            pages = [full_text] if full_text else []

        metadata = {
            "encoding": encoding,
            "delimiter": delimiter,
            "row_count": len(rows),
            "column_count": len(headers),
            "headers": headers,
        }

        return ParseResult(
            text=full_text,
            pages=pages,
            metadata=metadata,
            tables=[rows],
        )

    def _detect_delimiter(self, content: str) -> str:
        """
        自动检测 CSV 分隔符
        优先尝试逗号、制表符、分号
        """
        import csv
        # 取前几行做样本
        sample_lines = content.splitlines()[:20]
        sample = "\n".join(sample_lines)

        candidates = [",", "\t", ";", "|"]
        best_delimiter = ","
        max_fields = 0

        for delim in candidates:
            try:
                reader = csv.reader(io.StringIO(sample), delimiter=delim)
                field_counts = [len(row) for row in reader if row]
                if field_counts:
                    avg_fields = sum(field_counts) / len(field_counts)
                    # 字段数 > 1 且一致性高才算有效
                    if avg_fields > max_fields and all(c == field_counts[0] for c in field_counts):
                        max_fields = avg_fields
                        best_delimiter = delim
            except Exception:
                continue

        return best_delimiter
