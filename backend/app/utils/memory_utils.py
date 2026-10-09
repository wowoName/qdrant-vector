"""
内存监控工具
用于大文件处理时检测内存使用情况，自适应调整策略
"""

import os
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


def get_memory_usage_mb() -> float:
    """
    获取当前进程的内存使用量（MB）。
    支持 Windows / Linux / macOS。
    """
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        return mem_info.rss / 1024 / 1024
    except ImportError:
        # psutil 不可用时用 ctypes（仅 Windows）
        try:
            import ctypes
            import ctypes.wintypes
            # GetProcessMemoryInfo
            psapi = ctypes.WinDLL('psapi')
            kernel32 = ctypes.WinDLL('kernel32')

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ('cb', ctypes.wintypes.DWORD),
                    ('PageFaultCount', ctypes.wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t),
                ]

            h_process = kernel32.GetCurrentProcess()
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            psapi.GetProcessMemoryInfo(h_process, ctypes.byref(pmc), pmc.cb)
            return pmc.WorkingSetSize / 1024 / 1024
        except Exception:
            return 0.0


def get_system_memory_info() -> dict:
    """
    获取系统内存信息（总量、可用量）。
    优先用 psutil，不可用时返回空 dict。
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "total_gb": round(mem.total / 1024 / 1024 / 1024, 1),
            "available_gb": round(mem.available / 1024 / 1024 / 1024, 1),
            "used_percent": mem.percent,
        }
    except ImportError:
        return {}


def is_memory_pressure_high(threshold_percent: float = 85.0) -> bool:
    """
    检查当前是否处于高内存压力下。
    返回 True 时，调用方应该考虑降低批次大小或切换到更保守的策略。
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.percent >= threshold_percent
    except ImportError:
        return False


class MemoryGuard:
    """
    内存卫士：监控处理过程中的内存使用，
    当内存压力过高时自动降低 batch_size。

    用法:
        guard = MemoryGuard(initial_batch=200, min_batch=16)
        for batch in batches:
            batch_size = guard.current_batch_size
            process(batch)
            guard.check_and_adjust()
    """

    def __init__(
        self,
        initial_batch: int = 200,
        min_batch: int = 16,
        high_threshold_percent: float = 85.0,
        low_threshold_percent: float = 60.0,
    ):
        self.current_batch_size = initial_batch
        self.min_batch = min_batch
        self.high_threshold = high_threshold_percent
        self.low_threshold = low_threshold_percent
        self.initial_batch = initial_batch
        self._adjustments = 0

    def check_and_adjust(self) -> int:
        """
        检查内存使用并调整 batch_size。
        返回调整后的 batch_size。
        """
        try:
            import psutil
            mem = psutil.virtual_memory()
            usage_percent = mem.percent

            if usage_percent >= self.high_threshold:
                # 内存压力大，减小 batch
                new_size = max(self.min_batch, self.current_batch_size // 2)
                if new_size != self.current_batch_size:
                    logger.warning(
                        f"Memory pressure high ({usage_percent:.1f}%), "
                        f"reducing batch size: {self.current_batch_size} → {new_size}"
                    )
                    self.current_batch_size = new_size
                    self._adjustments += 1

            elif usage_percent <= self.low_threshold and self.current_batch_size < self.initial_batch:
                # 内存充裕，可以恢复 batch
                new_size = min(self.initial_batch, self.current_batch_size * 2)
                if new_size != self.current_batch_size:
                    logger.info(
                        f"Memory pressure low ({usage_percent:.1f}%), "
                        f"increasing batch size: {self.current_batch_size} → {new_size}"
                    )
                    self.current_batch_size = new_size

        except ImportError:
            pass

        return self.current_batch_size

    @property
    def adjustments(self) -> int:
        """累计调整次数"""
        return self._adjustments
