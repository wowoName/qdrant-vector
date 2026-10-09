"""
Collection 服务
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import CollectionNotFoundError, ConflictError
from app.core.logging import get_logger
from app.models.db.collection import CollectionDB
from app.models.schemas.collection import CollectionCreate, CollectionStats, CollectionUpdate
from app.repositories.qdrant_repo import get_qdrant

logger = get_logger(__name__)


class CollectionService:
    """Collection 业务服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.qdrant = get_qdrant()

    async def create(self, req: CollectionCreate) -> CollectionDB:
        """创建 Collection"""
        # 检查名称是否已存在
        result = await self.db.execute(select(CollectionDB).where(CollectionDB.name == req.name))
        if result.scalar_one_or_none():
            raise ConflictError(f"Collection name already exists: {req.name}")

        collection = CollectionDB(
            name=req.name,
            description=req.description,
            embedding_model=req.embedding_model,
            vector_dim=req.vector_dim,
            distance_metric=req.distance_metric,
        )
        self.db.add(collection)
        await self.db.flush()

        # 在 Qdrant 中创建 Collection
        try:
            await self.qdrant.create_collection(
                collection_id=collection.id,
                vector_size=collection.vector_dim,
                distance=collection.distance_metric,
            )
        except Exception as e:
            logger.error(f"Failed to create Qdrant collection for {collection.id}, rolling back DB: {e}")
            await self.db.rollback()
            raise

        await self.db.commit()
        await self.db.refresh(collection)
        logger.info(f"Collection created: {collection.id} ({collection.name})")
        return collection

    async def get_by_id(self, collection_id: str) -> CollectionDB:
        """根据 ID 获取 Collection"""
        result = await self.db.execute(select(CollectionDB).where(CollectionDB.id == collection_id))
        collection = result.scalar_one_or_none()
        if not collection:
            raise CollectionNotFoundError(collection_id)
        return collection

    async def list(
        self,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
    ) -> tuple[list[CollectionDB], int]:
        """获取 Collection 列表（分页）"""
        query = select(CollectionDB)

        if keyword:
            query = query.where(CollectionDB.name.contains(keyword))

        # 总数
        count_query = select(func.count()).select_from(query.subquery())
        result = await self.db.execute(count_query)
        total = result.scalar_one()

        # 分页数据
        query = query.order_by(CollectionDB.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return list(items), total

    async def update(self, collection_id: str, req: CollectionUpdate) -> CollectionDB:
        """更新 Collection"""
        collection = await self.get_by_id(collection_id)

        if req.name is not None:
            # 检查名称冲突
            result = await self.db.execute(
                select(CollectionDB).where(
                    CollectionDB.name == req.name,
                    CollectionDB.id != collection_id,
                )
            )
            if result.scalar_one_or_none():
                raise ConflictError(f"Collection name already exists: {req.name}")
            collection.name = req.name

        if req.description is not None:
            collection.description = req.description

        if req.is_active is not None:
            collection.is_active = req.is_active

        await self.db.commit()
        await self.db.refresh(collection)
        return collection

    async def delete(self, collection_id: str) -> None:
        """删除 Collection"""
        collection = await self.get_by_id(collection_id)

        # 删除 Qdrant Collection
        try:
            await self.qdrant.delete_collection(collection_id)
        except Exception as e:
            logger.warning(f"Failed to delete Qdrant collection: {e}")

        # 删除数据库记录
        await self.db.delete(collection)
        await self.db.commit()
        logger.info(f"Collection deleted: {collection_id}")

    async def get_stats(self, collection_id: str) -> CollectionStats:
        """获取 Collection 统计信息"""
        collection = await self.get_by_id(collection_id)
        qdrant_stats = await self.qdrant.get_collection_info(collection_id)

        return CollectionStats(
            id=collection.id,
            name=collection.name,
            document_count=collection.document_count,
            chunk_count=collection.chunk_count,
            vector_count=qdrant_stats.get("points_count", 0),
            storage_size=f"{qdrant_stats.get('points_count', 0)} vectors",
        )
