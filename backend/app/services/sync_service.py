"""
Qdrant 启动同步服务
在应用启动时，将数据库中已完成的文档向量同步到 Qdrant。

支持四种策略（由 settings.qdrant_sync_on_start 控制）：
  - if_missing: 仅当 Qdrant 中对应 Collection 不存在时才同步（默认，安全）
  - always:     每次启动都检查并增量同步缺失的向量
  - rebuild:    每次启动都全量重建所有向量（谨慎使用）
  - disabled:   不自动同步
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.models.db.collection import CollectionDB
from app.models.db.document import ChunkDB, DocumentDB
from app.providers.embedding.factory import get_embedding
from app.repositories.qdrant_repo import get_qdrant

logger = get_logger(__name__)

# 批量向量化大小
BATCH_SIZE = 64

# 维度一致性检查结果
DIM_CHECK_MATCHED = "matched"  # 维度一致
DIM_CHECK_RECREATED = "recreated"  # 维度不一致，已重建
DIM_CHECK_FAILED = "failed"  # 检查失败


class QdrantSyncService:
    """Qdrant 启动同步服务"""

    def __init__(self, async_session_factory: async_sessionmaker[AsyncSession]):
        self.db_factory = async_session_factory
        self.qdrant = get_qdrant()

    # ---------- 公共入口 ----------

    async def sync_on_start(self) -> None:
        """
        应用启动时的 Qdrant 同步入口。
        根据配置策略决定是否同步以及如何同步。
        """
        strategy = (settings.qdrant_sync_on_start or "disabled").lower()

        if strategy == "disabled":
            logger.info("Qdrant sync on start: disabled (skipped)")
            return

        # 健康检查
        health = await self.qdrant.health_check()
        if health["status"] != "healthy":
            logger.warning(
                f"Qdrant sync skipped: Qdrant service is unhealthy "
                f"({health.get('host')}:{health.get('port')}) - "
                f"{health.get('error', 'unknown')}"
            )
            return

        logger.info(f"Qdrant sync on start: strategy={strategy}")

        # 查询所有启用的知识库
        async with self.db_factory() as db:
            result = await db.execute(select(CollectionDB).where(CollectionDB.is_active == True))
            collections = result.scalars().all()

        if not collections:
            logger.info("Qdrant sync: no active collections found, skipped")
            return

        logger.info(f"Qdrant sync: found {len(collections)} active collection(s)")

        total_added = 0
        for coll in collections:
            try:
                added = await self._sync_collection(coll, strategy)
                total_added += added
            except Exception as e:
                # 单个知识库同步失败不影响其他知识库
                logger.error(
                    f"Qdrant sync failed for collection '{coll.name}' ({coll.id}): {e}",
                    exc_info=True,
                )

        logger.info(
            f"Qdrant sync complete: {len(collections)} collection(s) processed, "
            f"{total_added} vectors added"
        )

    # ---------- 单个 Collection 同步 ----------

    async def _sync_collection(
        self,
        coll: CollectionDB,
        strategy: str,
    ) -> int:
        """
        同步单个知识库的向量到 Qdrant。
        返回新增/写入的向量数量。
        """
        coll_id = coll.id
        coll_name = coll.name
        # 优先用 DB 中记录的维度；没有则在创建时从 embedding 模型动态获取
        vector_dim = coll.vector_dim

        collection_exists = await self.qdrant.collection_exists(coll_id)

        # 策略 1: if_missing — 只有 Collection 不存在时才同步
        if strategy == "if_missing" and collection_exists:
            # 即使集合存在，也要检查维度是否一致
            # 如果维度不匹配，重建后需要全量重写向量
            was_recreated = await self._ensure_dimension_matches(coll, collection_exists)
            if was_recreated == DIM_CHECK_RECREATED:
                logger.info(
                    f"Qdrant sync: collection '{coll_name}' was recreated due to "
                    f"dimension mismatch, will rebuild all vectors"
                )
                # 集合刚被重建（空的），走全量同步
                collection_exists = False
            else:
                logger.debug(
                    f"Qdrant sync: collection '{coll_name}' already exists, "
                    f"skip (strategy=if_missing)"
                )
                return 0

        # 策略 2: rebuild — 先删除再重建
        if strategy == "rebuild" and collection_exists:
            logger.info(f"Qdrant sync: rebuilding collection '{coll_name}'...")
            await self.qdrant.delete_collection(coll_id)
            collection_exists = False

        # 确保 Collection 存在
        if not collection_exists:
            # 如果 DB 中没有记录维度，从 embedding 模型动态获取
            if not vector_dim:
                try:
                    from app.providers.embedding.factory import get_embedding

                    embedding = await get_embedding()
                    vector_dim = embedding.dimension
                    if not vector_dim or vector_dim <= 0:
                        logger.error(
                            f"Qdrant sync: invalid embedding dimension '{vector_dim}' "
                            f"for model '{embedding.model_name}', skip collection '{coll_name}'"
                        )
                        return 0
                    logger.info(
                        f"Qdrant sync: using embedding model dimension "
                        f"{vector_dim} ({embedding.model_name})"
                    )
                except Exception as e:
                    logger.error(
                        f"Qdrant sync: failed to get embedding dimension "
                        f"for collection '{coll_name}': {e}"
                    )
                    return 0

            logger.info(f"Qdrant sync: creating collection '{coll_name}' (dim={vector_dim})")
            await self.qdrant.create_collection(
                collection_id=coll_id,
                vector_size=vector_dim,
                distance=coll.distance_metric or "cosine",
            )

        # 查询该知识库所有已完成的文档
        async with self.db_factory() as db:
            result = await db.execute(
                select(DocumentDB).where(
                    DocumentDB.collection_id == coll_id,
                    DocumentDB.status == "completed",
                    DocumentDB.current_version_id.isnot(None),
                )
            )
            documents = result.scalars().all()

        if not documents:
            logger.debug(f"Qdrant sync: no completed documents in '{coll_name}', skip")
            return 0

        # 增量同步：获取 Qdrant 中已有的 chunk_id 集合
        existing_chunk_ids: set[str] = set()
        if strategy in ("always", "if_missing") and collection_exists:
            existing_chunk_ids = await self._get_existing_chunk_ids(coll_id)
            if existing_chunk_ids:
                logger.debug(
                    f"Qdrant sync: '{coll_name}' has {len(existing_chunk_ids)} existing vectors"
                )

        # 逐个文档处理
        embedding = await get_embedding()
        total_added = 0

        for doc in documents:
            try:
                added = await self._sync_document(
                    coll_id=coll_id,
                    doc=doc,
                    embedding=embedding,
                    existing_chunk_ids=existing_chunk_ids,
                )
                total_added += added
            except Exception as e:
                logger.error(
                    f"Qdrant sync failed for document '{doc.name}' ({doc.id}): {e}",
                    exc_info=True,
                )

        if total_added > 0:
            logger.info(f"Qdrant sync: collection '{coll_name}' done, {total_added} vectors added")
        else:
            logger.info(f"Qdrant sync: collection '{coll_name}' is up-to-date (0 new vectors)")

        return total_added

    # ---------- 维度一致性检查 ----------

    async def _ensure_dimension_matches(self, coll: CollectionDB, collection_exists: bool) -> str:
        """
        检查 Qdrant 集合维度是否与当前 embedding 模型匹配。
        如果不匹配，删除旧集合并用正确维度重建（会清空旧向量）。

        Returns:
            DIM_CHECK_MATCHED   - 维度一致，无需处理
            DIM_CHECK_RECREATED - 维度不一致，已重建集合
            DIM_CHECK_FAILED    - 无法检查或重建失败
        """
        if not collection_exists:
            return DIM_CHECK_MATCHED  # 还不存在，创建时会用正确维度

        try:
            embedding = await get_embedding()
        except Exception as e:
            logger.warning(f"Failed to load embedding model for dimension check: {e}")
            return DIM_CHECK_FAILED

        expected_dim = embedding.dimension
        if not expected_dim:
            return DIM_CHECK_FAILED

        actual_dim = await self.qdrant.get_collection_vector_size(coll.id)
        if actual_dim is None:
            return DIM_CHECK_FAILED  # 获取失败

        if actual_dim == expected_dim:
            return DIM_CHECK_MATCHED  # 维度一致

        # 维度不匹配 —— 重建集合
        logger.warning(
            f"Qdrant collection '{coll.name}' dimension mismatch: "
            f"expected={expected_dim} (current model: {embedding.model_name}), "
            f"actual={actual_dim}. "
            f"Recreating collection (all old vectors will be lost)."
        )

        try:
            await self.qdrant.recreate_collection(
                collection_id=coll.id,
                vector_size=expected_dim,
                distance=coll.distance_metric or "cosine",
            )

            # 更新 DB 中的记录
            async with self.db_factory() as db:
                db_coll = await db.get(CollectionDB, coll.id)
                if db_coll:
                    db_coll.vector_dim = expected_dim
                    db_coll.embedding_model = (
                        getattr(embedding, "model_name", "") or db_coll.embedding_model
                    )
                    db_coll.chunk_count = 0  # 向量被清空了
                    await db.commit()

            logger.info(
                f"Collection '{coll.name}' recreated with dimension {expected_dim}. "
                f"Old vectors cleared."
            )
            return DIM_CHECK_RECREATED
        except Exception as e:
            logger.error(f"Failed to recreate collection '{coll.name}': {e}")
            return DIM_CHECK_FAILED

    # ---------- 单文档同步 ----------

    async def _sync_document(
        self,
        coll_id: str,
        doc: DocumentDB,
        embedding,
        existing_chunk_ids: set[str],
    ) -> int:
        """
        同步单个文档的向量。
        仅写入 Qdrant 中不存在的 chunk（增量）。
        返回新增向量数。
        """
        version_id = doc.current_version_id
        if not version_id:
            return 0

        # 查询该版本的所有 chunk
        async with self.db_factory() as db:
            result = await db.execute(
                select(ChunkDB)
                .where(ChunkDB.version_id == version_id)
                .order_by(ChunkDB.chunk_index)
            )
            chunks = result.scalars().all()

        if not chunks:
            return 0

        # 过滤出尚未在 Qdrant 中的 chunk
        new_chunks = [c for c in chunks if c.id not in existing_chunk_ids]
        if not new_chunks:
            return 0

        logger.debug(
            f"Qdrant sync: document '{doc.name}' has "
            f"{len(new_chunks)}/{len(chunks)} new chunks to sync"
        )

        # 分批向量化并写入
        total_added = 0
        for i in range(0, len(new_chunks), BATCH_SIZE):
            batch = new_chunks[i : i + BATCH_SIZE]
            texts = [c.text for c in batch]
            vectors = await embedding.embed_texts(texts)

            # 构造 points
            points = []
            for j, chunk in enumerate(batch):
                points.append(
                    {
                        "id": chunk.id,
                        "vector": vectors[j],
                        "payload": {
                            "chunk_id": chunk.id,
                            "document_id": chunk.document_id,
                            "version_id": chunk.version_id,
                            "collection_id": coll_id,
                            "directory_id": doc.directory_id or "",
                            "chunk_index": chunk.chunk_index,
                            "text": chunk.text,
                            "page_num": chunk.page_num if chunk.page_num is not None else -1,
                            "file_name": doc.name,
                            "file_type": doc.file_type,
                            "token_count": chunk.token_count or 0,
                        },
                    }
                )

            await self.qdrant.upsert_points(coll_id, points)
            total_added += len(batch)

        return total_added

    # ---------- 工具方法 ----------

    async def _get_existing_chunk_ids(self, collection_id: str) -> set[str]:
        """
        获取 Qdrant 中某个 Collection 已有的所有 chunk_id。
        用于增量同步，避免重复写入。
        """
        try:
            # 使用 scroll 遍历所有点，只取 id
            all_points = await self.qdrant.scroll_all(
                collection_id=collection_id,
                limit=500,
            )
            return {str(p["id"]) for p in all_points}
        except Exception as e:
            logger.warning(
                f"Failed to fetch existing chunk IDs from Qdrant for {collection_id}: {e}"
            )
            # 如果查询失败，返回空集合（退化为全量写入，upsert 是幂等的）
            return set()


async def run_startup_sync(async_session_factory) -> None:
    """
    便捷函数：执行启动时同步。
    在 main.py lifespan 中调用。
    """
    service = QdrantSyncService(async_session_factory)
    await service.sync_on_start()
