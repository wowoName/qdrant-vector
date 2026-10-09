"""
DOCX 解析器
"""

import asyncio
from typing import List

from .base import BaseParser, ParseResult
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace

logger = get_logger(__name__)


class DOCXParser(BaseParser):
    """Word 文档解析器"""

    def supported_types(self) -> List[str]:
        return ["docx", "doc"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing DOCX: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        import os
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".doc":
            # 旧版 .doc 格式，先尝试 docx2txt/antiword/LibreOffice 转换
            return self._parse_doc(file_path)

        return self._parse_docx(file_path)

    def _parse_docx(self, file_path: str) -> ParseResult:
        from docx import Document

        doc = Document(file_path)
        paragraphs = []
        pages = []  # 按标题分段
        current_section = []

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                if current_section:
                    paragraphs.append("")
                continue

            # 检测标题层级
            style = para.style.name if para.style else ""
            if style.startswith("Heading"):
                # 保存上一个 section
                if current_section:
                    pages.append("\n".join(current_section))
                    current_section = []
                # 添加标题标记
                level = style.replace("Heading", "").strip()
                prefix = "#" * min(int(level) if level.isdigit() else 1, 6)
                paragraphs.append(f"{prefix} {text}")
                current_section.append(f"{prefix} {text}")
            else:
                paragraphs.append(text)
                current_section.append(text)

        # 最后一个 section
        if current_section:
            pages.append("\n".join(current_section))

        # 提取表格
        tables = []
        for table in doc.tables:
            table_data = []
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                table_data.append(row_data)
            tables.append(table_data)

        full_text = "\n".join(paragraphs)
        full_text = normalize_text(full_text)
        full_text = clean_whitespace(full_text)

        # 元数据
        metadata = {}
        core_props = doc.core_properties
        if core_props.title:
            metadata["title"] = core_props.title
        if core_props.author:
            metadata["author"] = core_props.author

        return ParseResult(
            text=full_text,
            pages=pages if pages else [full_text],
            metadata=metadata,
            tables=tables,
        )

    def _parse_doc(self, file_path: str) -> ParseResult:
        """
        解析旧版 .doc 文件（二进制 OLE 格式，不能直接按文本编码 decode）
        优先级（Windows）: win32com (Word COM) > docx2txt > LibreOffice 转换 > olefile 提取
        优先级（非 Windows）: docx2txt > LibreOffice 转换 > olefile 提取
        """
        import os
        import tempfile
        import subprocess
        import shutil
        import sys

        filename = os.path.basename(file_path)

        # 1. Windows 下优先用 win32com 调用本机 Word（最可靠）
        if sys.platform == "win32":
            try:
                text = self._parse_doc_with_win32com(file_path)
                if text and text.strip():
                    text = normalize_text(text)
                    text = clean_whitespace(text)
                    pages = [p.strip() for p in text.split("\n\n") if p.strip()]
                    logger.info(f"Parsed .doc with win32com: {filename}, {len(text)} chars")
                    return ParseResult(
                        text=text,
                        pages=pages if pages else [text],
                        metadata={"file_name": filename, "converter": "win32com_word"},
                    )
            except ImportError:
                logger.debug("win32com not available")
            except Exception as e:
                logger.debug(f"win32com .doc parse failed: {e}")

        # 2. 尝试 docx2txt
        try:
            import docx2txt
            text = docx2txt.process(file_path)
            if text and text.strip():
                text = normalize_text(text)
                text = clean_whitespace(text)
                pages = [p.strip() for p in text.split("\n\n") if p.strip()]
                logger.info(f"Parsed .doc with docx2txt: {filename}, {len(text)} chars")
                return ParseResult(
                    text=text,
                    pages=pages if pages else [text],
                    metadata={"file_name": filename, "converter": "docx2txt"},
                )
        except ImportError:
            logger.debug("docx2txt not available")
        except Exception as e:
            logger.debug(f"docx2txt failed: {e}")

        # 3. 尝试 LibreOffice 转换为 .docx 后解析
        tmp_dir = tempfile.mkdtemp(prefix="doc_convert_")
        try:
            result = subprocess.run(
                ["soffice", "--headless", "--convert-to", "docx",
                 "--outdir", tmp_dir, file_path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                base_name = os.path.splitext(filename)[0]
                converted = os.path.join(tmp_dir, f"{base_name}.docx")
                if not os.path.exists(converted):
                    # 查找转换后的文件
                    for f in os.listdir(tmp_dir):
                        if f.endswith(".docx"):
                            converted = os.path.join(tmp_dir, f)
                            break
                if os.path.exists(converted):
                    parsed = self._parse_docx(converted)
                    if parsed.metadata:
                        parsed.metadata["converter"] = "libreoffice"
                    else:
                        parsed.metadata = {"converter": "libreoffice"}
                    logger.info(f"Parsed .doc via LibreOffice: {filename}")
                    return parsed
        except FileNotFoundError:
            logger.debug("LibreOffice not available")
        except Exception as e:
            logger.debug(f"LibreOffice conversion failed: {e}")
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

        # 4. 尝试 olefile 提取 WordDocument 流中的文本
        try:
            text = self._extract_doc_with_olefile(file_path)
            if text and text.strip():
                text = normalize_text(text)
                text = clean_whitespace(text)
                pages = [p.strip() for p in text.split("\n\n") if p.strip()]
                logger.info(f"Parsed .doc with olefile: {filename}, {len(text)} chars")
                return ParseResult(
                    text=text,
                    pages=pages if pages else [text],
                    metadata={"file_name": filename, "converter": "olefile"},
                )
        except ImportError:
            logger.debug("olefile not available")
        except Exception as e:
            logger.debug(f"olefile .doc extraction failed: {e}")

        # 所有方案都失败
        logger.error(f"All .doc parsers failed for {filename}")
        return ParseResult(
            text="",
            pages=[],
            metadata={"file_name": filename, "error": "Failed to parse .doc file (all parsers failed)"},
        )

    def _parse_doc_with_win32com(self, file_path: str) -> str:
        """
        使用 win32com 调用本地 Microsoft Word 解析 .doc 文件
        仅在 Windows + 已安装 Word 时可用
        """
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        word = None
        doc = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0  # wdAlertsNone
            doc = word.Documents.Open(
                FileName=file_path,
                ReadOnly=True,
                ConfirmConversions=False,
                AddToRecentFiles=False,
            )
            # 提取全文
            text = doc.Content.Text
            return text
        finally:
            if doc is not None:
                try:
                    doc.Close(SaveChanges=0)  # wdDoNotSaveChanges
                except Exception:
                    pass
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()

    def _extract_doc_with_olefile(self, file_path: str) -> str:
        """
        使用 olefile 从 .doc（OLE Compound File）的 WordDocument 流中提取文本
        兜底方案，可能有少量格式残留，但比二进制乱码强
        """
        try:
            import olefile
        except ImportError:
            # 动态安装提示（不强制）
            raise

        if not olefile.isOleFile(file_path):
            raise ValueError("Not a valid OLE file")

        ole = olefile.OleFileIO(file_path)
        text_parts = []

        try:
            # 读取 WordDocument 流
            if ole.exists("WordDocument"):
                stream = ole.openstream("WordDocument")
                data = stream.read()

                # .doc 文件中，文本部分以多种编码存储
                # 尝试提取可读的中文和 ASCII 文本（过滤掉二进制格式控制符）
                import re
                # 提取所有可打印的 Unicode 字符范围
                # 先尝试 UTF-16 LE 解码（Word 内部常用）
                try:
                    decoded = data.decode("utf-16-le", errors="ignore")
                    # 过滤出可打印字符和常用标点
                    clean_chars = []
                    for ch in decoded:
                        if ch.isprintable() or ch in "\n\r\t":
                            clean_chars.append(ch)
                    text = "".join(clean_chars)
                    # 如果提取出了较多的有效中文/文字，就使用这个结果
                    chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
                    if chinese_count > 10 or len(text.strip()) > 50:
                        # 清理多余的空字符和异常段落
                        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
                        return text
                except Exception:
                    pass

                # 回退：尝试 GBK 解码提取
                try:
                    decoded = data.decode("gbk", errors="ignore")
                    clean_chars = []
                    for ch in decoded:
                        if ch.isprintable() or ch in "\n\r\t":
                            clean_chars.append(ch)
                    text = "".join(clean_chars)
                    chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
                    if chinese_count > 10 or len(text.strip()) > 50:
                        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
                        return text
                except Exception:
                    pass
        finally:
            ole.close()

        raise ValueError("No extractable text found in WordDocument stream")
