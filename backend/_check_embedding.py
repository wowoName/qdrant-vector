"""
检查向量维度和 embedding 模型
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models.db.collection import CollectionDB
from app.providers.embedding.factory import get_embedding


async def main():
    print(f"Embedding 模型配置: {settings.embedding_model}")
    print(f"缓存目录: {settings.embedding_cache_dir}")

    # 加载 embedding 模型并测试维度
    print("\n正在加载 embedding 模型...")
    embedding = await get_embedding()
    test_vec = await embedding.embed_texts(["测试文本"])
    actual_dim = len(test_vec[0])
    print(f"✅ 模型加载成功，向量维度: {actual_dim}")

    # 查询所有 collection 的 vector_dim
    print("\n数据库中各知识库的 vector_dim:")
    async with async_session() as db:
        result = await db.execute(select(CollectionDB))
        colls = result.scalars().all()
        for c in colls:
            match = "✅" if c.vector_dim == actual_dim else "⚠️"
            print(f"  {match} {c.name}: vector_dim={c.vector_dim} (实际={actual_dim})")


if __name__ == "__main__":
    asyncio.run(main())
