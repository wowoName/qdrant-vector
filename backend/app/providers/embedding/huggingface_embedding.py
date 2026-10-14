"""
HuggingFace 本地 Embedding 模型
使用 sentence-transformers
"""

import asyncio
import os
from typing import AsyncIterator, Optional

from app.core.config import settings
from app.core.logging import get_logger

from .base import BaseEmbedding

logger = get_logger(__name__)


class HuggingFaceEmbedding(BaseEmbedding):
    """基于 sentence-transformers 的本地 Embedding"""

    _instance: Optional["HuggingFaceEmbedding"] = None
    _model_name: str | None = None
    _instance_lock = asyncio.Lock()

    def __init__(self, model_name: str, device: str = "auto", cache_dir: str | None = None):
        self._model_name = model_name
        self._device = device
        self._cache_dir = cache_dir
        self._model = None
        self._dimension: int | None = None

    @classmethod
    async def get_instance(cls) -> "HuggingFaceEmbedding":
        """获取单例"""
        async with cls._instance_lock:
            # 加载期间其他调用必须等待，并在取得锁后重新检查缓存。
            if cls._instance is None or cls._model_name != settings.embedding_model:
                instance = cls(
                    model_name=settings.embedding_model,
                    device=settings.embedding_device,
                    cache_dir=settings.embedding_cache_dir,
                )
                await instance._load_model()
                cls._instance = instance
                cls._model_name = settings.embedding_model
        return cls._instance

    async def _load_model(self) -> None:
        """加载模型（在线程池中执行，避免阻塞事件循环）"""
        if self._model is not None:
            return

        logger.info(f"Loading embedding model: {self._model_name} (pid={os.getpid()})")

        # 设置缓存目录
        if self._cache_dir:
            os.makedirs(self._cache_dir, exist_ok=True)
            os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", self._cache_dir)
            os.environ.setdefault("HF_HOME", self._cache_dir)

        # 如果配置了离线模式或本地模型路径，设置环境变量强制离线
        if getattr(settings, "embedding_offline", False):
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"

        # 配置 HuggingFace 镜像源
        if getattr(settings, "hf_endpoint", None):
            os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)

        # ---------- 先检查本地是否有完整模型 ----------
        local_path = self._find_local_model()
        if local_path:
            if not self._is_model_complete(local_path):
                missing = self._get_missing_files(local_path)
                raise RuntimeError(
                    f"Embedding 模型文件不完整: {self._model_name}\n"
                    f"路径: {local_path}\n"
                    f"缺失的关键文件: {', '.join(missing)}\n"
                    f"请下载完整模型后放到该目录，或修改 EMBEDDING_MODEL 配置。"
                )
            logger.info(f"Found complete local model at: {local_path}")

        def _load():
            from sentence_transformers import SentenceTransformer

            device = self._device if self._device != "auto" else None

            if local_path:
                # 本地模式：直接加载，不碰网络
                logger.info(f"Loading embedding model from local path: {local_path} (pid={os.getpid()})")
                model = SentenceTransformer(
                    model_name_or_path=local_path,
                    device=device,
                    trust_remote_code=True,
                    local_files_only=True,
                )
            else:
                # 在线模式：从 HuggingFace Hub 下载
                logger.info(f"Loading embedding model from HuggingFace Hub: {self._model_name}")
                load_kwargs = {
                    "model_name_or_path": self._model_name,
                    "device": device,
                    "trust_remote_code": True,
                    "cache_folder": self._cache_dir,
                }
                if getattr(settings, "embedding_offline", False):
                    load_kwargs["local_files_only"] = True
                model = SentenceTransformer(**load_kwargs)

            return model

        self._model = await asyncio.to_thread(_load)

        # 获取维度
        test_emb = self._model.encode(["test"], normalize_embeddings=settings.embedding_normalize)
        self._dimension = len(test_emb[0])

        logger.info(
            f"Embedding model loaded: {self._model_name}, "
            f"dimension: {self._dimension}, device: {self._model.device}"
        )

    def _find_local_model(self) -> str | None:
        """查找本地模型路径，返回绝对路径或 None"""
        model_name = self._model_name

        # 1. 直接是本地路径
        if os.path.isdir(model_name):
            return os.path.abspath(model_name)

        # 2. 相对于多个基准目录查找
        search_bases = []
        if self._cache_dir:
            search_bases.append(self._cache_dir)
        search_bases.extend(
            [
                os.getcwd(),
                "./data/models",
            ]
        )

        for base in search_bases:
            if not base:
                continue
            # 直接同名目录
            candidate = os.path.abspath(os.path.join(base, model_name))
            if os.path.isdir(candidate) and self._looks_like_model_dir(candidate):
                return candidate

        # 3. HuggingFace 缓存格式：models--Org--Model/snapshots/<hash>/
        if self._cache_dir and "/" in model_name:
            local_name = model_name.replace("/", "--")
            cached_path = os.path.join(self._cache_dir, f"models--{local_name}")
            if os.path.isdir(cached_path):
                snapshots_dir = os.path.join(cached_path, "snapshots")
                if os.path.isdir(snapshots_dir):
                    entries = [e for e in os.listdir(snapshots_dir) if not e.startswith(".")]
                    for entry in entries:
                        snap_path = os.path.join(snapshots_dir, entry)
                        if os.path.isdir(snap_path) and self._looks_like_model_dir(snap_path):
                            return snap_path

        return None

    def _looks_like_model_dir(self, path: str) -> bool:
        """判断目录看起来像不像一个模型目录（有 config.json 即可初步判断）"""
        return os.path.isfile(os.path.join(path, "config.json"))

    # 模型必备的关键文件（至少满足一组）
    _REQUIRED_FILES = {
        "pytorch": ["pytorch_model.bin"],
        "safetensors": ["model.safetensors"],
        "tf": ["tf_model.h5"],
        "onnx": ["onnx/model.onnx"],
    }
    _REQUIRED_TOKENIZER_FILES = ["tokenizer.json", "tokenizer_config.json"]

    def _is_model_complete(self, path: str) -> bool:
        """检查模型文件是否完整"""
        if not self._looks_like_model_dir(path):
            return False

        # tokenizer 基本文件
        has_tokenizer = any(
            os.path.isfile(os.path.join(path, f)) for f in self._REQUIRED_TOKENIZER_FILES
        )
        if not has_tokenizer:
            return False

        # 权重文件：至少有一种格式的主权重文件，且大小 > 10MB（排除 LFS 指针文件）
        for weight_files in self._REQUIRED_FILES.values():
            for wf in weight_files:
                wf_path = os.path.join(path, wf)
                if os.path.isfile(wf_path):
                    size = os.path.getsize(wf_path)
                    if size > 10 * 1024 * 1024:  # > 10MB 认为是真实权重
                        return True

        return False

    def _get_missing_files(self, path: str) -> list[str]:
        """返回缺失的关键文件列表（用于错误提示）"""
        missing = []

        has_tokenizer = any(
            os.path.isfile(os.path.join(path, f)) for f in self._REQUIRED_TOKENIZER_FILES
        )
        if not has_tokenizer:
            missing.extend(self._REQUIRED_TOKENIZER_FILES)

        # 检查权重文件
        weight_found = False
        for fmt, weight_files in self._REQUIRED_FILES.items():
            for wf in weight_files:
                wf_path = os.path.join(path, wf)
                if os.path.isfile(wf_path):
                    size = os.path.getsize(wf_path)
                    if size > 10 * 1024 * 1024:
                        weight_found = True
                    else:
                        missing.append(f"{wf} (仅 {size} 字节，可能是 LFS 指针文件)")

        if not weight_found:
            missing.append("模型权重文件 (pytorch_model.bin / model.safetensors / onnx/model.onnx)")

        return missing

    async def prepare_chunks(self, chunks):
        if self._model is None:
            await self._load_model()
        from app.utils.embedding_chunks import split_chunks_by_tokens
        prepared = await asyncio.to_thread(
            split_chunks_by_tokens, chunks, self._model.tokenizer,
            self._model.max_seq_length,
        )
        if len(prepared) != len(chunks):
            logger.info("Token budget split: %s source chunks -> %s embedding chunks (limit=%s)",
                        len(chunks), len(prepared), self._model.max_seq_length)
        return prepared

    def _check_input_lengths(self, texts):
        # Every caller, including reindex/sync paths, must not silently truncate.
        lengths = self._model.tokenizer(texts, add_special_tokens=True, truncation=False)["input_ids"]
        if any(len(tokens) > self._model.max_seq_length for tokens in lengths):
            raise ValueError(
                f"Embedding 输入超过 {self._model.max_seq_length} tokens；请先按模型 tokenizer 分片，禁止静默截断"
            )

    async def validate_texts(self, texts):
        if self._model is None:
            await self._load_model()
        await asyncio.to_thread(self._check_input_lengths, texts)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化"""
        if not texts:
            return []

        if self._model is None:
            await self._load_model()

        await self.validate_texts(texts)
        batch_size = self._get_optimal_batch_size(texts)

        def _encode(texts_batch: list[str]) -> list[list[float]]:
            embeddings = self._model.encode(
                texts_batch,
                batch_size=batch_size,
                show_progress_bar=False,
                normalize_embeddings=settings.embedding_normalize,
                convert_to_numpy=True,
            )
            return embeddings.tolist()

        # 分批处理，每批之间释放 GIL 让事件循环运行
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_emb = await asyncio.to_thread(_encode, batch)
            all_embeddings.extend(batch_emb)
            # 给事件循环留机会处理其他任务
            await asyncio.sleep(0)

        return all_embeddings

    async def embed_texts_stream(
        self, texts: list[str], batch_size: int = None
    ) -> AsyncIterator[list[list[float]]]:
        """
        流式向量化：分批 yield 结果，降低内存占用。
        每批处理完就立即 yield，调用方可以边处理边等待。
        """
        if not texts:
            return

        if self._model is None:
            await self._load_model()

        await self.validate_texts(texts)
        if batch_size is None:
            batch_size = self._get_optimal_batch_size(texts)

        def _encode(texts_batch: list[str]) -> list[list[float]]:
            embeddings = self._model.encode(
                texts_batch,
                batch_size=batch_size,
                show_progress_bar=False,
                normalize_embeddings=settings.embedding_normalize,
                convert_to_numpy=True,
            )
            return embeddings.tolist()

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_emb = await asyncio.to_thread(_encode, batch)
            yield batch_emb
            # 让出事件循环
            await asyncio.sleep(0)

    def _get_optimal_batch_size(self, texts: list[str]) -> int:
        """
        根据文本长度动态计算合适的 batch_size。
        短文本可以用更大的 batch，长文本需要减小 batch 避免 OOM。
        """
        base_batch = settings.embedding_batch_size
        if not texts:
            return base_batch

        # 估算平均文本长度
        avg_len = sum(len(t) for t in texts[: min(100, len(texts))]) / min(100, len(texts))

        # 基础假设：avg_len=100 时 batch_size=base_batch 合适
        # 文本越长，batch 越小，大致按反比调整
        if avg_len <= 100:
            return min(base_batch * 2, 256)  # 上限 256
        elif avg_len <= 500:
            return base_batch
        elif avg_len <= 1000:
            return max(8, base_batch // 2)
        else:
            return max(4, base_batch // 4)

    @property
    def dimension(self) -> int:
        return self._dimension or 0

    @property
    def model_name(self) -> str:
        return self._model_name or ""
