"""
中间件
"""

import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging import get_logger

logger = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """请求 ID 中间件：为每个请求分配唯一 ID"""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.time()
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-MS"] = f"{process_time:.2f}"

        if request.url.path not in ("/health", "/healthz"):
            logger.info(
                f"{request.method} {request.url.path} "
                f"- {response.status_code} "
                f"- {process_time:.2f}ms "
                f"- rid: {request_id}"
            )

        return response
