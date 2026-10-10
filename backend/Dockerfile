# ============================================================
# 后端镜像（FastAPI + uvicorn）
# 基础镜像是 Linux，与宿主 Windows 无关：
# 依赖在镜像内部安装，不存在 .venv 跨平台不兼容的问题。
# ============================================================
FROM python:3.11-slim

# 国内构建慢时可指定镜像源：
#   docker compose -f docker-compose.prod.yml build \
#     --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
#     --build-arg UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG UV_DEFAULT_INDEX=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# 系统依赖：
#   libgl1 / libglib2.0-0 → opencv（图片/视频抽帧）
#   libgomp1              → onnxruntime / torch 的 OpenMP 运行时
#   ffmpeg                → moviepy 视频处理
#   curl                  → 容器健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN pip install --no-cache-dir uv

WORKDIR /app

# 先装依赖（利用层缓存：只要 pyproject/uv.lock 不变就不会重装 torch 等大包）
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 再拷贝应用代码
COPY app ./app

# 运行期数据目录（storage/models/logs/exports），运行时由 compose 挂载持久化
RUN mkdir -p /app/data/storage /app/data/models /app/data/logs /app/data/exports

EXPOSE 8000

# 必须单进程：应用启动时会拉起「进程内」异步任务队列，多 worker 会导致任务重复消费
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
