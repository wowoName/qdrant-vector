"""
音频文档处理器（ASR 语音识别）
"""

from typing import List

from .text_processor import TextDocumentProcessor


class AudioProcessor(TextDocumentProcessor):
    """音频专属处理器（ASR 转文本后切片）"""

    def _create_parser(self):
        from app.parsers.audio_parser import AudioParser
        return AudioParser()

    def supported_types(self) -> List[str]:
        return ["mp3", "wav", "flac", "m4a", "aac", "ogg", "wma", "audio"]
