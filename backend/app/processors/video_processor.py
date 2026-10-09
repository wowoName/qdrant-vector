"""
视频文档处理器（抽帧 OCR + 音频 ASR）
"""

from .text_processor import TextDocumentProcessor


class VideoProcessor(TextDocumentProcessor):
    """视频专属处理器（抽帧OCR + 音频ASR → 文本切片）"""

    def _create_parser(self):
        from app.parsers.video_parser import VideoParser

        return VideoParser()

    def supported_types(self) -> list[str]:
        return ["mp4", "avi", "mkv", "mov", "flv", "wmv", "webm", "video"]
