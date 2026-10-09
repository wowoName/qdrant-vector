"""
音频解析器（语音转文字 ASR）
支持 mp3、wav、m4a、flac、aac、ogg、wma 等常见格式

技术方案（多级降级）：
1. faster-whisper（推荐，CPU/GPU 都快，中文好，纯离线）
2. openai-whisper（次选，官方实现）
3. SpeechRecognition + 在线服务（兜底，需要网络）

长音频处理：自动按段落切分，保留时间戳
"""

import asyncio
import os
import tempfile
from typing import List

from .base import BaseParser, ParseResult
from app.core.config import settings
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace

logger = get_logger(__name__)

AUDIO_EXTENSIONS = {
    "mp3", "wav", "m4a", "flac", "aac", "ogg", "wma", "opus", "amr",
}

# ASR 引擎缓存
_asr_engine = None
_asr_backend = None  # "faster_whisper" | "whisper" | "none"


def _get_asr_engine():
    """
    获取 ASR 引擎实例
    优先级: faster-whisper > openai-whisper > none
    """
    global _asr_engine, _asr_backend
    if _asr_engine is not None or _asr_backend == "none":
        return _asr_engine, _asr_backend

    model_name = getattr(settings, "asr_model", "small")  # tiny/base/small/medium/large

    # 1. 尝试 faster-whisper（推荐，更快更省内存）
    try:
        from faster_whisper import WhisperModel
        device = getattr(settings, "asr_device", "auto")
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"

        compute_type = "int8" if device == "cpu" else "float16"
        _asr_engine = WhisperModel(model_name, device=device, compute_type=compute_type)
        _asr_backend = "faster_whisper"
        logger.info(f"ASR backend: faster-whisper ({model_name}, {device})")
        return _asr_engine, _asr_backend
    except ImportError:
        logger.debug("faster-whisper not available")
    except Exception as e:
        logger.warning(f"faster-whisper init failed: {e}")

    # 2. 尝试 openai-whisper（官方实现）
    try:
        import whisper
        _asr_engine = whisper.load_model(model_name)
        _asr_backend = "whisper"
        logger.info(f"ASR backend: openai-whisper ({model_name})")
        return _asr_engine, _asr_backend
    except ImportError:
        logger.debug("openai-whisper not available")
    except Exception as e:
        logger.warning(f"whisper init failed: {e}")

    _asr_backend = "none"
    logger.warning(
        "No ASR backend available. Install faster-whisper for best results: "
        "pip install faster-whisper"
    )
    return None, _asr_backend


