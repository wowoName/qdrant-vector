"""
视频解析器
支持 mp4、avi、mkv、mov、webm 等格式
策略：按固定间隔抽帧 → OCR 提取画面文字 → 按时间轴组织文本
"""

import asyncio
import os
import tempfile
from typing import List

from .base import BaseParser, ParseResult
from .image_parser import _get_ocr_engine
from app.core.config import settings
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace

logger = get_logger(__name__)

VIDEO_EXTENSIONS = {
    "mp4", "avi", "mkv", "mov", "webm", "flv", "wmv", "m4v",
}


class VideoParser(BaseParser):
    """视频解析器（抽帧 OCR）"""

    def supported_types(self) -> List[str]:
        return ["video"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing video: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        engine, backend = _get_ocr_engine()
        if engine is None or backend == "none":
            return ParseResult(
                text="",
                pages=[],
                metadata={"error": "No OCR backend available for video text extraction."},
            )

        # 尝试用 moviepy / cv2 抽帧
        frames_info = self._extract_frames(file_path)
        if not frames_info:
            return ParseResult(
                text="",
                pages=[],
                metadata={"error": "Failed to extract frames from video"},
            )

        # 对每帧做 OCR
        page_texts = []
        all_texts = []

        for frame_path, timestamp_sec in frames_info:
            try:
                if backend == "rapidocr":
                    result, _ = engine(frame_path)
                    if result:
                        # 按 y 坐标排序后拼接
                        sorted_results = sorted(
                            result,
                            key=lambda item: (
                                sum(p[1] for p in item[0]) / 4,
                                sum(p[0] for p in item[0]) / 4,
                            ),
                        )
                        lines = [text for _, text, _ in sorted_results]
                        frame_text = "\n".join(lines)
                    else:
                        frame_text = ""
                elif backend == "pytesseract":
                    import pytesseract
                    from PIL import Image
                    img = Image.open(frame_path)
                    try:
                        frame_text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                    except Exception:
                        frame_text = ""
                else:
                    frame_text = ""

                if frame_text.strip():
                    frame_text = normalize_text(frame_text)
                    frame_text = clean_whitespace(frame_text)
                    # 带时间戳标记
                    ts = self._format_timestamp(timestamp_sec)
                    page_header = f"[时间 {ts}]"
                    page_text = f"{page_header}\n{frame_text}"
                    page_texts.append(page_text)
                    all_texts.append(page_text)
            except Exception as e:
                logger.warning(f"OCR failed for frame at {timestamp_sec}s: {e}")
            finally:
                # 清理临时帧文件
                try:
                    if os.path.exists(frame_path):
                        os.unlink(frame_path)
                except Exception:
                    pass

        full_text = "\n\n".join(all_texts)

        filename = os.path.basename(file_path)
        return ParseResult(
            text=full_text,
            pages=page_texts,
            metadata={
                "ocr_engine": backend,
                "frame_count": len(frames_info),
                "text_frames": len(page_texts),
                "file_name": filename,
            },
        )

    def _extract_frames(self, file_path: str) -> List[tuple]:
        """
        从视频中抽取关键帧
        返回: [(临时帧文件路径, 时间戳秒数), ...]
        优先使用 moviepy，回退到 opencv
        """
        # 1. 尝试 moviepy
        try:
            return self._extract_with_moviepy(file_path)
        except ImportError:
            logger.debug("moviepy not available, trying opencv")
        except Exception as e:
            logger.warning(f"moviepy frame extraction failed: {e}")

        # 2. 尝试 opencv
        try:
            return self._extract_with_opencv(file_path)
        except ImportError:
            logger.debug("opencv not available")
        except Exception as e:
            logger.warning(f"opencv frame extraction failed: {e}")

        logger.error("No video frame extraction library available")
        return []

    def _extract_with_moviepy(self, file_path: str) -> List[tuple]:
        """使用 moviepy 抽帧"""
        from moviepy.editor import VideoFileClip

        clip = VideoFileClip(file_path)
        duration = clip.duration  # 秒

        # 抽帧间隔：视频越长间隔越大，最多 30 帧
        max_frames = getattr(settings, "video_max_frames", 30)
        frame_interval = max(2.0, duration / max_frames)

        frames = []
        tmp_dir = tempfile.mkdtemp(prefix="video_frames_")
        t = 0.0
        idx = 0

        while t < duration:
            try:
                frame = clip.get_frame(t)
                frame_path = os.path.join(tmp_dir, f"frame_{idx:04d}.jpg")
                # 用 PIL 保存
                from PIL import Image
                img = Image.fromarray(frame)
                img.save(frame_path, "JPEG", quality=85)
                frames.append((frame_path, t))
                idx += 1
            except Exception:
                pass
            t += frame_interval

        clip.close()
        return frames

    def _extract_with_opencv(self, file_path: str) -> List[tuple]:
        """使用 opencv 抽帧"""
        import cv2

        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = total_frames / fps if fps > 0 else 0

        max_frames = getattr(settings, "video_max_frames", 30)
        frame_interval_sec = max(2.0, duration / max_frames) if duration > 0 else 2.0
        frame_interval = int(fps * frame_interval_sec)

        frames = []
        tmp_dir = tempfile.mkdtemp(prefix="video_frames_")
        idx = 0
        frame_num = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % frame_interval == 0:
                frame_path = os.path.join(tmp_dir, f"frame_{idx:04d}.jpg")
                cv2.imwrite(frame_path, frame)
                timestamp = frame_num / fps if fps > 0 else 0
                frames.append((frame_path, timestamp))
                idx += 1

                if idx >= max_frames:
                    break

            frame_num += 1

        cap.release()
        return frames

    def _format_timestamp(self, seconds: float) -> str:
        """秒数格式化为 HH:MM:SS"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
