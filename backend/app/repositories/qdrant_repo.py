"""
Qdrant 向量数据库仓储层
"""

from typing import List, Optional, Dict, Any
import asyncio

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import settings
from app.core.exceptions import QdrantError, QdrantConnectionError, QdrantCollectionNotFoundError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _is_connection_error(e: Exception) -> bool:
    """判断是否为连接类错误（服务不可达/502/503/超时等）"""
    msg = str(e).lower()
    conn_keywords = [
        "connection refused", "connection error", "connection reset",
        "502", "503", "504", "bad gateway", "service unavailable",
        "timeout", "timed out", "name or service not known",
        "no route to host", "network is unreachable",
    ]
    return any(kw in msg for kw in conn_keywords)


def _is_collection_not_found(e: Exception) -> bool:
    """判断是否为 Collection 不存在错误（404 + doesn't exist）"""
    msg = str(e).lower()
    return "404" in msg and ("doesn't exist" in msg or "not found" in msg or "does not exist" in msg)


def _wrap_qdrant_error(operation: str, e: Exception) -> Exception:
    """包装 Qdrant 错误，区分连接错误、集合不存在和业务错误"""
    if _is_connection_error(e):
        return QdrantConnectionError(
            f"Qdrant 服务不可达 ({settings.qdrant_host}:{settings.qdrant_port})，"
            f"请确认 Qdrant 已启动。操作: {operation}",
            detail={"host": settings.qdrant_host, "port": settings.qdrant_port, "operation": operation},
        )
    if _is_collection_not_found(e):
        return QdrantCollectionNotFoundError(
            f"Qdrant 集合不存在，操作: {operation}",
            detail={"operation": operation, "raw_error": str(e)[:200]},
        )
    return QdrantError(f"{operation} failed: {e}")