class AudioParser(BaseParser):
    """音频解析器（语音转文字）"""

    def supported_types(self) -> List[str]:
        return ["audio"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing audio: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        engine, backend = _get_asr_engine()
        if engine is None or backend == "none":
            return ParseResult(
                text="",
                pages=[],
                metadata={
                    "error": (
                        "No ASR backend available. "
                        "Install faster-whisper: pip install faster-whisper"
                    ),
                },
            )

        filename = os.path.basename(file_path)

        try:
            if backend == "faster_whisper":
                return self._transcribe_faster_whisper(file_path, engine, filename)
            elif backend == "whisper":
                return self._transcribe_whisper(file_path, engine, filename)
        except Exception as e:
            logger.error(f"Audio transcription failed: {e}")
            return ParseResult(
                text="",
                pages=[],
                metadata={"error": f"Transcription failed: {str(e)[:200]}", "file_name": filename},
            )

        return ParseResult(text="", pages=[], metadata={"file_name": filename})

    def _transcribe_faster_whisper(
        self, file_path: str, model, filename: str
    ) -> ParseResult:
        """使用 faster-whisper 转写"""
        language = getattr(settings, "asr_language", None)  # None=自动检测

        segments, info = model.transcribe(
            file_path,
            language=language,
            beam_size=5,
            vad_filter=True,  # 启用 VAD，跳过静音段
        )

        pages = []      # 按时间段分"页"
        all_texts = []
        current_segment_texts = []
        current_duration = 0.0
        page_duration = 60.0  # 每 60 秒一页

        for seg in segments:
            seg_text = seg.text.strip()
            if not seg_text:
                continue

            current_segment_texts.append(seg_text)
            current_duration = seg.end

            # 累积到 page_duration 就新建一页
            if current_duration >= page_duration:
                page_text = " ".join(current_segment_texts)
                page_text = normalize_text(page_text)
                page_text = clean_whitespace(page_text)
                ts_start = self._format_timestamp(seg.start - current_duration)
                ts_end = self._format_timestamp(seg.end)
                page_header = f"[音频 {ts_start} - {ts_end}]"
                pages.append(f"{page_header}\n{page_text}")
                all_texts.append(page_text)
                current_segment_texts = []
                current_duration = 0.0

        # 最后一段
        if current_segment_texts:
            page_text = " ".join(current_segment_texts)
            page_text = normalize_text(page_text)
            page_text = clean_whitespace(page_text)
            if pages:
                ts_start = self._format_timestamp(info.duration - len(current_segment_texts) * 5)
            else:
                ts_start = "00:00"
            ts_end = self._format_timestamp(info.duration)
            page_header = f"[音频 {ts_start} - {ts_end}]"
            pages.append(f"{page_header}\n{page_text}")
            all_texts.append(page_text)

        full_text = "\n\n".join(all_texts)
        full_text = normalize_text(full_text)
        full_text = clean_whitespace(full_text)

        return ParseResult(
            text=full_text,
            pages=pages,
            metadata={
                "asr_engine": "faster-whisper",
                "language": info.language,
                "duration": round(info.duration, 2),
                "file_name": filename,
                "segment_count": len(pages),
            },
        )

    def _transcribe_whisper(
        self, file_path: str, model, filename: str
    ) -> ParseResult:
        """使用 openai-whisper 转写"""
        language = getattr(settings, "asr_language", None)

        result = model.transcribe(
            file_path,
            language=language,
            verbose=False,
        )

        segments = result.get("segments", [])
        pages = []
        all_texts = []
        current_segment_texts = []
        current_duration = 0.0
        page_duration = 60.0

        for seg in segments:
            seg_text = seg.get("text", "").strip()
            if not seg_text:
                continue

            current_segment_texts.append(seg_text)
            current_duration = seg.get("end", 0)

            if current_duration >= page_duration:
                page_text = " ".join(current_segment_texts)
                page_text = normalize_text(page_text)
                page_text = clean_whitespace(page_text)
                ts_start = self._format_timestamp(seg.get("start", 0) - current_duration)
                ts_end = self._format_timestamp(seg.get("end", 0))
                page_header = f"[音频 {ts_start} - {ts_end}]"
                pages.append(f"{page_header}\n{page_text}")
                all_texts.append(page_text)
                current_segment_texts = []
                current_duration = 0.0

        if current_segment_texts:
            page_text = " ".join(current_segment_texts)
            page_text = normalize_text(page_text)
            page_text = clean_whitespace(page_text)
            duration = result.get("duration", 0)
            ts_end = self._format_timestamp(duration)
            page_header = f"[音频 - {ts_end}]"
            pages.append(f"{page_header}\n{page_text}")
            all_texts.append(page_text)

        full_text = result.get("text", "")
        full_text = normalize_text(full_text)
        full_text = clean_whitespace(full_text)

        return ParseResult(
            text=full_text,
            pages=pages,
            metadata={
                "asr_engine": "openai-whisper",
                "language": result.get("language", ""),
                "duration": round(result.get("duration", 0), 2),
                "file_name": filename,
                "segment_count": len(pages),
            },
        )

    def _format_timestamp(self, seconds: float) -> str:
        """秒数格式化为 MM:SS 或 HH:MM:SS"""
        seconds = max(0, int(seconds))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
