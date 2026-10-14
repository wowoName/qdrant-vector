# Vector Data Manager

生产级向量数据库管理工具，基于 FastAPI + Qdrant + HuggingFace Embeddings 构建。

## 功能特性

- 📚 **Collection 管理**：多知识库独立管理
- 📁 **目录树**：多级目录组织文档
- 📄 **多格式解析**：PDF / DOCX / TXT / Markdown / Excel
- ✂️ **智能分块**：递归字符分块、固定大小分块
- 🧠 **本地 Embedding**：HuggingFace sentence-transformers
- ⚡ **增量更新**：Chunk Hash 比对，只更新变化内容
- 🔄 **版本管理**：文档版本历史，支持回滚
- 📤 **分片上传**：支持大文件分片 + 断点续传 + 秒传
- 🚀 **任务系统**：异步任务队列，进度追踪
- 🔍 **语义检索**：向量相似度搜索
- 💬 **对话验证**：RAG 对话测试（开发中）

## 快速开始

### 环境要求

- Python 3.11+
- Qdrant 1.10+
- uv (推荐) 或 pip

### 安装依赖

```bash
cd backend
uv sync
```

### 配置

```bash
cp .env.example .env
# 编辑 .env 文件，配置 Qdrant 连接等
```

### 启动

```bash
uv run python -m app.main
```

访问 http://localhost:8000/docs 查看 API 文档。

## 项目结构

```
backend/
├── app/
│   ├── api/              # API 路由层
│   ├── services/         # 业务逻辑层
│   ├── repositories/     # 数据访问层
│   ├── models/           # 数据模型
│   ├── providers/        # 外部服务（Embedding/Storage/LLM）
│   ├── parsers/          # 文件解析器
│   ├── chunkers/         # 文本分块器
│   ├── tasks/            # 异步任务
│   ├── core/             # 核心配置与工具
│   └── utils/            # 通用工具
├── data/                 # 运行时数据
└── pyproject.toml
```

## API 概览

| 模块 | 路径前缀 | 说明 |
|------|----------|------|
| Collections | `/api/v1/collections` | 知识库管理 |
| Directories | `/api/v1/collections/{id}/directories` | 目录树管理 |
| Documents | `/api/v1/collections/{id}/documents` | 文档管理 |
| Upload | `/api/v1/documents/upload` | 分片上传 |
| Search | `/api/v1/collections/{id}/search` | 语义检索 |
| Tasks | `/api/v1/tasks` | 任务管理 |
| System | `/api/v1/system` | 系统接口 |

Windows 启动：在 backend 目录运行 `uv run python -m app.main`，或从项目根目录运行 `powershell -ExecutionPolicy Bypass -File .\backend\start.ps1`。不要添加 `--reload` 或多个 workers；入口已选择本机数据库写入所需的事件循环。