class QdrantRepository:
    """Qdrant 操作封装"""

    _instance: Optional["QdrantRepository"] = None
    _no_proxy_configured = False

    def __init__(self):
        self._client: Optional[QdrantClient] = None

    @classmethod
    def get_instance(cls) -> "QdrantRepository":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def _ensure_no_proxy(cls) -> None:
        """确保 Qdrant 地址不经过系统代理（一次性设置）"""
        if cls._no_proxy_configured:
            return
        import os

        no_proxy = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
        qdrant_addr = f"{settings.qdrant_host}:{settings.qdrant_port}"
        hosts_to_add = [settings.qdrant_host, qdrant_addr]
        if "127.0.0.1" not in no_proxy:
            hosts_to_add.append("127.0.0.1")
        if "localhost" not in no_proxy:
            hosts_to_add.append("localhost")

        existing = [h.strip() for h in no_proxy.split(",") if h.strip()]
        for h in hosts_to_add:
            if h not in existing:
                existing.append(h)
        os.environ["NO_PROXY"] = ",".join(existing)
        os.environ["no_proxy"] = ",".join(existing)
        cls._no_proxy_configured = True

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> QdrantClient:
        """创建 Qdrant 客户端"""
        # 确保 Qdrant 地址不走系统代理（避免 Fastlink/Clash 等代理工具干扰 localhost 请求）
        self._ensure_no_proxy()

        try:
            kwargs = {
                "host": settings.qdrant_host,
                "port": settings.qdrant_port,
            }
            if settings.qdrant_api_key:
                kwargs["api_key"] = settings.qdrant_api_key
            if settings.qdrant_use_grpc:
                kwargs["grpc_port"] = settings.qdrant_grpc_port
                kwargs["prefer_grpc"] = True

            client = QdrantClient(**kwargs)
            logger.info(f"Qdrant client connected to {settings.qdrant_host}:{settings.qdrant_port}")
            return client
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant: {e}")
            raise _wrap_qdrant_error("connect", e)

    def _collection_name(self, collection_id: str) -> str:
        """构造 Qdrant Collection 名称"""
        return f"{settings.qdrant_collection_prefix}{collection_id}"

    # ---------- 健康检查 ----------

    async def health_check(self) -> dict:
        """检查 Qdrant 服务是否可用"""
        try:
            def _check():
                # 调用一次轻量 API 验证连通性
                self.client.get_collections()
                return True

            await asyncio.to_thread(_check)
            return {
                "status": "healthy",
                "host": settings.qdrant_host,
                "port": settings.qdrant_port,
            }
        except Exception as e:
            logger.warning(f"Qdrant health check failed: {e}")
            return {
                "status": "unhealthy",
                "host": settings.qdrant_host,
                "port": settings.qdrant_port,
                "error": str(e)[:200],
            }

    # ---------- Collection 操作 ----------

    async def create_collection(
        self,
        collection_id: str,
        vector_size: int,
        distance: str = "Cosine",
    ) -> None:
        """创建 Collection"""
        name = self._collection_name(collection_id)
        try:
            distance_enum = models.Distance.COSINE
            if distance.lower() in ("euclid", "euclidean", "l2"):
                distance_enum = models.Distance.EUCLID
            elif distance.lower() in ("dot", "dot_product"):
                distance_enum = models.Distance.DOT

            def _create():
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=distance_enum,
                    ),
                )
                # 创建 Payload 索引
                self._create_payload_indexes(name)

            await asyncio.to_thread(_create)
            logger.info(f"Collection created: {name}")
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.warning(f"Collection already exists: {name}")
                return
            logger.error(f"Failed to create collection {name}: {e}")
            raise _wrap_qdrant_error("Create collection", e)

    def _create_payload_indexes(self, collection_name: str) -> None:
        """为常用字段创建 Payload 索引"""
        index_fields = [
            ("document_id", models.PayloadSchemaType.KEYWORD),
            ("version_id", models.PayloadSchemaType.KEYWORD),
            ("directory_id", models.PayloadSchemaType.KEYWORD),
            ("file_type", models.PayloadSchemaType.KEYWORD),
            ("chunk_index", models.PayloadSchemaType.INTEGER),
            ("page_num", models.PayloadSchemaType.INTEGER),
        ]
        for field_name, field_type in index_fields:
            try:
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_type,
                )
            except Exception as e:
                logger.warning(f"Failed to create index for {field_name}: {e}")

    async def delete_collection(self, collection_id: str) -> None:
        """删除 Collection"""
        name = self._collection_name(collection_id)
        try:
            await asyncio.to_thread(self.client.delete_collection, name)
            logger.info(f"Collection deleted: {name}")
        except Exception as e:
            logger.error(f"Failed to delete collection {name}: {e}")
            raise _wrap_qdrant_error("Delete collection", e)

    async def collection_exists(self, collection_id: str) -> bool:
        """检查 Collection 是否存在"""
        name = self._collection_name(collection_id)
        try:
            result = await asyncio.to_thread(self.client.collection_exists, name)
            return result
        except Exception:
            return False

    async def get_collection_info(self, collection_id: str) -> dict:
        """获取 Collection 统计信息"""
        name = self._collection_name(collection_id)
        try:
            info = await asyncio.to_thread(self.client.get_collection, name)
            return {
                "points_count": info.points_count,
                "vectors_count": info.vectors_count,
                "segments_count": info.segments_count,
                "status": info.status,
            }
        except Exception as e:
            logger.error(f"Failed to get collection info: {e}")
            return {"points_count": 0, "vectors_count": 0}

    async def get_collection_vector_size(self, collection_id: str) -> Optional[int]:
        """获取 Collection 的向量维度，不存在或失败返回 None"""
        name = self._collection_name(collection_id)
        try:
            def _get():
                info = self.client.get_collection(name)
                # 从 config 中读取向量维度
                params = getattr(info.config, "params", None)
                if params:
                    vectors = getattr(params, "vectors", None)
                    if vectors:
                        # 可能是 VectorParams 或 dict
                        if isinstance(vectors, dict):
                            first_key = next(iter(vectors))
                            return int(vectors[first_key].size)
                        return int(getattr(vectors, "size", 0))
                return 0

            result = await asyncio.to_thread(_get)
            return result if result > 0 else None
        except Exception as e:
            logger.debug(f"Failed to get collection vector size for {collection_id}: {e}")
            return None

    async def recreate_collection(
        self,
        collection_id: str,
        vector_size: int,
        distance: str = "Cosine",
    ) -> None:
        """删除旧集合并重新创建（用于维度变更时重建）"""
        name = self._collection_name(collection_id)
        try:
            # 先删后建
            exists = await self.collection_exists(collection_id)
            if exists:
                logger.warning(f"Recreating collection {name} (old will be deleted)")
                await asyncio.to_thread(self.client.delete_collection, name)

            await self.create_collection(collection_id, vector_size, distance)
            logger.info(f"Collection recreated: {name} (dim={vector_size})")
        except Exception as e:
            logger.error(f"Failed to recreate collection {name}: {e}")
            raise _wrap_qdrant_error("Recreate collection", e)

    # ---------- Point 操作 ----------

    async def upsert_points(
        self,
        collection_id: str,
        points: List[Dict[str, Any]],
        wait: bool = True,
        batch_size: int = None,
    ) -> None:
        """批量插入/更新 Point，支持分批写入"""
        if not points:
            return

        # 不分批或数量少，直接一次写入
        if not batch_size or len(points) <= batch_size:
            await self._upsert_batch(collection_id, points, wait)
            return

        # 分批写入
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            await self._upsert_batch(collection_id, batch, wait)
            # 让出事件循环
            await asyncio.sleep(0)

    async def _upsert_batch(
        self,
        collection_id: str,
        points: List[Dict[str, Any]],
        wait: bool = True,
    ) -> None:
        """写入一批 Point"""
        name = self._collection_name(collection_id)
        try:
            ids = [p["id"] for p in points]
            vectors = [p["vector"] for p in points]
            payloads = [p["payload"] for p in points]

            def _upsert():
                self.client.upsert(
                    collection_name=name,
                    points=models.Batch(
                        ids=ids,
                        vectors=vectors,
                        payloads=payloads,
                    ),
                    wait=wait,
                )

            await asyncio.to_thread(_upsert)
            logger.debug(f"Upserted {len(points)} points to {name}")
        except Exception as e:
            logger.error(f"Failed to upsert points: {e}")
            raise _wrap_qdrant_error("Upsert points", e)

    async def delete_by_version(self, collection_id: str, version_id: str) -> int:
        """按版本 ID 删除所有 Point"""
        name = self._collection_name(collection_id)
        try:
            def _delete():
                return self.client.delete(
                    collection_name=name,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="version_id",
                                    match=models.MatchValue(value=version_id),
                                )
                            ]
                        )
                    ),
                )

            result = await asyncio.to_thread(_delete)
            deleted = getattr(result, "deleted", 0) or 0
            logger.info(f"Deleted {deleted} points for version {version_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete points by version: {e}")
            raise _wrap_qdrant_error("Delete points", e)

    async def delete_by_document(self, collection_id: str, document_id: str) -> int:
        """按文档 ID 删除所有 Point"""
        name = self._collection_name(collection_id)
        try:
            def _delete():
                return self.client.delete(
                    collection_name=name,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="document_id",
                                    match=models.MatchValue(value=document_id),
                                )
                            ]
                        )
                    ),
                )

            result = await asyncio.to_thread(_delete)
            deleted = getattr(result, "deleted", 0) or 0
            logger.info(f"Deleted {deleted} points for document {document_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete points by document: {e}")
            raise _wrap_qdrant_error("Delete points", e)

    # ---------- 检索操作 ----------

    async def search(
        self,
        collection_id: str,
        query_vector: List[float],
        top_k: int = 10,
        score_threshold: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """语义检索"""
        name = self._collection_name(collection_id)
        try:
            # 构造过滤条件
            qdrant_filter = None
            if filters:
                must_conditions = []
                for key, value in filters.items():
                    if value is not None:
                        must_conditions.append(
                            models.FieldCondition(
                                key=key,
                                match=models.MatchValue(value=value),
                            )
                        )
                if must_conditions:
                    qdrant_filter = models.Filter(must=must_conditions)

            def _search():
                return self.client.query_points(
                    collection_name=name,
                    query=query_vector,
                    query_filter=qdrant_filter,
                    limit=top_k,
                    score_threshold=score_threshold if score_threshold > 0 else None,
                    with_payload=True,
                    with_vectors=False,
                )

            response = await asyncio.to_thread(_search)
            return [
                {
                    "id": hit.id,
                    "score": hit.score,
                    "payload": hit.payload or {},
                }
                for hit in response.points
            ]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise _wrap_qdrant_error("Search", e)

    async def scroll_all(
        self,
        collection_id: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Scroll 遍历所有符合条件的点"""
        name = self._collection_name(collection_id)
        all_points = []
        next_offset = None

        try:
            while True:
                def _scroll(offset):
                    return self.client.scroll(
                        collection_name=name,
                        scroll_filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key=k,
                                    match=models.MatchValue(value=v),
                                )
                                for k, v in (filters or {}).items()
                                if v is not None
                            ]
                        ) if filters else None,
                        limit=limit,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )

                points, next_offset = await asyncio.to_thread(_scroll, next_offset)
                all_points.extend([
                    {"id": p.id, "payload": p.payload or {}}
                    for p in points
                ])

                if next_offset is None:
                    break

            return all_points
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            raise _wrap_qdrant_error("Scroll", e)


def get_qdrant() -> QdrantRepository:
    """获取 Qdrant 仓储单例"""
    return QdrantRepository.get_instance()
