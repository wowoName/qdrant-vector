"""
文档服务
"""

import os

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger
from app.core.storage_quota import check_storage_quota
from app.models.db.document import ChunkDB, DocumentDB, DocumentVersionDB
from app.models.schemas.document import (
    SimpleUploadResponse,
)
from app.providers.storage.factory import get_storage
from app.services.collection_service import CollectionService
from app.services.task_service import TaskService, enqueue_task
from app.utils.file_utils import (
    get_file_type,
    sanitize_filename,
)
from app.utils.hash_utils import generate_id, sha256_file

logger = get_logger(__name__)


class DocumentService:
    """文档业务服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = get_storage()

    async def upload_file(
        self,
        collection_id: str,
        filename: str,
        content: bytes,
        directory_id: str | None = None,
        user_id: str | None = None,
    ) -> SimpleUploadResponse:
        """
        单文件上传（小文件直接上传）
        流程：保存文件 → 创建Document+Version → 入队处理任务
        """
        # 先保存到临时文件，然后走统一的路径处理逻辑
        import tempfile

        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.write(content)
        tmp_file.close()

        try:
            return await self.upload_file_from_path(
                collection_id=collection_id,
                filename=filename,
                file_path=tmp_file.name,
                directory_id=directory_id,
                delete_after=True,
                user_id=user_id,
            )
        except Exception:
            # 出错时清理临时文件
            try:
                os.unlink(tmp_file.name)
            except Exception:
                pass
            raise

    async def upload_file_from_path(
        self,
        collection_id: str,
        filename: str,
        file_path: str,
        file_hash: str | None = None,
        directory_id: str | None = None,
        delete_after: bool = False,
        user_id: str | None = None,
    ) -> SimpleUploadResponse:
        """
        从本地文件路径上传（用于分片上传合并后的大文件，避免重复读取内存）
        流程：校验 → 计算hash → 秒传检查 → 保存到存储 → 创建Document+Version → 入队处理任务
        """
        # 校验 Collection
        coll_svc = CollectionService(self.db)
        collection = await coll_svc.get_by_id(collection_id)

        # 校验文件大小
        file_size = os.path.getsize(file_path)
        if file_size > settings.upload_max_file_size:
            if delete_after:
                try:
                    os.unlink(file_path)
                except Exception:
                    pass
            raise FileTooLargeError(settings.upload_max_file_size)

        # 校验文件类型
        filename = sanitize_filename(filename)
        file_type = get_file_type(filename)
        if not file_type:
            if delete_after:
                try:
                    os.unlink(file_path)
                except Exception:
                    pass
            raise UnsupportedFileTypeError(filename)

        # 检查同目录下是否已有同名文件（有则创建新版本）
        result = await self.db.execute(
            select(DocumentDB).where(
                DocumentDB.collection_id == collection_id,
                DocumentDB.directory_id == directory_id,
                DocumentDB.name == filename,
            )
        )
        existing_doc = result.scalar_one_or_none()

        # 计算文件 hash（如果没有传入）
        if not file_hash:
            file_hash = sha256_file(file_path)

        def _cleanup_source():
            """清理源文件（如果需要）"""
            if delete_after:
                try:
                    os.unlink(file_path)
                except Exception:
                    pass

        if existing_doc:
            # 检查 hash 是否相同（秒传）
            old_ver_result = await self.db.execute(
                select(DocumentVersionDB).where(
                    DocumentVersionDB.id == existing_doc.current_version_id
                )
            )
            old_ver = old_ver_result.scalar_one_or_none()
            if old_ver and old_ver.file_hash == file_hash and old_ver.status == "completed" and existing_doc.status == "completed":
                # 文件未变化，直接返回
                _cleanup_source()
                return SimpleUploadResponse(
                    document_id=existing_doc.id,
                    version_id=old_ver.id,
                    status="completed",
                    task_id=None,
                )

            # 租户磁盘配额校验：替换文件仅计入增量占用
            await check_storage_quota(
                self.db,
                collection.owner_user_id,
                max(0, file_size - (existing_doc.file_size or 0)),
            )

            # 创建新版本
            new_version_num = await self._get_next_version_num(existing_doc.id)
            storage_path = self._make_storage_path(
                collection_id, existing_doc.id, f"v{new_version_num}_{filename}"
            )
            # 从文件路径上传（内部可能支持流式）
            await self._upload_file_from_path(storage_path, file_path)
            _cleanup_source()

            version = DocumentVersionDB(
                id=generate_id(),
                document_id=existing_doc.id,
                version_num=new_version_num,
                file_hash=file_hash,
                storage_path=storage_path,
                status="processing",
            )
            self.db.add(version)
            existing_doc.file_size = file_size
            existing_doc.status = "processing"
            await self.db.flush()

            doc_id = existing_doc.id
        else:
            # 租户磁盘配额校验：新增文档计入完整文件大小
            await check_storage_quota(self.db, collection.owner_user_id, file_size)

            # 创建新文档
            doc_id = generate_id()
            storage_path = self._make_storage_path(collection_id, doc_id, f"v1_{filename}")
            await self._upload_file_from_path(storage_path, file_path)
            _cleanup_source()

            doc = DocumentDB(
                id=doc_id,
                collection_id=collection_id,
                directory_id=directory_id,
                name=filename,
                file_type=file_type,
                file_size=file_size,
                status="processing",
            )
            self.db.add(doc)

            version = DocumentVersionDB(
                id=generate_id(),
                document_id=doc_id,
                version_num=1,
                file_hash=file_hash,
                storage_path=storage_path,
                status="processing",
            )
            self.db.add(version)
            await self.db.flush()

        # 创建处理任务
        task_svc = TaskService(self.db)
        task = await task_svc.create_task(
            task_type="document_process",
            payload={
                "version_id": version.id,
                "document_id": doc_id,
                "file_name": filename,
                "collection_id": collection_id,
                "file_type": file_type,
            },
            user_id=user_id,
        )

        # 入队执行
        await enqueue_task(task.id)

        await self.db.commit()

        return SimpleUploadResponse(
            document_id=doc_id,
            version_id=version.id,
            status="processing",
            task_id=task.id,
        )

    async def _upload_file_from_path(self, storage_path: str, local_path: str) -> None:
        """
        从本地文件路径上传到存储
        优先使用存储后端的本地路径优化（如果支持），否则读取文件内容上传
        """
        # 检查存储是否支持本地路径直接操作
        local_storage_path = self.storage.get_local_path(storage_path)
        if local_storage_path:
            # 本地存储：直接使用文件复制，避免读入内存
            import shutil

            os.makedirs(os.path.dirname(local_storage_path), exist_ok=True)
            shutil.copy2(local_path, local_storage_path)
        else:
            # 远程存储：分块读取上传
            # 先尝试使用存储的流式上传（如果支持）
            with open(local_path, "rb") as f:
                content = f.read()
            await self.storage.upload(storage_path, content)

    async def get_document(self, document_id: str) -> DocumentDB:
        """获取文档详情"""
        result = await self.db.execute(select(DocumentDB).where(DocumentDB.id == document_id))
        doc = result.scalar_one_or_none()
        if not doc:
            raise DocumentNotFoundError(document_id)
        return doc

    async def get_current_version(self, document_id: str) -> DocumentVersionDB | None:
        """获取文档的当前版本，不存在则返回 None"""
        doc = await self.get_document(document_id)
        if not doc.current_version_id:
            return None
        result = await self.db.execute(
            select(DocumentVersionDB).where(DocumentVersionDB.id == doc.current_version_id)
        )
        return result.scalar_one_or_none()

    async def list_documents(
        self,
        collection_id: str,
        directory_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        file_type: str | None = None,
    ) -> tuple[list[DocumentDB], int]:
        """获取文档列表"""
        query = select(DocumentDB).where(DocumentDB.collection_id == collection_id)

        if directory_id is not None:
            if directory_id == "":
                query = query.where(DocumentDB.directory_id.is_(None))
            else:
                query = query.where(DocumentDB.directory_id == directory_id)

        if keyword:
            query = query.where(DocumentDB.name.contains(keyword))

        if file_type:
            query = query.where(DocumentDB.file_type == file_type)

        count_query = select(func.count()).select_from(query.subquery())
        result = await self.db.execute(count_query)
        total = result.scalar_one()

        query = query.order_by(desc(DocumentDB.updated_at))
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return list(items), total

    async def delete_document(self, document_id: str) -> None:
        """删除文档"""
        from app.repositories.qdrant_repo import get_qdrant

        qdrant = get_qdrant()

        doc = await self.get_document(document_id)

        # 删除 Qdrant 向量
        try:
            await qdrant.delete_by_document(doc.collection_id, document_id)
        except Exception as e:
            logger.warning(f"Failed to delete Qdrant vectors: {e}")

        # 删除存储文件（所有版本）
        versions_result = await self.db.execute(
            select(DocumentVersionDB).where(DocumentVersionDB.document_id == document_id)
        )
        versions = versions_result.scalars().all()
        for v in versions:
            try:
                await self.storage.delete(v.storage_path)
            except Exception as e:
                logger.warning(f"Failed to delete storage file: {e}")

        # 删除数据库记录（级联：chunks, records, versions, document）
        from app.models.db.document_record import DocumentRecordDB

        await self.db.execute(ChunkDB.__table__.delete().where(ChunkDB.document_id == document_id))
        await self.db.execute(
            DocumentRecordDB.__table__.delete().where(
                DocumentRecordDB.document_id == document_id
            )
        )
        await self.db.execute(
            DocumentVersionDB.__table__.delete().where(DocumentVersionDB.document_id == document_id)
        )
        await self.db.delete(doc)

        # 更新 Collection 计数
        from app.models.db.collection import CollectionDB

        coll_result = await self.db.execute(
            select(CollectionDB).where(CollectionDB.id == doc.collection_id)
        )
        coll = coll_result.scalar_one_or_none()
        if coll:
            coll.document_count = max(0, coll.document_count - 1)
            # 统计该文档实际的 chunk 数量并从 collection 中减去
            chunk_count_result = await self.db.execute(
                select(func.count()).select_from(
                    select(ChunkDB).where(ChunkDB.document_id == document_id).subquery()
                )
            )
            doc_chunk_count = chunk_count_result.scalar_one() or 0
            coll.chunk_count = max(0, coll.chunk_count - doc_chunk_count)

        await self.db.commit()
        logger.info(f"Document deleted: {document_id}")

    async def get_versions(self, document_id: str) -> list[DocumentVersionDB]:
        """获取文档版本列表"""
        await self.get_document(document_id)  # 校验存在
        result = await self.db.execute(
            select(DocumentVersionDB)
            .where(DocumentVersionDB.document_id == document_id)
            .order_by(DocumentVersionDB.version_num.desc())
        )
        return list(result.scalars().all())

    async def get_chunks(
        self, version_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[ChunkDB], int]:
        """获取 Chunk 列表"""
        query = select(ChunkDB).where(ChunkDB.version_id == version_id)

        count_query = select(func.count()).select_from(query.subquery())
        result = await self.db.execute(count_query)
        total = result.scalar_one()

        query = query.order_by(ChunkDB.chunk_index)
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return list(items), total

    async def reindex(self, document_id: str, user_id: str | None = None) -> str:
        """重新索引文档（user_id 用于任务归属）"""
        doc = await self.get_document(document_id)
        version_result = await self.db.execute(
            select(DocumentVersionDB).where(DocumentVersionDB.id == doc.current_version_id)
        )
        version = version_result.scalar_one_or_none()
        if not version:
            raise DocumentNotFoundError(document_id)

        # 创建新版本（重新索引使用新版本号）
        new_version_num = await self._get_next_version_num(document_id)
        new_version = DocumentVersionDB(
            id=generate_id(),
            document_id=document_id,
            version_num=new_version_num,
            file_hash=version.file_hash,
            storage_path=version.storage_path,
            status="processing",
        )
        self.db.add(new_version)
        doc.status = "processing"
        await self.db.flush()

        # 创建任务
        task_svc = TaskService(self.db)
        task = await task_svc.create_task(
            task_type="document_process",
            payload={
                "version_id": new_version.id,
                "document_id": document_id,
                "file_name": doc.name,
                "collection_id": doc.collection_id,
                "file_type": doc.file_type,
            },
            user_id=user_id,
        )
        await enqueue_task(task.id)
        await self.db.commit()

        return task.id

    async def _get_next_version_num(self, document_id: str) -> int:
        """获取下一个版本号"""
        result = await self.db.execute(
            select(func.max(DocumentVersionDB.version_num)).where(
                DocumentVersionDB.document_id == document_id
            )
        )
        max_ver = result.scalar_one_or_none()
        return (max_ver or 0) + 1

    def _make_storage_path(self, collection_id: str, document_id: str, filename: str) -> str:
        """构造存储路径"""
        return f"documents/{collection_id}/{document_id}/{filename}"
