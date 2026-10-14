# Vector Data Manager

生产级向量数据库管理工具，支持多格式文档解析、智能分块、本地 Embedding、语义检索与 RAG 对话。

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLAlchemy / Qdrant / HuggingFace Embeddings
- **前端**：Vue 3.5 / TypeScript / Vite / Element Plus / UnoCSS / Pinia
- **存储**：PostgreSQL（元数据与结构化记录）/ Qdrant 向量库 / Redis 缓存

## 项目结构

```
Qdrant-vector/
├── backend/                 # 后端 - FastAPI 服务
│   ├── app/
│   │   ├── api/             # API 路由层
│   │   ├── core/            # 配置、数据库、日志、中间件
│   │   ├── models/          # 数据模型与 Schema
│   │   ├── parsers/         # 文件解析器（PDF/DOCX/Excel/PPT/HTML...）
│   │   ├── processors/      # 文档处理器
│   │   ├── providers/       # 外部服务（Embedding/Storage/LLM）
│   │   ├── repositories/    # 数据访问层
│   │   ├── services/        # 业务逻辑层
│   │   ├── tasks/           # 异步任务
│   │   ├── utils/           # 通用工具
│   │   └── main.py          # 应用入口
│   ├── data/                # 运行时数据（DB / 存储 / 模型 / 日志）
│   ├── .env.example         # 环境变量模板
│   ├── pyproject.toml       # Python 依赖（uv 管理）
│   └── uv.lock              # 依赖锁
├── frontend/                # 前端 - Vue 3 应用
│   ├── src/
│   │   ├── components/      # 公共组件
│   │   ├── pages/           # 页面（collections / documents / chat / search / tasks）
│   │   ├── router/          # 路由
│   │   ├── stores/          # Pinia 状态管理
│   │   ├── styles/          # 全局样式
│   │   ├── utils/           # 工具（请求、上传、用户）
│   │   ├── App.vue
│   │   └── main.ts
│   ├── index.html
│   ├── package.json         # 前端依赖
│   ├── vite.config.ts       # Vite 配置（含 /api 代理到 8000）
│   ├── uno.config.ts        # UnoCSS 配置
│   └── tsconfig.json
├── requirements.txt         # Python 依赖清单（pip 兼容）
└── .gitignore
```

## 环境要求

### 后端依赖

| 依赖 | 说明 |
|------|------|
| Python 3.11+ | 运行时 |
| Qdrant 1.10+ | 向量数据库（本地 Docker 部署或云服务） |
| Redis 5.0+ | 会话热存储 / 缓存（可选但推荐） |
| Tesseract OCR | 图片文字识别（可选，系统安装） |

### 前端依赖

| 依赖 | 说明 |
|------|------|
| Node.js 18+ | 运行时 |
| pnpm / npm | 包管理器 |

## 快速启动

### 1. 启动 Qdrant 向量数据库

```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_data:/qdrant/storage \
  qdrant/qdrant:latest
```

### 2. 启动后端

```bash
cd backend

# 复制环境配置
cp .env.example .env
# 按需编辑 .env：Qdrant 连接、Redis、Embedding 模型、LLM Provider 等

# 方式一：使用 uv（推荐）
uv sync
uv run python -m app.main

# 方式二：使用 pip
pip install -r ../requirements.txt
python -m app.main
```

启动后访问：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install     # 或 pnpm install

# 开发模式启动
npm run dev     # 或 pnpm dev
```

启动后访问：http://localhost:5173

> 前端开发服务器已配置代理，`/api` 请求自动转发到后端 `http://localhost:8000`。

### 4. 构建前端生产包

```bash
cd frontend
npm run build      # 产物输出至 frontend/dist/
```

## 环境变量配置

后端配置见 [backend/.env.example](backend/.env.example)，主要项：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8000` | 服务监听 |
| `QDRANT_HOST` / `QDRANT_PORT` | `127.0.0.1` / `6333` | Qdrant 连接 |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 本地 Embedding 模型 |
| `LLM_PROVIDER` | `ark` | LLM 提供方（ollama/ark/deepseek/qwen/openai） |
| `REDIS_ENABLED` | `true` | 是否启用 Redis |

## API 概览

| 模块 | 路径前缀 | 说明 |
|------|----------|------|
| Collections | `/api/v1/collections` | 知识库管理 |
| Directories | `/api/v1/collections/{id}/directories` | 目录树管理 |
| Documents | `/api/v1/collections/{id}/documents` | 文档管理 |
| Upload | `/api/v1/documents/upload` | 分片上传（断点续传 / 秒传） |
| Search | `/api/v1/collections/{id}/search` | 语义检索 |
| Tasks | `/api/v1/tasks` | 异步任务管理 |
| System | `/api/v1/system` | 系统接口 |

## 功能特性

- 📚 **Collection 管理**：多知识库独立管理
- 📁 **目录树**：多级目录组织文档
- 📄 **多格式解析**：PDF / DOCX / TXT / Markdown / Excel / PPT / HTML / 音视频
- ✂️ **智能分块**：递归字符分块、固定大小分块
- 🧠 **本地 Embedding**：HuggingFace sentence-transformers，离线运行
- ⚡ **增量更新**：Chunk Hash 比对，只更新变化内容
- 🔄 **版本管理**：文档版本历史，支持回滚
- 📤 **分片上传**：支持大文件分片 + 断点续传 + 秒传
- 🚀 **任务系统**：异步任务队列，进度追踪
- 🔍 **语义检索**：向量相似度搜索
- 💬 **RAG 对话**：多 LLM Provider 支持，上下文压缩与会话热存储

Windows 启动：在 backend 目录运行 `uv run python -m app.main`，或从项目根目录运行 `powershell -ExecutionPolicy Bypass -File .\backend\start.ps1`。不要添加 `--reload` 或多个 workers；入口已选择本机数据库写入所需的事件循环。
