"""
Vector Search API
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.schemas.search import SearchRequest, SearchResponse
from app.models.schemas.common import success
from app.services.vector_service import VectorService

router = APIRouter(tags=["Search"])


@router.post("/collections/{collection_id}/search", summary="语义检索")
async def search_vectors(
    collection_id: str,
    req: SearchRequest,
    db: AsyncSession = Depends(get_db),
):
    service = VectorService(db)
    result = await service.search(collection_id, req)
    return success(result.model_dump())
