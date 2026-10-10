"""
重建向量索引脚本
从 PostgreSQL 数据库读取所有 chunk，重新生成 embedding 并写入 Qdrant

用法:
    .venv\Scripts\python.exe rebuild_vectors.py [collection_id]
    - 如果指定 collection_id，则只重建该知识库
    - 不指定则重建所有知识库
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings
from app.core.database import async_session
from app.models.db.collection import CollectionDB
from app.models.db.document import DocumentDB, DocumentVersionDB, ChunkDB
from app.providers.embedding.factory import get_embedding
from app.repositories.qdrant_repo import get_qdrant
from sqlalchemy import select

# 批次大小（一次向量化多少个 chunk）
BATCH_SIZE = 32


async def rebuild_collection(collection_id: str, collection_name: str, vector_dim: int):
    """重建单个知识库的向量索引"""
    qdrant = get_qdrant()
    embedding = await get_embedding()

    print(f"\n{'='*60}")
    print(f"重建知识库: {collection_name} ({collection_id})")
    print(f"向量维度: {vector_dim}")
    print(f"{'='*60}")

    # 1. 删除旧集合（如果存在）
    exists = await qdrant.collection_exists(collection_id)
    if exists:
        print(f"  删除旧集合...")
        await qdrant.delete_collection(collection_id)

    # 2. 创建新集合
    print(f"  创建新集合...")
    await qdrant.create_collection(
        collection_id=collection_id,
        vector_size=vector_dim,
        distance="Cosine",
    )

    # 3. 查询所有已完成的文档
    async with async_session() as db:
        result = await db.execute(
            select(DocumentDB).where(
                DocumentDB.collection_id == collection_id,
                DocumentDB.status == "completed",
            )
        )
        documents = result.scalars().all()

    if not documents:
        print(f"  ⚠️  没有已完成的文档，跳过")
        return

    print(f"  找到 {len(documents)} 个已完成的文档")

    total_chunks = 0
    for doc in documents:
        if not doc.current_version_id:
            continue

        # 查询该版本的所有 chunk
        async with async_session() as db:
            result = await db.execute(
                select(ChunkDB)
                .where(ChunkDB.version_id == doc.current_version_id)
                .order_by(ChunkDB.chunk_index)
            )
            chunks = result.scalars().all()

        if not chunks:
            print(f"  - {doc.name}: 无 chunk 数据，跳过")
            continue

        print(f"  - {doc.name}: {len(chunks)} 个 chunk, 正在向量化...")

        # 分批向量化并写入
        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i : i + BATCH_SIZE]
            texts = [c.text for c in batch]

            # 生成向量
            vectors = await embedding.embed_texts(texts)

            # 构造 Qdrant points
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
                            "file_name": doc.name,
                            "file_type": doc.file_type,
                            "chunk_index": chunk.chunk_index,
                            "text": chunk.text,
                            "page_num": chunk.page_num if chunk.page_num else -1,
                            "token_count": chunk.token_count,
                        },
                    }
                )

            # 写入 Qdrant
            await qdrant.upsert_points(collection_id, points)
            total_chunks += len(batch)
            print(f"    已写入 {min(i + BATCH_SIZE, len(chunks))}/{len(chunks)}")

    print(f"\n✅ 完成! 共写入 {total_chunks} 个向量到 {collection_name}")


async def main():
    target_collection_id = sys.argv[1] if len(sys.argv) > 1 else None

    # 获取所有知识库
    async with async_session() as db:
        if target_collection_id:
            result = await db.execute(
                select(CollectionDB).where(CollectionDB.id == target_collection_id)
            )
            collections = result.scalars().all()
            if not collections:
                print(f"❌ 找不到知识库: {target_collection_id}")
                return
        else:
            result = await db.execute(
                select(CollectionDB).where(CollectionDB.is_active == True)
            )
            collections = result.scalars().all()

    if not collections:
        print("❌ 没有找到知识库")
        return

    print(f"共需要重建 {len(collections)} 个知识库")

    # 检查 Qdrant 连接
    qdrant = get_qdrant()
    health = await qdrant.health_check()
    if health["status"] != "healthy":
        print(f"❌ Qdrant 服务不可用: {health.get('error', 'unknown')}")
        print(f"   地址: {settings.qdrant_host}:{settings.qdrant_port}")
        return
    print(f"✅ Qdrant 服务正常 ({health['host']}:{health['port']})")

    # 逐个重建
    for coll in collections:
        # 优先用 DB 中记录的维度；没有就从 embedding 模型动态获取
        vector_dim = coll.vector_dim
        if not vector_dim:
            embedding = await get_embedding()
            vector_dim = embedding.dimension
            print(f"  ℹ️  使用 embedding 模型维度: {vector_dim} ({embedding.model_name})")

        # 校验维度有效性
        if not vector_dim or vector_dim <= 0:
            print(f"  ❌ 无效的向量维度: {vector_dim}，跳过知识库 '{coll.name}'")
            continue

        await rebuild_collection(coll.id, coll.name, vector_dim)

    print(f"\n{'='*60}")
    print("🎉 所有知识库向量索引重建完成!")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
