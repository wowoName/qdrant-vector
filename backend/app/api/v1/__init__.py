"""
API v1 路由
"""

from fastapi import APIRouter

from . import collection, directory, document, upload, search, chat, task, system

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(collection.router, prefix="/collections", tags=["Collections"])
api_router.include_router(directory.router, prefix="", tags=["Directories"])
api_router.include_router(document.router, prefix="", tags=["Documents"])
api_router.include_router(upload.router, prefix="/documents/upload", tags=["Upload"])
api_router.include_router(search.router, prefix="", tags=["Search"])
api_router.include_router(chat.router, prefix="", tags=["Chat"])
api_router.include_router(task.router, prefix="/tasks", tags=["Tasks"])
api_router.include_router(system.router, prefix="/system", tags=["System"])
