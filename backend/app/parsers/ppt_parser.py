"""
PPT / PPTX 解析器
提取幻灯片文本、备注、表格
"""

import asyncio
import os
from typing import List

from .base import BaseParser, ParseResult
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace

logger = get_logger(__name__)


class PPTParser(BaseParser):
    """PowerPoint 解析器"""

    def supported_types(self) -> List[str]:
        return ["pptx", "ppt"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing PowerPoint: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pptx":
            return self._parse_pptx(file_path)
        else:
            # .ppt 旧格式，尝试使用 python-pptx（部分兼容，不行则用 LibreOffice 回退
            try:
                return self._parse_pptx(file_path)
            except Exception as e:
                logger.warning(f"python-pptx failed for .ppt: {e}, trying LibreOffice fallback")
                return self._parse_ppt_libreoffice(file_path)

    def _parse_pptx(self, file_path: str) -> ParseResult:
        """解析 .pptx（基于 python-pptx"""
        from pptx import Presentation

        prs = Presentation(file_path)
        pages = []  # 每页一个"页"=一张幻灯片
        all_tables = []

        for slide_idx, slide in enumerate(prs.slides):
            slide_parts = []
            slide_tables = []

            # 提取标题
            title = ""
            title_shape = None
            if slide.shapes.title:
                title_shape = slide.shapes.title
                if shape_has_text_frame(title_shape) and title_shape.text_frame:
                    title = title_shape.text_frame.text.strip()

            if title:
                slide_parts.append(f"# 第 {slide_idx + 1} 页: {title}")
            else:
                slide_parts.append(f"# 第 {slide_idx + 1} 页")

            # 提取文本内容
            for shape in slide.shapes:
                # 文本框文本
                if shape.has_text_frame:
                    text_frame = shape.text_frame
                    text = text_frame.text.strip()
                    if text and shape != title_shape:
                        # 按段落添加
                        paragraphs_text = []
                        for para in text_frame.paragraphs:
                            para_text = para.text.strip()
                            if para_text:
                                paragraphs_text.append(para_text)
                        if paragraphs_text:
                            slide_parts.append("\n".join(paragraphs_text))

                # 表格
                if shape.has_table:
                    table = shape.table
                    table_data = []
                    for row in table.rows:
                        row_data = [cell.text.strip() for cell in row.cells]
                        table_data.append(row_data)
                    slide_tables.append(table_data)
                    all_tables.append(table_data)

                    # 将表格转换为描述性文本
                    if table_data:
                        slide_parts.append("【表格】")
                        headers = table_data[0]
                        slide_parts.append("表头: " + " | ".join(headers))
                        for row in table_data[1:]:
                            line_parts = []
                            for i, val in enumerate(row):
                                if val and i < len(headers):
                                    line_parts.append(f"{headers[i]}: {val}")
                            if line_parts:
                                slide_parts.append("; ".join(line_parts))
                        slide_parts.append("")

            # 备注
            if slide.has_notes_slide:
                notes_slide = slide.notes_slide
                notes_text = notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    slide_parts.append("")
                    slide_parts.append(f"【备注】\n{notes_text}")

            # Keep one page slot per slide, including title-only slides.
            slide_text = "\n".join(slide_parts)
            pages.append(slide_text)

        full_text = "\n\n".join(pages)
        full_text = normalize_text(full_text)
        full_text = clean_whitespace(full_text)

        # 元数据
        metadata = {
            "slide_count": len(prs.slides),
            "file_name": os.path.basename(file_path),
        }

        return ParseResult(
            text=full_text,
            pages=pages,
            metadata=metadata,
            tables=all_tables,
        )

    def _parse_ppt_libreoffice(self, file_path: str) -> ParseResult:
        """旧 .ppt 格式：尝试用 LibreOffice 转换为 .pptx 再解析"""
        import subprocess
        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="ppt_convert_")
        try:
            # 调用 libreoffice 转换
            result = subprocess.run(
                ["soffice", "--headless", "--convert-to", "pptx",
                "--outdir", tmp_dir, file_path,
            ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error(f"LibreOffice conversion failed: {result.stderr}")
                return ParseResult(
                    text="",
                    pages=[],
                    metadata={"error": f"LibreOffice conversion failed: {result.stderr[:200]}"},
                )

            # 找转换后的文件
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            converted = os.path.join(tmp_dir, f"{base_name}.pptx")
            if not os.path.exists(converted):
                # 尝试找任何 .pptx 文件
                for f in os.listdir(tmp_dir):
                    if f.endswith(".pptx"):
                        converted = os.path.join(tmp_dir, f)
                        break

            if os.path.exists(converted):
                return self._parse_pptx(converted)
            else:
                return ParseResult(
                    text="", pages=[],
                    metadata={"error": "Converted file not found"},
                )
        except FileNotFoundError:
            logger.warning("LibreOffice not found, .ppt files cannot be parsed")
            return ParseResult(
                text="", pages=[],
                metadata={"error": "LibreOffice not available for .ppt conversion"},
            )
        except Exception as e:
            logger.error(f"PPT conversion error: {e}")
            return ParseResult(
                text="", pages=[],
                metadata={"error": str(e)[:200]},
            )
        finally:
            import shutil
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


def shape_has_text_frame(shape):
    """安全判断 shape 是否有 text_frame"""
    try:
        return shape.has_text_frame
    except Exception:
        return False
