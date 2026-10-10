"""
分片上传服务
"""

import os
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    FileTooLargeError,
    NotFoundError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger
from app.core.storage_quota import check_storage_quota
from app.models.db.document import DocumentDB, DocumentVersionDB
from app.models.db.upload import UploadSessionDB
from app.models.schemas.document import (
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadStatusResponse,
)
from app.providers.storage.factory import get_storage
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService
from app.utils.datetime_utils import now
from app.utils.file_utils import get_file_type, sanitize_filename
from app.utils.hash_utils import generate_id, sha256_file

logger = get_logger(__name__)


class UploadService:
    """分片上传服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = get_storage()

    async def init_upload(self, req: UploadInitRequest) -> UploadInitResponse:
        """初始化分片上传"""
        # 校验 Collection
        coll_svc = CollectionService(self.db)
        collection = await coll_svc.get_by_id(req.collection_id)

        # 校验文件大小
        if req.file_size > settings.upload_max_file_size:
            raise FileTooLargeError(settings.upload_max_file_size)

        # 校验文件类型
        filename = sanitize_filename(req.file_name)
        file_type = get_file_type(filename)
        if not file_type:
            raise UnsupportedFileTypeError(filename)

        # 校验分片数
        expected_chunks = (req.file_size + req.chunk_size - 1) // req.chunk_size
        if req.total_chunks != expected_chunks:
            raise BadRequestError(
                f"total_chunks mismatch: expected {expected_chunks}, got {req.total_chunks}"
            )

        # 租户磁盘配额校验（大文件提前拦截，避免上传后才失败）
        # 同目录同名文件为"替换"场景，仅计入增量占用
        existing_doc = await self._find_existing_doc(
            req.collection_id, req.directory_id, filename
        )
        add_bytes = (
            max(0, req.file_size - (existing_doc.file_size or 0))
            if existing_doc
            else req.file_size
        )
        await check_storage_quota(self.db, collection.owner_user_id, add_bytes)

        # 创建上传会话
        upload_id = generate_id()
        temp_dir = self._make_temp_dir(upload_id)
        os.makedirs(temp_dir, exist_ok=True)

        expires_at = now() + timedelta(hours=settings.upload_expire_hours)

        session = UploadSessionDB(
            id=upload_id,
            file_name=filename,
            file_size=req.file_size,
            total_chunks=req.total_chunks,
            chunk_size=req.chunk_size,
            file_hash=req.file_hash,
            directory_id=req.directory_id,
            collection_id=req.collection_id,
            uploaded_chunks=[],
            status="uploading",
            temp_dir=temp_dir,
            expires_at=expires_at,
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)

        logger.info(f"Upload session created: {upload_id}, chunks: {req.total_chunks}")

        return UploadInitResponse(
            upload_id=upload_id,
            chunk_size=req.chunk_size,
            expires_at=expires_at,
            status="uploading",
        )

    async def upload_chunk(self, upload_id: str, chunk_index: int, content: bytes) -> dict:
        """上传单个分片"""
        session = await self._get_session(upload_id)

        if session.status != "uploading":
            raise BadRequestError(f"Upload session status is {session.status}")

        if chunk_index < 0 or chunk_index >= session.total_chunks:
            raise BadRequestError(f"Invalid chunk index: {chunk_index}")

        # 校验分片大小
        chunk_size = session.chunk_size
        actual_size = len(content)
        is_last_chunk = chunk_index == session.total_chunks - 1
        if is_last_chunk:
            # 最后一片：必须 > 0 且 <= chunk_size（允许为 0 字节的情况只有 total_chunks=1 时）
            if actual_size <= 0 and session.total_chunks > 1:
                raise BadRequestError(f"Last chunk cannot be empty (size: {actual_size})")
            if actual_size > chunk_size:
                raise BadRequestError(
                    f"Last chunk size {actual_size} exceeds chunk_size {chunk_size}"
                )
        else:
            # 非最后一片：必须严格等于 chunk_size
            if actual_size != chunk_size:
                raise BadRequestError(
                    f"Chunk {chunk_index} size {actual_size} != expected {chunk_size}"
                )

        # 保存分片到临时目录
        chunk_path = os.path.join(session.temp_dir, f"chunk_{chunk_index:06d}.tmp")
        with open(chunk_path, "wb") as f:
            f.write(content)

        # 更新已上传分片列表
        uploaded = list(session.uploaded_chunks or [])
        if chunk_index not in uploaded:
            uploaded.append(chunk_index)
            uploaded.sort()
            session.uploaded_chunks = uploaded

        await self.db.commit()

        return {"received": True, "chunk_index": chunk_index, "uploaded_count": len(uploaded)}

    async def get_collection_id(self, upload_id: str) -> str:
        """获取上传会话对应的 collection_id（供 API 层做归属校验）"""
        session = await self._get_session(upload_id)
        return session.collection_id

    async def get_status(self, upload_id: str) -> UploadStatusResponse:
        """获取上传状态"""
        session = await self._get_session(upload_id)
        uploaded = list(session.uploaded_chunks or [])
        return UploadStatusResponse(
            upload_id=upload_id,
            file_name=session.file_name,
            total_chunks=session.total_chunks,
            uploaded_count=len(uploaded),
            uploaded_chunks=uploaded,
            status=session.status,
            expires_at=session.expires_at,
        )

    async def cancel_upload(self, upload_id: str) -> dict:
        """取消上传：清理临时文件，标记状态为 cancelled"""
        session = await self._get_session(upload_id)

        if session.status not in ("uploading", "failed"):
            raise BadRequestError(f"Cannot cancel upload with status: {session.status}")

        # 清理临时文件
        self._cleanup_temp(session.temp_dir)

        session.status = "cancelled"
        await self.db.commit()

        logger.info(f"Upload cancelled: {upload_id}")
        return {"cancelled": True, "upload_id": upload_id}

    async def complete_upload(
        self, upload_id: str, req: UploadCompleteRequest, user_id: str | None = None
    ) -> UploadCompleteResponse:
        """完成上传：合并分片 → 保存 → 触发处理（user_id 用于任务归属）"""
        session = await self._get_session(upload_id)

        if session.status != "uploading":
            raise BadRequestError(f"Upload session status is {session.status}")

        uploaded = list(session.uploaded_chunks or [])
        if len(uploaded) != session.total_chunks:
            missing = [i for i in range(session.total_chunks) if i not in uploaded]
            raise BadRequestError(f"Missing {len(missing)} chunks: {missing[:5]}...")

        # 合并分片
        merged_path = os.path.join(session.temp_dir, "merged.tmp")
        with open(merged_path, "wb") as out_f:
            for i in range(session.total_chunks):
                chunk_path = os.path.join(session.temp_dir, f"chunk_{i:06d}.tmp")
                with open(chunk_path, "rb") as in_f:
                    out_f.write(in_f.read())

        # 计算总 hash
        file_hash = sha256_file(merged_path)
        if req.file_hash and req.file_hash != file_hash:
            raise BadRequestError("File hash mismatch")

        # 秒传检查：同 Collection 内是否已有相同 hash 的文件
        existing_doc, existing_version = await self._find_by_hash(
            session.collection_id, file_hash, session.file_name, session.directory_id
        )
        if existing_doc:
            session.status = "completed"
            await self.db.commit()
            # 清理临时文件
            self._cleanup_temp(session.temp_dir)
            return UploadCompleteResponse(
                document_id=existing_doc.id,
                version_id=existing_version.id,
                status="completed",
                task_id=None,
                instant=True,
            )

        # 保存到正式存储
        doc_svc = DocumentService(self.db)

        # 直接使用合并后的文件路径上传，避免再次读取整个文件到内存
        result = await doc_svc.upload_file_from_path(
            collection_id=session.collection_id,
            filename=session.file_name,
            file_path=merged_path,
            file_hash=file_hash,  # 已经计算过 hash，直接传入避免重复计算
            directory_id=session.directory_id,
            user_id=user_id,
        )

        # 清理临时文件
        self._cleanup_temp(session.temp_dir)
        session.status = "completed"
        await self.db.commit()

        return UploadCompleteResponse(
            document_id=result.document_id,
            version_id=result.version_id,
            status=result.status,
            task_id=result.task_id,
            instant=False,
        )

    async def _get_session(self, upload_id: str) -> UploadSessionDB:
        result = await self.db.execute(
            select(UploadSessionDB).where(UploadSessionDB.id == upload_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            raise NotFoundError(f"Upload session not found: {upload_id}")
        return session

    async def _find_by_hash(
        self,
        collection_id: str,
        file_hash: str,
        filename: str,
        directory_id: str | None,
    ) -> tuple[DocumentDB | None, DocumentVersionDB | None]:
        """按 hash 查找已有文档（秒传）"""
        # 找同 Collection 下相同 hash 的版本
        result = await self.db.execute(
            select(DocumentVersionDB)
            .where(
                DocumentVersionDB.file_hash == file_hash,
                DocumentVersionDB.status == "completed",
            )
            .limit(1)
        )
        version = result.scalar_one_or_none()
        if not version:
            return None, None

        # 找所属文档
        doc_result = await self.db.execute(
            select(DocumentDB).where(
                DocumentDB.id == version.document_id,
                DocumentDB.collection_id == collection_id,
            )
        )
        doc = doc_result.scalar_one_or_none()
        if not doc:
            return None, None

        return doc, version

    async def _find_existing_doc(
        self, collection_id: str, directory_id: str | None, filename: str
    ) -> DocumentDB | None:
        """查找同 Collection 同目录下同名文档（用于替换场景的配额增量计算）"""
        conditions = [
            DocumentDB.collection_id == collection_id,
            DocumentDB.name == filename,
        ]
        if directory_id:
            conditions.append(DocumentDB.directory_id == directory_id)
        else:
            conditions.append(DocumentDB.directory_id.is_(None))
        result = await self.db.execute(select(DocumentDB).where(*conditions))
        return result.scalar_one_or_none()

    def _make_temp_dir(self, upload_id: str) -> str:
        """构造临时目录路径"""
        return os.path.join(settings.storage_root, "uploads", upload_id)

    def _cleanup_temp(self, temp_dir: str) -> None:
        """清理临时目录"""
        import shutil

        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
        except Exception as e:
            logger.warning(f"Failed to cleanup temp dir {temp_dir}: {e}")
