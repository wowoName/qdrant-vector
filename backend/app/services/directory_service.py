"""
目录服务
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.db.directory import DirectoryDB
from app.models.schemas.directory import (
    DirectoryCreate,
    DirectoryMove,
    DirectoryTreeNode,
    DirectoryUpdate,
)
from app.services.collection_service import CollectionService
from app.utils.hash_utils import generate_id

logger = get_logger(__name__)


class DirectoryService:
    """目录业务服务"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, collection_id: str, req: DirectoryCreate) -> DirectoryDB:
        """创建目录"""
        # 校验 Collection
        coll_svc = CollectionService(self.db)
        await coll_svc.get_by_id(collection_id)

        # 校验父目录
        if req.parent_id:
            await self._get_directory(req.parent_id, collection_id)

        # 同层级名称检查
        result = await self.db.execute(
            select(DirectoryDB).where(
                DirectoryDB.collection_id == collection_id,
                DirectoryDB.parent_id == req.parent_id,
                DirectoryDB.name == req.name,
            )
        )
        if result.scalar_one_or_none():
            raise ConflictError(f"Directory name already exists: {req.name}")

        directory = DirectoryDB(
            id=generate_id(),
            collection_id=collection_id,
            parent_id=req.parent_id,
            name=req.name,
            sort_order=req.sort_order,
        )

        # 计算 path
        directory.path = await self._build_path(directory)

        self.db.add(directory)
        await self.db.commit()
        await self.db.refresh(directory)
        return directory

    async def get_tree(self, collection_id: str) -> list[DirectoryTreeNode]:
        """获取完整目录树"""
        coll_svc = CollectionService(self.db)
        await coll_svc.get_by_id(collection_id)

        result = await self.db.execute(
            select(DirectoryDB)
            .where(DirectoryDB.collection_id == collection_id)
            .order_by(DirectoryDB.sort_order, DirectoryDB.name)
        )
        dirs = list(result.scalars().all())

        # 构建树
        node_map: dict[str, DirectoryTreeNode] = {}
        roots: list[DirectoryTreeNode] = []

        for d in dirs:
            node = DirectoryTreeNode(
                id=d.id,
                name=d.name,
                path=d.path,
                parent_id=d.parent_id,
                sort_order=d.sort_order,
                children=[],
            )
            node_map[d.id] = node

        for d in dirs:
            node = node_map[d.id]
            if d.parent_id and d.parent_id in node_map:
                node_map[d.parent_id].children.append(node)
            else:
                roots.append(node)

        return roots

    async def update(
        self, directory_id: str, collection_id: str, req: DirectoryUpdate
    ) -> DirectoryDB:
        """更新目录"""
        directory = await self._get_directory(directory_id, collection_id)

        if req.name is not None and req.name != directory.name:
            # 同层级名称检查
            result = await self.db.execute(
                select(DirectoryDB).where(
                    DirectoryDB.collection_id == collection_id,
                    DirectoryDB.parent_id == directory.parent_id,
                    DirectoryDB.name == req.name,
                    DirectoryDB.id != directory_id,
                )
            )
            if result.scalar_one_or_none():
                raise ConflictError(f"Directory name already exists: {req.name}")
            directory.name = req.name

            # 更新子节点 path
            await self._update_children_path(directory)

        if req.sort_order is not None:
            directory.sort_order = req.sort_order

        await self.db.commit()
        await self.db.refresh(directory)
        return directory

    async def move(self, directory_id: str, collection_id: str, req: DirectoryMove) -> DirectoryDB:
        """移动目录"""
        directory = await self._get_directory(directory_id, collection_id)

        if req.parent_id == directory.id:
            raise ConflictError("Cannot move directory into itself")

        if req.parent_id:
            # 校验目标父目录存在
            await self._get_directory(req.parent_id, collection_id)
            # 检查是否会形成循环
            if await self._is_descendant(req.parent_id, directory_id):
                raise ConflictError("Cannot move directory into its own descendant")

            # 同层级名称检查
            result = await self.db.execute(
                select(DirectoryDB).where(
                    DirectoryDB.collection_id == collection_id,
                    DirectoryDB.parent_id == req.parent_id,
                    DirectoryDB.name == directory.name,
                    DirectoryDB.id != directory_id,
                )
            )
            if result.scalar_one_or_none():
                raise ConflictError(f"Directory name already exists in target: {directory.name}")

        directory.parent_id = req.parent_id
        directory.path = await self._build_path(directory)

        # 更新子节点 path
        await self._update_children_path(directory)

        await self.db.commit()
        await self.db.refresh(directory)
        return directory

    async def delete(self, directory_id: str, collection_id: str) -> None:
        """删除目录"""
        directory = await self._get_directory(directory_id, collection_id)

        # 检查是否有子目录
        result = await self.db.execute(
            select(DirectoryDB).where(DirectoryDB.parent_id == directory_id)
        )
        children = result.scalars().all()
        if children:
            raise ConflictError(
                "Cannot delete directory with children. Please delete children first."
            )

        # 检查是否有文档
        from app.models.db.document import DocumentDB

        doc_result = await self.db.execute(
            select(func.count()).select_from(
                select(DocumentDB).where(DocumentDB.directory_id == directory_id).subquery()
            )
        )
        doc_count = doc_result.scalar_one()
        if doc_count > 0:
            raise ConflictError(f"Cannot delete directory with {doc_count} documents.")

        await self.db.delete(directory)
        await self.db.commit()

    async def _get_directory(self, directory_id: str, collection_id: str) -> DirectoryDB:
        result = await self.db.execute(
            select(DirectoryDB).where(
                DirectoryDB.id == directory_id,
                DirectoryDB.collection_id == collection_id,
            )
        )
        d = result.scalar_one_or_none()
        if not d:
            raise NotFoundError(f"Directory not found: {directory_id}")
        return d

    async def _build_path(self, directory: DirectoryDB) -> str:
        """构建目录路径"""
        if not directory.parent_id:
            return f"/{directory.name}"
        parent_result = await self.db.execute(
            select(DirectoryDB).where(DirectoryDB.id == directory.parent_id)
        )
        parent = parent_result.scalar_one_or_none()
        if parent:
            return f"{parent.path}/{directory.name}"
        return f"/{directory.name}"

    async def _update_children_path(self, parent: DirectoryDB) -> None:
        """递归更新所有子节点的 path"""
        result = await self.db.execute(
            select(DirectoryDB).where(DirectoryDB.parent_id == parent.id)
        )
        children = result.scalars().all()
        for child in children:
            child.path = f"{parent.path}/{child.name}"
            await self._update_children_path(child)

    async def _is_descendant(self, target_id: str, ancestor_id: str) -> bool:
        """检查 target_id 是否是 ancestor_id 的后代"""
        current_id = target_id
        visited = set()
        while current_id:
            if current_id == ancestor_id:
                return True
            if current_id in visited:
                break
            visited.add(current_id)
            result = await self.db.execute(select(DirectoryDB).where(DirectoryDB.id == current_id))
            current = result.scalar_one_or_none()
            if not current:
                break
            current_id = current.parent_id
        return False
