#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 Vector Data Manager 技术设计文档 docx
"""

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.enum.table import WD_TABLE_ALIGNMENT


def set_chinese_font(run, font_name='微软雅黑', size=None, bold=None, color=None):
    """设置中文字体"""
    run.font.name = font_name
    run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = RGBColor(*color)


def add_heading(doc, text, level=1):
    """添加带中文字体的标题"""
    heading = doc.add_heading(level=level)
    run = heading.add_run(text)
    if level == 1:
        set_chinese_font(run, '微软雅黑', size=18, bold=True, color=(0x1F, 0x4E, 0x79))
    elif level == 2:
        set_chinese_font(run, '微软雅黑', size=15, bold=True, color=(0x2E, 0x75, 0xB6))
    elif level == 3:
        set_chinese_font(run, '微软雅黑', size=13, bold=True, color=(0x2E, 0x75, 0xB6))
    else:
        set_chinese_font(run, '微软雅黑', size=12, bold=True, color=(0x33, 0x33, 0x33))
    return heading


def add_para(doc, text, size=11, bold=False, color=None, first_line_indent=True):
    """添加正文段落"""
    p = doc.add_paragraph()
    if first_line_indent:
        p.paragraph_format.first_line_indent = Cm(0.74)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.5
    run = p.add_run(text)
    set_chinese_font(run, '宋体', size=size, bold=bold, color=color)
    return p


def add_code_block(doc, code_text):
    """添加代码块"""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.5)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing = 1.2
    run = p.add_run(code_text)
    run.font.name = 'Consolas'
    run.font.size = Pt(9.5)
    run.font.color.rgb = RGBColor(0x24, 0x29, 0x2E)
    # 浅灰色背景
    shading_elm = p._p.get_or_add_pPr()
    from docx.oxml import OxmlElement
    shd = OxmlElement('w:shd')
    shd.set(qn('w:fill'), 'F5F5F5')
    shading_elm.append(shd)
    return p


def add_bullet(doc, text, size=11, level=0):
    """添加项目符号"""
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.left_indent = Cm(0.74 + level * 0.5)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.4
    run = p.add_run(text)
    set_chinese_font(run, '宋体', size=size)
    return p


def add_table(doc, headers, rows, col_widths=None):
    """添加表格"""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Light Grid Accent 1'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 表头
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = ''
        p = hdr_cells[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(h)
        set_chinese_font(run, '微软雅黑', size=10.5, bold=True, color=(0xFF, 0xFF, 0xFF))
        # 表头背景色
        from docx.oxml import OxmlElement
        shading_elm = hdr_cells[i]._tc.get_or_add_tcPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:fill'), '1F4E79')
        shading_elm.append(shd)

    # 数据行
    for r_idx, row in enumerate(rows):
        cells = table.rows[r_idx + 1].cells
        for c_idx, cell_text in enumerate(row):
            cells[c_idx].text = ''
            p = cells[c_idx].paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(str(cell_text))
            set_chinese_font(run, '宋体', size=10)

    if col_widths:
        for i, width in enumerate(col_widths):
            for cell in table.columns[i].cells:
                cell.width = Inches(width)

    return table


def main():
    doc = Document()

    # === 页面设置 ===
    section = doc.sections[0]
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)

    # === 封面 ===
    for _ in range(6):
        doc.add_paragraph()

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run('Vector Data Manager')
    set_chinese_font(run, '微软雅黑', size=28, bold=True, color=(0x1F, 0x4E, 0x79))

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run('生产级向量数据库管理工具')
    set_chinese_font(run, '微软雅黑', size=22, bold=True, color=(0x2E, 0x75, 0xB6))

    subtitle2 = doc.add_paragraph()
    subtitle2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle2.add_run('技术设计文档')
    set_chinese_font(run, '微软雅黑', size=20, bold=True, color=(0x2E, 0x75, 0xB6))

    for _ in range(8):
        doc.add_paragraph()

    version_p = doc.add_paragraph()
    version_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = version_p.add_run('版本：V1.0')
    set_chinese_font(run, '宋体', size=12)

    date_p = doc.add_paragraph()
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = date_p.add_run('日期：2026年9月')
    set_chinese_font(run, '宋体', size=12)

    doc.add_page_break()

    # ============================
    # 第1章 项目定位与总体架构
    # ============================
    add_heading(doc, '第1章 项目定位与总体架构', level=1)

    add_heading(doc, '1.1 项目定位', level=2)
    add_para(doc, 'Vector Data Manager 是一款面向生产环境的向量数据库全生命周期管理工具，以 Qdrant 作为核心向量存储引擎，提供文档上传、解析、分块、向量化、版本管理、增量更新、一致性检查和对话验证等一体化能力。工具定位如下：')
    add_bullet(doc, '企业级知识库底座：为 RAG（检索增强生成）系统提供稳定可靠的向量数据管理能力')
    add_bullet(doc, '可视化管理控制台：通过 Web 界面对 Collection、Directory、Document、Chunk 进行全链路管理')
    add_bullet(doc, '生产级可靠性保障：断点续传、秒传、增量更新、一致性校验、任务重试、回滚机制')
    add_bullet(doc, '多模型与多 Provider 兼容：支持 DeepSeek、Qwen、Ark、Ollama 等主流大模型，可通过环境变量动态切换')

    add_heading(doc, '1.2 总体架构', level=2)
    add_para(doc, '系统采用前后端分离的分层架构，后端基于 Python + uv + FastAPI 构建，前端采用 Vue3 + Element Plus + UnoCSS + Vite8 技术栈。整体架构自上而下分为五层：')

    add_bullet(doc, '表示层（Presentation）：Vue3 前端 SPA，提供文档管理、向量检索、任务监控、对话验证等交互界面')
    add_bullet(doc, '网关层（Gateway）：FastAPI 提供 RESTful API，负责路由分发、参数校验、鉴权、CORS、限流')
    add_bullet(doc, '业务层（Service）：核心业务逻辑，包括文档服务、向量服务、任务服务、目录服务、对话服务')
    add_bullet(doc, '基础设施层（Infrastructure）：Storage 抽象、Embedding 模型封装、Qdrant 客户端、文件解析器')
    add_bullet(doc, '数据层（Data）：Qdrant 向量数据库、关系型数据库（元数据）、文件存储（本地/对象存储）')

    add_heading(doc, '1.3 技术选型总览', level=2)
    add_table(doc,
        ['层级', '技术选型', '说明'],
        [
            ['后端框架', 'Python 3.12+ / FastAPI / uvicorn', '异步高性能 Web 框架'],
            ['包管理', 'uv', 'Rust 编写的极速 Python 包管理器'],
            ['向量数据库', 'Qdrant 1.10+', '高性能 Rust 向量数据库，支持稠密/稀疏向量'],
            ['关系数据库', 'SQLite（默认）/ PostgreSQL', '存储元数据，可切换'],
            ['任务队列', 'APScheduler + 异步 Worker', '轻量任务调度，支持后台异步执行'],
            ['缓存/队列', 'Redis（可选）', '任务进度、分布式锁'],
            ['Embedding', 'HuggingFace sentence-transformers', '本地推理，BGE-M3 / bge-large-zh 等'],
            ['LLM Provider', 'DeepSeek / Qwen / Ark / Ollama', '可通过环境变量动态切换'],
            ['文件解析', 'PyMuPDF / python-docx / openpyxl / markdown', '多格式文档解析'],
            ['前端框架', 'Vue 3 + TypeScript + Vite 8', '现代化前端工程'],
            ['UI 组件库', 'Element Plus', '企业级组件库'],
            ['原子 CSS', 'UnoCSS', '按需生成，体积小'],
            ['部署', 'Docker Compose', '一键部署所有服务'],
        ],
        col_widths=[1.5, 2.8, 2.5]
    )

    doc.add_page_break()

    # ============================
    # 第2章 Python + uv + FastAPI 技术栈
    # ============================
    add_heading(doc, '第2章 Python + uv + FastAPI 技术栈', level=1)

    add_heading(doc, '2.1 技术栈选型理由', level=2)
    add_para(doc, 'Python 是 AI/ML 领域的事实标准，与向量数据库、Embedding 模型、LLM SDK 生态深度融合。FastAPI 基于 Starlette 和 Pydantic，提供自动 OpenAPI 文档、类型安全、异步原生支持，是构建 AI 服务的首选框架。uv 作为新一代 Python 包管理器，安装速度比 pip 快 10-100 倍，且支持 pyproject.toml 标准配置。')

    add_heading(doc, '2.2 FastAPI 应用分层', level=2)
    add_para(doc, '后端采用经典的分层架构，确保代码可维护性和可测试性：')
    add_bullet(doc, 'api/：路由层，定义 API 端点，负责参数校验和响应格式化')
    add_bullet(doc, 'services/：业务逻辑层，封装核心业务规则')
    add_bullet(doc, 'repositories/：数据访问层，封装 Qdrant 和关系数据库操作')
    add_bullet(doc, 'models/：数据模型层，Pydantic Schema 与 ORM Model')
    add_bullet(doc, 'core/：核心配置、日志、异常、中间件')
    add_bullet(doc, 'providers/：外部服务封装（Embedding、LLM、Storage）')
    add_bullet(doc, 'tasks/：异步任务定义与调度')
    add_bullet(doc, 'utils/：通用工具函数')

    add_heading(doc, '2.3 uv 工程化配置', level=2)
    add_para(doc, '使用 pyproject.toml 统一管理项目元数据与依赖，uv 提供虚拟环境管理、依赖解析、锁定文件等功能：')
    add_code_block(doc, '''# pyproject.toml 关键配置
[project]
name = "vector-data-manager"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "qdrant-client>=1.12.0",
    "sentence-transformers>=3.0.0",
    "pymupdf>=1.24.0",
    "python-docx>=1.1.0",
    "openpyxl>=3.1.0",
    "python-multipart>=0.0.9",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "aiosqlite>=0.20.0",
    "apscheduler>=3.10.0",
    "httpx>=0.27.0",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.7.0",
]''')

    add_para(doc, '常用 uv 命令：', first_line_indent=True)
    add_bullet(doc, 'uv sync：同步依赖（安装 + 锁定）')
    add_bullet(doc, 'uv add <pkg>：添加运行时依赖')
    add_bullet(doc, 'uv add --dev <pkg>：添加开发依赖')
    add_bullet(doc, 'uv run python main.py：在虚拟环境中运行')

    doc.add_page_break()

    # ============================
    # 第3章 Qdrant 向量数据库设计
    # ============================
    add_heading(doc, '第3章 Qdrant 向量数据库设计', level=1)

    add_heading(doc, '3.1 Collection 设计原则', level=2)
    add_para(doc, 'Qdrant Collection 是向量的逻辑分组，每个 Collection 对应一个知识库或业务域。Collection 设计遵循以下原则：')
    add_bullet(doc, '按业务域划分：不同业务线使用独立 Collection，避免数据混杂')
    add_bullet(doc, '向量维度一致：同一 Collection 内所有点向量维度必须相同（由 Embedding 模型决定）')
    add_bullet(doc, '距离函数：默认使用 Cosine（余弦相似度），语义检索首选')
    add_bullet(doc, '索引类型：默认 HNSW，在精度与速度之间取得平衡')

    add_heading(doc, '3.2 Collection 配置模板', level=2)
    add_code_block(doc, '''{
  "collection_name": "knowledge_base_001",
  "vectors_config": {
    "size": 1024,           // 向量维度（BGE-M3 dense）
    "distance": "Cosine"   // 距离度量
  },
  "optimizers_config": {
    "default_segment_number": 2,
    "indexing_threshold": 20000,
    "memmap_threshold": 20000
  },
  "hnsw_config": {
    "m": 64,               // HNSW 连接数
    "ef_construct": 500,   // 构建时 ef 值
    "full_scan_threshold": 10000
  },
  "wal_config": {
    "wal_capacity_mb": 32,
    "wal_segments_ahead": 0
  }
}''')

    add_heading(doc, '3.3 Payload 字段设计', level=2)
    add_para(doc, '每个 Qdrant Point 的 Payload 携带完整的元数据，以支持过滤检索和溯源展示：')
    add_table(doc,
        ['字段名', '类型', '说明', '索引'],
        [
            ['chunk_id', 'string', 'Chunk 唯一标识（UUID）', '是'],
            ['document_id', 'string', '所属文档 ID', '是'],
            ['version_id', 'string', '文档版本 ID', '是'],
            ['collection_id', 'string', '所属 Collection', '是'],
            ['directory_id', 'string', '所属目录 ID', '是'],
            ['chunk_index', 'integer', 'Chunk 在文档中的序号', '是'],
            ['content_hash', 'string', 'Chunk 内容 SHA256（增量更新用）', '否'],
            ['text', 'string', 'Chunk 原始文本', '否'],
            ['page_num', 'integer', '页码（PDF 类文档）', '是'],
            ['file_name', 'string', '源文件名', '是'],
            ['file_type', 'string', '文件类型：pdf/docx/txt/md/xlsx', '是'],
            ['created_at', 'string', '创建时间（ISO8601）', '是'],
            ['tags', 'array<string>', '标签列表（可扩展）', '是（keyword）'],
        ],
        col_widths=[1.3, 1.2, 2.8, 1.0]
    )

    add_heading(doc, '3.4 混合检索支持', level=2)
    add_para(doc, '可选支持稀疏向量（BM25 / SPLADE）实现混合检索，通过 Qdrant 原生稀疏向量 + RRF 融合排序提升检索准确率。')
    add_bullet(doc, '稠密向量：BGE-M3 / bge-large-zh，负责语义相似度')
    add_bullet(doc, '稀疏向量：BM25 / SPLADE，负责关键词精确匹配')
    add_bullet(doc, '融合策略：RRF（Reciprocal Rank Fusion），默认权重 60')

    doc.add_page_break()

    # ============================
    # 第4章 数据模型与关系
    # ============================
    add_heading(doc, '第4章 Collection / Directory / Document / Version / Chunk / Vector 数据关系', level=1)

    add_heading(doc, '4.1 实体关系总览', level=2)
    add_para(doc, '系统包含六个核心实体，形成自上而下的层级关系：')
    add_code_block(doc, '''Collection (集合/知识库)
    └── Directory (目录树，可多层嵌套)
          └── Document (文档，文件级)
                └── Version (版本，每次上传/更新生成新版本)
                      └── Chunk (文本块，切分后的最小单元)
                            └── Vector (向量，Qdrant Point)''')

    add_para(doc, '关系说明：')
    add_bullet(doc, 'Collection 与 Directory：一对多，一个知识库下有多个目录')
    add_bullet(doc, 'Directory 自关联：目录支持树形结构，parent_id 指向父目录')
    add_bullet(doc, 'Directory 与 Document：一对多，一个目录下有多个文档')
    add_bullet(doc, 'Document 与 Version：一对多，一个文档可有多个历史版本')
    add_bullet(doc, 'Document.current_version_id：指向当前生效版本')
    add_bullet(doc, 'Version 与 Chunk：一对多，一个版本切分为多个 Chunk')
    add_bullet(doc, 'Chunk 与 Vector：一对一，每个 Chunk 对应 Qdrant 中一个 Point')

    add_heading(doc, '4.2 Collection 模型', level=2)
    add_table(doc,
        ['字段', '类型', '说明'],
        [
            ['id', 'UUID', '主键'],
            ['name', 'string(100)', '集合名称，唯一'],
            ['description', 'text', '描述'],
            ['embedding_model', 'string(100)', 'Embedding 模型名称'],
            ['vector_dim', 'integer', '向量维度'],
            ['distance_metric', 'string(20)', '距离度量：cosine/euclid/dot'],
            ['is_active', 'boolean', '是否启用'],
            ['created_at', 'datetime', '创建时间'],
            ['updated_at', 'datetime', '更新时间'],
        ],
        col_widths=[1.5, 1.5, 4.0]
    )

    add_heading(doc, '4.3 Directory 模型', level=2)
    add_table(doc,
        ['字段', '类型', '说明'],
        [
            ['id', 'UUID', '主键'],
            ['collection_id', 'UUID', '所属 Collection'],
            ['parent_id', 'UUID / NULL', '父目录 ID，根目录为 NULL'],
            ['name', 'string(100)', '目录名称'],
            ['path', 'string(500)', '目录路径（冗余，便于查询）'],
            ['sort_order', 'integer', '排序号'],
            ['created_at', 'datetime', '创建时间'],
        ],
        col_widths=[1.5, 1.5, 4.0]
    )

    add_heading(doc, '4.4 Document 模型', level=2)
    add_table(doc,
        ['字段', '类型', '说明'],
        [
            ['id', 'UUID', '主键'],
            ['collection_id', 'UUID', '所属 Collection'],
            ['directory_id', 'UUID', '所属目录'],
            ['name', 'string(255)', '文档名称（文件名）'],
            ['file_type', 'string(20)', '文件类型：pdf/docx/txt/md/xlsx'],
            ['file_size', 'bigint', '文件大小（字节）'],
            ['current_version_id', 'UUID', '当前生效版本 ID'],
            ['status', 'string(20)', '状态：pending/processing/completed/failed'],
            ['created_at', 'datetime', '创建时间'],
            ['updated_at', 'datetime', '更新时间'],
        ],
        col_widths=[1.5, 1.5, 4.0]
    )

    add_heading(doc, '4.5 DocumentVersion 模型', level=2)
    add_table(doc,
        ['字段', '类型', '说明'],
        [
            ['id', 'UUID', '主键（即 Qdrant payload 中的 version_id）'],
            ['document_id', 'UUID', '所属文档'],
            ['version_num', 'integer', '版本号（自增，从 1 开始）'],
            ['file_hash', 'string(64)', '文件 SHA256（秒传与去重用）'],
            ['chunk_count', 'integer', 'Chunk 数量'],
            ['storage_path', 'string(500)', '文件在存储系统中的路径'],
            ['status', 'string(20)', '状态：processing/completed/failed'],
            ['created_at', 'datetime', '创建时间'],
        ],
        col_widths=[1.5, 1.5, 4.0]
    )

    add_heading(doc, '4.6 Chunk 模型', level=2)
    add_table(doc,
        ['字段', '类型', '说明'],
        [
            ['id', 'UUID', '主键（即 Qdrant Point ID）'],
            ['version_id', 'UUID', '所属版本'],
            ['document_id', 'UUID', '所属文档（冗余，便于过滤）'],
            ['chunk_index', 'integer', '在文档中的序号，从 0 开始'],
            ['text', 'text', 'Chunk 文本内容'],
            ['content_hash', 'string(64)', '文本 SHA256（增量更新用）'],
            ['page_num', 'integer', '页码（PDF/PPT 等）'],
            ['char_start', 'integer', '在原文中的起始字符位置'],
            ['char_end', 'integer', '在原文中的结束字符位置'],
            ['token_count', 'integer', 'Token 数（估算）'],
            ['created_at', 'datetime', '创建时间'],
        ],
        col_widths=[1.5, 1.5, 4.0]
    )

    doc.add_page_break()

    # ============================
    # 第5章 大文件分片上传
    # ============================
    add_heading(doc, '第5章 大文件分片上传', level=1)

    add_heading(doc, '5.1 设计目标', level=2)
    add_para(doc, '支持 GB 级大文件上传，解决大文件在网络不稳定时的传输问题。分片上传将文件切割为固定大小的块，逐片上传，服务端合并。')

    add_heading(doc, '5.2 分片上传流程', level=2)
    add_para(doc, '整个分片上传分为三个阶段：初始化、分片上传、合并完成。')
    add_bullet(doc, '阶段1 - 初始化：客户端发送文件元数据（文件名、总大小、分片总数、总 SHA256），服务端返回 upload_id')
    add_bullet(doc, '阶段2 - 分片上传：客户端按序号上传分片，每个分片携带 upload_id、分片序号、分片内容、分片 SHA256')
    add_bullet(doc, '阶段3 - 合并完成：客户端通知服务端合并，服务端校验总 SHA256，合并分片，触发解析任务')

    add_heading(doc, '5.3 分片策略', level=2)
    add_table(doc,
        ['参数', '默认值', '说明'],
        [
            ['分片大小', '5 MB', '可配置，建议 5-20 MB'],
            ['最大文件大小', '10 GB', '可配置上限'],
            ['最大分片数', '10,000', '分片大小 × 分片数 = 最大文件'],
            ['并发分片数', '3', '客户端可并发上传的分片数'],
            ['分片过期时间', '24 小时', '未完成的上传 24 小时后清理'],
        ],
        col_widths=[1.8, 1.5, 3.5]
    )

    add_heading(doc, '5.4 核心 API 设计', level=2)
    add_code_block(doc, '''# 1. 初始化分片上传
POST /api/v1/documents/upload/init
Request: {
  "file_name": "large_report.pdf",
  "file_size": 1073741824,
  "total_chunks": 205,
  "chunk_size": 5242880,
  "file_hash": "sha256...",
  "directory_id": "uuid"
}
Response: {
  "upload_id": "uuid",
  "chunk_size": 5242880,
  "expires_at": "2026-09-29T12:00:00Z"
}

# 2. 上传单个分片
PUT /api/v1/documents/upload/{upload_id}/{chunk_index}
Content-Type: multipart/form-data
Body: chunk 文件二进制 + chunk_hash
Response: { "received": true, "chunk_index": 0 }

# 3. 完成合并
POST /api/v1/documents/upload/{upload_id}/complete
Request: { "file_hash": "sha256..." }
Response: {
  "document_id": "uuid",
  "version_id": "uuid",
  "status": "processing"
}

# 4. 查询上传进度
GET /api/v1/documents/upload/{upload_id}/status
Response: {
  "upload_id": "uuid",
  "total_chunks": 205,
  "uploaded_chunks": [0, 1, 2, ...],
  "status": "uploading"
}''')

    doc.add_page_break()

    # ============================
    # 第6章 断点续传
    # ============================
    add_heading(doc, '第6章 断点续传', level=1)

    add_heading(doc, '6.1 设计思路', level=2)
    add_para(doc, '断点续传是分片上传的自然延伸。当上传中断（网络抖动、浏览器刷新、程序崩溃）后，客户端可通过 upload_id 查询已上传的分片列表，仅上传缺失的分片，无需从头开始。')

    add_heading(doc, '6.2 续传流程', level=2)
    add_bullet(doc, '客户端保存 upload_id 到本地（localStorage + 文件指纹）')
    add_bullet(doc, '上传中断后，客户端使用 upload_id + file_hash 查询上传状态')
    add_bullet(doc, '服务端返回已上传分片索引列表')
    add_bullet(doc, '客户端计算缺失分片，继续上传')
    add_bullet(doc, '所有分片上传完成后，触发合并')

    add_heading(doc, '6.3 服务端存储结构', level=2)
    add_code_block(doc, '''# 分片上传记录表 upload_sessions
- id (PK, UUID)              # upload_id
- file_name (string)
- file_size (bigint)
- total_chunks (integer)
- chunk_size (integer)
- file_hash (string(64))     # 文件总 SHA256
- directory_id (UUID)
- collection_id (UUID)
- uploaded_chunks (JSON)     # 已上传分片索引数组
- status (string)            # uploading / completed / expired / failed
- temp_dir (string)          # 临时分片存储目录
- expires_at (datetime)
- created_at (datetime)
- updated_at (datetime)

# 每个分片保存在临时目录：
# {temp_dir}/{upload_id}/chunk_{index}.tmp''')

    add_heading(doc, '6.4 一致性保障', level=2)
    add_bullet(doc, '每个分片上传后服务端计算 SHA256 并与客户端传递的对比，不一致则拒绝')
    add_bullet(doc, '合并时重新计算总文件 SHA256，与初始化时的 file_hash 对比')
    add_bullet(doc, '过期的上传会话由定时任务自动清理临时文件和数据库记录')
    add_bullet(doc, '合并失败自动回滚，保留已有分片，允许重试')

    doc.add_page_break()

    # ============================
    # 第7章 秒传与 SHA256
    # ============================
    add_heading(doc, '第7章 秒传与 SHA256', level=1)

    add_heading(doc, '7.1 秒传原理', level=2)
    add_para(doc, '秒传（Instant Upload）基于文件内容哈希去重。当用户上传文件时，先计算文件的 SHA256 哈希值，在服务端查询是否已有相同哈希的文件。若存在，则直接复用已有文件的存储和解析结果，跳过上传和解析过程，实现"秒级上传"。')

    add_heading(doc, '7.2 秒传判定流程', level=2)
    add_code_block(doc, '''客户端                          服务端
  |  -- 预上传请求（file_hash）--> |
  |                                |
  |  <-- 命中？                    |
  |     - 命中: 返回已有 document  |
  |     - 未命中: 返回 upload_id  |
  |                                |
  ├─ 命中 → 直接完成（秒传）       │
  └─ 未命中 → 走分片上传流程       │''')

    add_heading(doc, '7.3 秒传的边界处理', level=2)
    add_bullet(doc, '同名同哈希：视为同一文件，直接返回已有文档（幂等）')
    add_bullet(doc, '同名不同哈希：创建新版本，保留历史版本记录')
    add_bullet(doc, '不同名同哈希：创建新 Document 记录，但复用底层存储文件（硬链接/引用计数）')
    add_bullet(doc, '不同 Collection：即使哈希相同，也必须重新索引向量（因为 Collection 独立）')

    add_heading(doc, '7.4 SHA256 计算优化', level=2)
    add_para(doc, '大文件计算 SHA256 时采用流式计算，避免一次性加载到内存：')
    add_bullet(doc, '客户端：使用 File.slice + Web Crypto API 分片计算，UI 不阻塞')
    add_bullet(doc, '服务端：分片上传时边收边算，合并时得出总哈希进行校验')
    add_bullet(doc, '哈希精度：SHA-256，碰撞概率可忽略（2^128 生日攻击下界）')

    doc.add_page_break()

    # ============================
    # 第8章 文档目录树
    # ============================
    add_heading(doc, '第8章 文档目录树', level=1)

    add_heading(doc, '8.1 目录树设计', level=2)
    add_para(doc, '每个 Collection 下支持多级目录树结构，用于组织和分类文档。目录采用邻接表模型（parent_id 自引用），配合 path 冗余字段实现高效查询。')

    add_heading(doc, '8.2 目录树核心操作', level=2)
    add_bullet(doc, '创建目录：指定 parent_id，自动计算 path 字段')
    add_bullet(doc, '移动目录：更新 parent_id，递归更新所有子节点 path')
    add_bullet(doc, '删除目录：软删除标记 + 异步清理（级联删除子目录和文档需确认）')
    add_bullet(doc, '重命名目录：更新 name，同步更新子节点 path')
    add_bullet(doc, '目录排序：sort_order 字段支持手动排序')

    add_heading(doc, '8.3 目录树查询优化', level=2)
    add_code_block(doc, '''-- 获取某目录下的直接子目录和文档（分页）
SELECT id, name, type FROM (
  SELECT id, name, 'directory' as type, sort_order
  FROM directories WHERE parent_id = :parent_id AND collection_id = :cid
  UNION ALL
  SELECT id, name, 'document' as type, 9999 as sort_order
  FROM documents WHERE directory_id = :parent_id AND collection_id = :cid
) ORDER BY type, sort_order, name
LIMIT :limit OFFSET :offset;

-- 获取完整目录树（一次性加载，适合小中型知识库）
SELECT id, parent_id, name, path, sort_order
FROM directories
WHERE collection_id = :cid
ORDER BY path, sort_order;''')

    add_heading(doc, '8.4 拖拽交互', level=2)
    add_para(doc, '前端支持拖拽文档到目录、拖拽目录调整层级，后端提供 move 接口，校验循环引用和权限。')

    doc.add_page_break()

    # ============================
    # 第9章 多格式文档解析
    # ============================
    add_heading(doc, '第9章 PDF / DOCX / TXT / Markdown / Excel 解析', level=1)

    add_heading(doc, '9.1 解析器抽象', level=2)
    add_para(doc, '使用策略模式统一文档解析接口，每种格式对应一个 Parser 实现类：')
    add_code_block(doc, '''class BaseParser(ABC):
    """文档解析器基类"""

    @abstractmethod
    async def parse(self, file_path: str, file_name: str) -> ParseResult:
        """解析文件，返回结构化文本结果"""
        pass

    @abstractmethod
    def supported_types(self) -> list[str]:
        """支持的文件扩展名"""
        pass

class ParseResult:
    text: str                    # 全文文本
    pages: list[str]             # 按页/按 sheet 分割的文本
    metadata: dict               # 元数据（标题、作者等）
    toc: list[dict]              # 目录结构（如有）
    tables: list[list]           # 提取的表格数据''')

    add_heading(doc, '9.2 PDF 解析', level=2)
    add_bullet(doc, '使用 PyMuPDF (fitz) 库，速度快、支持中文、提取质量高')
    add_bullet(doc, '提取纯文本时保留换行、表格结构和页码信息')
    add_bullet(doc, '支持提取 PDF 元数据（标题、作者、创建时间）')
    add_bullet(doc, '支持扫描件 PDF 的 OCR 扩展（可选集成 PaddleOCR）')
    add_bullet(doc, '按页返回文本列表，为后续 Chunk 携带 page_num 提供依据')

    add_heading(doc, '9.3 DOCX 解析', level=2)
    add_bullet(doc, '使用 python-docx 库解析 Word 文档')
    add_bullet(doc, '提取段落文本，保留标题层级（Heading 1-9）')
    add_bullet(doc, '提取表格内容，转换为 Markdown 表格格式')
    add_bullet(doc, '提取列表（有序/无序），保留缩进层级')
    add_bullet(doc, '生成伪目录结构（基于标题层级），辅助语义分块')

    add_heading(doc, '9.4 TXT 解析', level=2)
    add_bullet(doc, '自动检测编码（UTF-8 / GBK / GB2312 / UTF-16）')
    add_bullet(doc, '按行读取，处理换行符规范化（CRLF → LF）')
    add_bullet(doc, '按空行分段，保留段落结构')

    add_heading(doc, '9.5 Markdown 解析', level=2)
    add_bullet(doc, '使用 markdown-it-py 解析 Markdown 结构')
    add_bullet(doc, '保留标题层级（# → H1, ## → H2 等）')
    add_bullet(doc, '提取代码块、表格、列表等结构化元素')
    add_bullet(doc, '按标题自然分块，作为 Chunk 策略的优先依据')

    add_heading(doc, '9.6 Excel 解析', level=2)
    add_bullet(doc, '使用 openpyxl 读取 .xlsx 文件')
    add_bullet(doc, '按 Sheet 解析，每个 Sheet 生成独立文本块')
    add_bullet(doc, '表格转换：每行转换为一条"列名: 值"的结构化描述')
    add_bullet(doc, '支持合并单元格处理、公式值读取（取缓存值）')
    add_bullet(doc, '大表自动按行组分块，避免单个 Chunk 过大')

    doc.add_page_break()

    # ============================
    # 第10章 PDF 文本清洗
    # ============================
    add_heading(doc, '第10章 PDF 文本清洗', level=1)

    add_heading(doc, '10.1 为什么需要清洗', level=2)
    add_para(doc, 'PDF 提取的原始文本通常包含大量噪声：页眉页脚重复、页码、乱码字符、多余空白、断词断行、表格错位等。直接用于向量化会严重影响检索质量。文本清洗是提升 RAG 效果的关键前置步骤。')

    add_heading(doc, '10.2 清洗流水线', level=2)
    add_code_block(doc, '''原始 PDF 文本
      ↓
  ① 字符规范化
   - Unicode 标准化（NFKC）
   - 全角/半角转换
   - 不可见字符清理
      ↓
  ② 页眉页脚移除
   - 每页顶部 N 行 / 底部 N 行对比
   - 超过阈值重复率的行判定为页眉页脚
      ↓
  ③ 页码与水印移除
   - 正则匹配纯数字页码
   - 常见水印关键词过滤
      ↓
  ④ 断行修复
   - 中文按字符断行 → 拼接
   - 英文按单词断行 → 去连字符 + 拼接
   - 段落之间空行保留
      ↓
  ⑤ 空白清理
   - 多个空格/制表符合并为一个
   - 连续空行压缩为最多 2 个
   - 行首尾空白移除
      ↓
  ⑥ 特殊处理
   - 表格保留（Markdown 格式）
   - 公式标记（$...$）保留
   - 乱码行检测与剔除
      ↓
  清洗后文本''')

    add_heading(doc, '10.3 页眉页脚检测算法', level=2)
    add_para(doc, '页眉页脚具有"每页重复出现"的特征。检测算法：')
    add_bullet(doc, '提取每页的前 K 行和后 K 行（默认 K=3）')
    add_bullet(doc, '计算行文本的相似度（编辑距离或包含关系）')
    add_bullet(doc, '重复出现在超过 60% 页面的行，判定为页眉/页脚')
    add_bullet(doc, '对页码等动态内容使用正则白名单过滤')

    add_heading(doc, '10.4 断行修复策略', level=2)
    add_code_block(doc, '''def fix_line_breaks(lines: list[str]) -> list[str]:
    """修复 PDF 提取文本的断行问题"""
    paragraphs = []
    current = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 空行 → 段落分隔
            if current:
                paragraphs.append("".join(current))
                current = []
            continue

        # 判断是否需要与下一行连接
        if current and should_join(current[-1], stripped):
            current[-1] = current[-1] + stripped
        else:
            current.append(stripped)

    if current:
        paragraphs.append("".join(current))

    return paragraphs

def should_join(prev: str, curr: str) -> bool:
    # 中文：前一行末尾不是终止符（。！？；：…）就连接
    if is_chinese(prev[-1]) and is_chinese(curr[0]):
        return prev[-1] not in "。！？；：…"
    # 英文：前一行末尾是连字符 → 去连字符连接
    if prev.endswith("-") and curr[0].isalpha():
        return True
    # 英文：前一行末尾不是句末标点 → 空格连接
    if prev[-1].islower() and curr[0].islower():
        return True
    return False''')

    doc.add_page_break()

    # ============================
    # 第11章 Chunk 策略
    # ============================
    add_heading(doc, '第11章 Chunk 策略', level=1)

    add_heading(doc, '11.1 分块策略总览', level=2)
    add_para(doc, '文本分块（Chunking）直接影响检索质量和上下文完整性。系统提供多种分块策略，可按文档类型或 Collection 配置：')
    add_table(doc,
        ['策略名称', '适用场景', '原理', '默认参数'],
        [
            ['固定大小分块', '通用、纯文本', '按字符数切分', '500 字符，重叠 50'],
            ['语义分块', 'Markdown / 有结构文档', '按标题层级切分', 'H2 为一级边界'],
            ['递归字符分块', '通用推荐', '按分隔符优先级递归切分', '500 字符，重叠 100'],
            ['句子感知分块', '句子结构完整', '不切断句子', '400-600 字符'],
            ['自定义分块', '特殊需求', '用户自定义规则', '灵活配置'],
        ],
        col_widths=[1.3, 1.5, 2.0, 1.8]
    )

    add_heading(doc, '11.2 递归字符分块（推荐默认）', level=2)
    add_para(doc, '递归字符分块按优先级尝试不同分隔符，尽量在语义边界处切分：')
    add_bullet(doc, '分隔符优先级（中文场景）：段落（\\n\\n）→ 句子（。！？）→ 子句（，；：）→ 词组→ 字符')
    add_bullet(doc, '优先在较大的语义单位边界切分，保证 Chunk 内部语义完整')
    add_bullet(doc, '支持重叠（Overlap），避免边界信息丢失，默认重叠 10%-20%')

    add_heading(doc, '11.3 语义分块（Markdown 优化）', level=2)
    add_para(doc, '对于有标题结构的文档（Markdown、DOCX），优先按标题层级分块：')
    add_code_block(doc, '''# 一级标题（作为 Collection 级分组）
## 二级标题（作为 Chunk 分组边界）
### 三级标题（作为 Chunk 内部子结构）
正文段落...

Chunk 构造规则：
- 每个 ## 下的内容构成一个或多个 Chunk
- Chunk 前拼接标题路径：「一级标题 > 二级标题」
- 单个 ## 下内容过长 → 退化为递归字符分块
- 好处：检索时标题上下文自动包含，提升语义匹配度''')

    add_heading(doc, '11.4 Chunk 大小选择', level=2)
    add_bullet(doc, '小 Chunk（100-300 字符）：精度高，检索准确，但上下文不足')
    add_bullet(doc, '中 Chunk（400-800 字符）：平衡方案，生产推荐')
    add_bullet(doc, '大 Chunk（1000-2000 字符）：上下文完整，但噪声多、精度下降')
    add_bullet(doc, '建议策略：中 Chunk 检索 + 父文档扩展（Parent-Child Chunking）')

    add_heading(doc, '11.5 父子 Chunk（可选高级模式）', level=2)
    add_para(doc, '将文档切分为小 Chunk（Child）用于检索，返回时扩展为大 Chunk（Parent）用于 LLM 阅读，兼顾检索精度和上下文完整性。')

    doc.add_page_break()

    # ============================
    # 第12章 本地 HuggingFaceEmbeddings
    # ============================
    add_heading(doc, '第12章 本地 HuggingFaceEmbeddings', level=1)

    add_heading(doc, '12.1 模型选型', level=2)
    add_para(doc, '使用 sentence-transformers 库加载本地 Embedding 模型，支持主流中英文语义向量模型：')
    add_table(doc,
        ['模型名称', '维度', '语言', '特点', '推荐场景'],
        [
            ['bge-m3', '1024', '中英多语', '多粒度、稠密+稀疏+多向量', '通用推荐'],
            ['bge-large-zh-v1.5', '1024', '中文', '中文语义效果优秀', '中文知识库'],
            ['bge-base-zh-v1.5', '768', '中文', '轻量、速度快', '轻量部署'],
            ['text2vec-large-chinese', '1024', '中文', 'ERNIE 系列', '百度生态'],
            ['all-mpnet-base-v2', '768', '英文', '英文通用', '英文知识库'],
        ],
        col_widths=[1.8, 0.8, 0.8, 2.0, 1.2]
    )

    add_heading(doc, '12.2 本地推理架构', level=2)
    add_code_block(doc, '''class EmbeddingService:
    """Embedding 服务，单例模式，模型加载一次复用"""

    _instance = None
    _model = None
    _model_name = None

    @classmethod
    async def get_instance(cls, model_name: str) -> "EmbeddingService":
        if cls._instance is None or cls._model_name != model_name:
            cls._model = await asyncio.to_thread(
                SentenceTransformer,
                model_name,
                device="auto",  # 自动选择 GPU/CPU
                trust_remote_code=True
            )
            cls._model_name = model_name
            cls._instance = cls()
        return cls._instance

    async def embed_texts(self, texts: list[str],
                          batch_size: int = 32) -> list[list[float]]:
        """批量向量化，使用线程池避免阻塞事件循环"""
        return await asyncio.to_thread(
            self._model.encode,
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True
        ).tolist()''')

    add_heading(doc, '12.3 性能优化', level=2)
    add_bullet(doc, '批量推理：将多个 Chunk 组成批次（默认 32）一起推理，提升吞吐')
    add_bullet(doc, 'GPU 加速：自动检测 CUDA / MPS 设备，优先使用 GPU')
    add_bullet(doc, '模型缓存：模型文件下载到本地缓存目录，启动时直接加载')
    add_bullet(doc, '量化支持：可选 int8 / fp16 量化，降低显存占用')
    add_bullet(doc, '异步封装：使用 asyncio.to_thread 包装同步推理，不阻塞 FastAPI 事件循环')

    add_heading(doc, '12.4 模型文件管理', level=2)
    add_bullet(doc, '默认缓存目录：./models/（可通过环境变量配置）')
    add_bullet(doc, '支持从本地路径加载模型（离线环境）')
    add_bullet(doc, '启动时检查模型是否存在，不存在则自动下载（需网络）')
    add_bullet(doc, '模型元数据记录：名称、维度、描述、默认参数')

    doc.add_page_break()

    # ============================
    # 第13章 Qdrant Point / Payload
    # ============================
    add_heading(doc, '第13章 Qdrant Point / Payload', level=1)

    add_heading(doc, '13.1 Point 结构', level=2)
    add_para(doc, 'Qdrant 中的每个向量点（Point）由三部分组成：唯一 ID、向量数据、Payload 元数据。')
    add_code_block(doc, '''{
  "id": "uuid-chunk-id",           // Chunk ID 作为 Point ID
  "vector": [0.123, -0.456, ...],  // 稠密向量（1024维）
  "payload": {
    "chunk_id": "uuid-chunk-id",
    "document_id": "uuid-doc-id",
    "version_id": "uuid-version-id",
    "collection_id": "uuid-coll-id",
    "directory_id": "uuid-dir-id",
    "chunk_index": 0,
    "content_hash": "sha256...",
    "text": "Chunk 原文...",
    "page_num": 5,
    "file_name": "report.pdf",
    "file_type": "pdf",
    "created_at": "2026-09-28T10:00:00Z",
    "tags": ["技术文档", "AI"]
  }
}''')

    add_heading(doc, '13.2 Payload 索引策略', level=2)
    add_para(doc, '为常用过滤字段创建 Payload 索引，加速过滤检索：')
    add_table(doc,
        ['字段', '索引类型', '用途'],
        [
            ['document_id', 'Keyword', '按文档过滤 / 删除'],
            ['version_id', 'Keyword', '按版本过滤 / 删除（最常用）'],
            ['directory_id', 'Keyword', '按目录过滤检索'],
            ['file_type', 'Keyword', '按文件类型过滤'],
            ['chunk_index', 'Integer', '按序号排序'],
            ['page_num', 'Integer', '按页码过滤'],
            ['created_at', 'Datetime', '按时间范围过滤'],
            ['tags', 'Keyword (list)', '按标签过滤'],
        ],
        col_widths=[1.5, 1.5, 3.5]
    )

    add_heading(doc, '13.3 写入操作', level=2)
    add_code_block(doc, '''# 批量 Upsert Point
from qdrant_client import models

await qdrant.upsert(
    collection_name=collection_name,
    points=models.Batch(
        ids=[chunk.id for chunk in chunks],
        vectors=[chunk.vector for chunk in chunks],
        payloads=[
            {
                "chunk_id": c.id,
                "document_id": c.document_id,
                "version_id": c.version_id,
                "text": c.text,
                "page_num": c.page_num,
                # ...
            }
            for c in chunks
        ],
    ),
    wait=True,  # 等待写入完成
)

# 按版本删除旧向量
await qdrant.delete(
    collection_name=collection_name,
    points_selector=models.FilterSelector(
        filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="version_id",
                    match=models.MatchValue(value=old_version_id),
                )
            ]
        )
    ),
)''')

    add_heading(doc, '13.4 检索操作', level=2)
    add_code_block(doc, '''# 语义检索 + 过滤
results = await qdrant.search(
    collection_name=collection_name,
    query_vector=query_embedding,
    query_filter=models.Filter(
        must=[
            models.FieldCondition(
                key="directory_id",
                match=models.MatchValue(value=directory_id),
            )
        ]
    ),
    limit=10,
    score_threshold=0.5,  # 相似度阈值
    with_payload=True,
    with_vectors=False,
)

# 返回结果结构
# results[0].id          → Point ID
# results[0].score       → 相似度分数
# results[0].payload     → 元数据字典''')

    doc.add_page_break()

    # ============================
    # 第14章 Chunk Hash 增量更新
    # ============================
    add_heading(doc, '第14章 Chunk Hash 增量更新', level=1)

    add_heading(doc, '14.1 增量更新原理', level=2)
    add_para(doc, '当文档更新时，不需要重新向量化所有 Chunk。通过对每个 Chunk 的文本内容计算 SHA256 哈希（content_hash），对比新旧版本的 Chunk 哈希，只对发生变化的 Chunk 重新向量化，未变化的直接复用原有向量，大幅节省更新时间和计算资源。')

    add_heading(doc, '14.2 增量更新流程', level=2)
    add_code_block(doc, '''新版本文件解析 → Chunk 列表 V2
                      ↓
          为每个 Chunk 计算 content_hash
                      ↓
          与旧版本（V1）的 Chunk 哈希对比
                      ↓
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
     新增 Chunk    修改 Chunk    删除 Chunk
   (V2有V1无)   (hash 不同)   (V1有V2无)
        ↓             ↓             ↓
     新向量化      新向量化      删除向量
        ↓             ↓             ↓
        └───────── 写入 Qdrant ────┘
                      ↓
               版本指针切换为 V2''')

    add_heading(doc, '14.3 哈希匹配算法', level=2)
    add_para(doc, '为了应对文档修改导致的 Chunk 偏移（如在文档开头插入一段文字导致后续所有 Chunk 序号变化），不能仅按 chunk_index 对比。采用基于内容哈希的滑动匹配：')
    add_bullet(doc, '构建旧版本 content_hash → chunk_id 的映射表')
    add_bullet(doc, '遍历新版本 Chunk，按 content_hash 在映射表中查找')
    add_bullet(doc, '命中 → 复用旧向量（更新 chunk_index 等元数据）')
    add_bullet(doc, '未命中 → 判定为新增/修改，重新向量化')
    add_bullet(doc, '旧版本中未被命中的 Chunk → 判定为删除，从 Qdrant 移除')

    add_heading(doc, '14.4 性能收益评估', level=2)
    add_table(doc,
        ['场景', '更新比例', '全量时间', '增量时间', '加速比'],
        [
            ['修正错别字', '1%', '100%', '~5%', '20x'],
            ['增加章节', '10%', '100%', '~15%', '6-7x'],
            ['大规模修改', '50%', '100%', '~55%', '1.8x'],
            ['全新文档', '100%', '100%', '100%', '1x'],
        ],
        col_widths=[1.5, 1.2, 1.2, 1.2, 1.2]
    )

    doc.add_page_break()

    # ============================
    # 第15章 全量 Rebuild
    # ============================
    add_heading(doc, '第15章 全量 Rebuild', level=1)

    add_heading(doc, '15.1 Rebuild 适用场景', level=2)
    add_bullet(doc, 'Embedding 模型更换：从 bge-base 升级到 bge-m3，维度或语义空间变化')
    add_bullet(doc, 'Chunk 策略调整：分块大小、分块策略变更，需要重新切分')
    add_bullet(doc, '数据不一致：向量库与元数据不一致，需要重建校准')
    add_bullet(doc, 'Collection 迁移：从一个 Qdrant 集群迁移到另一个')
    add_bullet(doc, '索引优化：重建 Collection 以优化 HNSW 索引结构')

    add_heading(doc, '15.2 Rebuild 流程', level=2)
    add_code_block(doc, '''1. 创建临时 Collection（新配置）
   ↓
2. 遍历所有 Document 的当前版本
   ↓
3. 逐个文档：解析 → 分块 → 向量化 → 写入新 Collection
   ↓
4. 进度追踪：已完成 / 总数 / 失败列表
   ↓
5. 完成后校验：
   - 文档数量对比
   - Chunk 总数对比
   - 抽样相似度校验
   ↓
6. 切换（原子操作）：
   - 方案 A：删除旧 Collection，重命名新 Collection
   - 方案 B：更新元数据中的 collection_name 指针
   ↓
7. 清理旧 Collection（保留 N 天备份）''')

    add_heading(doc, '15.3 安全保障', level=2)
    add_bullet(doc, '蓝绿部署：新 Collection 构建期间，旧 Collection 持续提供服务')
    add_bullet(doc, '失败回滚：Rebuild 失败不影响旧 Collection，随时可终止')
    add_bullet(doc, '断点续建：Rebuild 任务支持暂停和继续（记录进度到任务表）')
    add_bullet(doc, '速率限制：可配置 Rebuild 并发数，避免耗尽资源影响在线服务')
    add_bullet(doc, '增量切换：Rebuild 完成后，切换前新写入的数据需要同步（短暂双写）')

    add_heading(doc, '15.4 Rebuild 任务管理', level=2)
    add_para(doc, '全量 Rebuild 作为后台任务执行，前端可查看进度、暂停、取消：')
    add_bullet(doc, '任务状态：pending / running / paused / completed / failed / cancelled')
    add_bullet(doc, '进度指标：总文档数、已处理数、失败数、当前文档、预估剩余时间')
    add_bullet(doc, '失败重试：单个文档失败可重试 N 次，记录失败原因')
    add_bullet(doc, '任务日志：实时输出 Rebuild 日志，便于排查问题')

    doc.add_page_break()

    # ============================
    # 第16章 Task 任务系统
    # ============================
    add_heading(doc, '第16章 Task 任务系统', level=1)

    add_heading(doc, '16.1 任务系统设计', level=2)
    add_para(doc, '文档上传后的解析、分块、向量化等耗时操作通过异步任务系统执行。采用轻量设计：使用 APScheduler + 异步 Worker 线程池，无需额外部署 Celery 和 Redis（可选支持）。')

    add_heading(doc, '16.2 任务类型', level=2)
    add_table(doc,
        ['任务类型', '触发方式', '耗时估计', '说明'],
        [
            ['document_process', '上传后自动', '秒~分钟级', '文档解析+分块+向量化'],
            ['document_reindex', '手动触发', '秒~分钟级', '单文档重索引'],
            ['collection_rebuild', '手动触发', '分钟~小时级', '全量 Rebuild'],
            ['consistency_check', '定时/手动', '分钟级', '一致性检查'],
            ['upload_cleanup', '定时', '秒级', '清理过期上传会话'],
            ['storage_gc', '定时', '分钟级', '存储垃圾回收'],
        ],
        col_widths=[1.8, 1.3, 1.3, 2.5]
    )

    add_heading(doc, '16.3 任务状态模型', level=2)
    add_code_block(doc, '''任务状态流转：
pending → queued → running → completed
                ↓         ↓
              failed    cancelled
                ↓
            retrying (→ running)

任务数据模型 tasks:
- id (UUID)                    # 任务 ID
- type (string)                # 任务类型
- status (string)              # 状态
- priority (integer)           # 优先级（0-10，越大越优先）
- payload (JSON)               # 任务参数
- result (JSON)                # 任务结果
- error_message (text)         # 错误信息
- progress (float)             # 进度 0.0 - 1.0
- progress_detail (JSON)       # 详细进度
- retry_count (integer)        # 已重试次数
- max_retries (integer)        # 最大重试次数
- started_at (datetime)
- completed_at (datetime)
- created_at (datetime)
- updated_at (datetime)''')

    add_heading(doc, '16.4 任务调度与执行', level=2)
    add_bullet(doc, '调度器：APScheduler BackgroundScheduler，单进程内调度')
    add_bullet(doc, '执行器：asyncio 事件循环内的任务队列，支持并发数控制')
    add_bullet(doc, '任务持久化：任务记录存入数据库，服务重启后可恢复')
    add_bullet(doc, '失败重试：指数退避重试，默认最多 3 次')
    add_bullet(doc, '进度上报：任务执行中更新 progress 字段，前端通过 API 轮询或 WebSocket 推送')
    add_bullet(doc, '取消任务：支持运行中任务的协作式取消（检查取消标志位）')

    add_heading(doc, '16.5 前端任务进度展示', level=2)
    add_para(doc, '前端提供任务中心页面，展示所有任务列表、状态、进度、耗时。支持筛选（按类型、状态、时间）、查看详情、重试失败任务、取消运行中任务。文档处理任务在文档列表中以内联进度条形式展示。')

    doc.add_page_break()

    # ============================
    # 第17章 Storage 抽象
    # ============================
    add_heading(doc, '第17章 Storage 抽象', level=1)

    add_heading(doc, '17.1 设计目标', level=2)
    add_para(doc, '文件存储层抽象，支持多种后端存储（本地文件系统、S3 兼容对象存储、MinIO、阿里云 OSS 等），业务层不依赖具体存储实现，可通过配置切换。')

    add_heading(doc, '17.2 Storage 接口定义', level=2)
    add_code_block(doc, '''class BaseStorage(ABC):
    """存储抽象基类"""

    @abstractmethod
    async def upload(self, key: str, data: bytes | AsyncIterator) -> None:
        """上传文件"""
        pass

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """下载文件（全量）"""
        pass

    @abstractmethod
    async def download_stream(self, key: str) -> AsyncIterator[bytes]:
        """流式下载"""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """文件是否存在"""
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除文件"""
        pass

    @abstractmethod
    async def get_size(self, key: str) -> int:
        """获取文件大小"""
        pass

    @abstractmethod
    async def get_signed_url(self, key: str, expires: int = 3600) -> str:
        """获取临时访问 URL（对象存储）"""
        pass''')

    add_heading(doc, '17.3 存储后端实现', level=2)
    add_table(doc,
        ['后端类型', '驱动', '适用场景', '配置关键字'],
        [
            ['本地文件系统', '本地 I/O + aiofiles', '单机部署、开发测试', 'local'],
            ['MinIO', 'minio / boto3', '私有部署对象存储', 'minio'],
            ['AWS S3', 'boto3 / aioboto3', '公有云 S3', 's3'],
            ['阿里云 OSS', 'oss2 / aliyun-oss', '阿里云生态', 'oss'],
            ['七牛云 Kodo', 'qiniu', '七牛云生态', 'qiniu'],
        ],
        col_widths=[1.5, 1.8, 1.8, 1.2]
    )

    add_heading(doc, '17.4 文件存储路径约定', level=2)
    add_code_block(doc, '''# 文件存储目录结构
{storage_root}/
├── documents/
│   └── {collection_id}/
│       └── {document_id}/
│           └── {version_id}/
│               ├── original.pdf            # 原始文件
│               ├── extracted.txt           # 提取的纯文本
│               └── chunks.json             # Chunk 元数据（可选备份）
├── uploads/
│   └── {upload_id}/
│       └── chunk_{index}.tmp               # 临时分片
└── temp/
    └── {task_id}/...                       # 任务临时文件''')

    add_heading(doc, '17.5 存储选择策略', level=2)
    add_bullet(doc, '通过 STORAGE_BACKEND 环境变量指定存储后端')
    add_bullet(doc, '工厂模式：StorageFactory.get_backend() 根据配置返回对应实现')
    add_bullet(doc, '业务层只依赖 BaseStorage 接口，实现可替换')
    add_bullet(doc, '支持大文件流式上传/下载，内存占用低')

    doc.add_page_break()

    # ============================
    # 第18章 一致性检查与恢复
    # ============================
    add_heading(doc, '第18章 一致性检查与恢复', level=1)

    add_heading(doc, '18.1 一致性问题来源', level=2)
    add_para(doc, '向量数据库与元数据数据库是两个独立存储系统，在以下场景可能出现不一致：')
    add_bullet(doc, '任务中断：文档处理过程中服务崩溃，部分 Chunk 已写入 Qdrant 但元数据未更新')
    add_bullet(doc, '删除失败：删除文档时 Qdrant 删除失败，但元数据已标记删除')
    add_bullet(doc, '版本切换：新版本写入成功但旧版本清理失败，导致残留向量')
    add_bullet(doc, '人工误操作：直接在 Qdrant 控制台删除或修改数据')
    add_bullet(doc, '硬件故障：磁盘损坏、网络分区导致数据不一致')

    add_heading(doc, '18.2 一致性检查维度', level=2)
    add_table(doc,
        ['检查项', '检查方法', '严重程度', '自动修复'],
        [
            ['孤儿向量', 'Qdrant 中有但元数据中无的 Point', '中', '删除孤儿向量'],
            ['缺失向量', '元数据中有 Chunk 但 Qdrant 中无', '高', '重新向量化'],
            ['版本不匹配', 'current_version_id 指向不存在的版本', '高', '回滚到上一版本'],
            ['文件丢失', '元数据引用的文件不存在', '高', '标记异常，需重新上传'],
            ['Chunk 数不符', '版本的 chunk_count 与实际 Chunk 数不一致', '中', '更新计数'],
            ['向量维度不一致', 'Point 向量维度与 Collection 配置不符', '高', '重新向量化'],
            ['Payload 不完整', 'Payload 缺少必要字段', '低', '补齐字段'],
        ],
        col_widths=[1.5, 2.2, 1.0, 1.8]
    )

    add_heading(doc, '18.3 检查流程', level=2)
    add_code_block(doc, '''1. 元数据侧扫描：
   - 获取 Collection 下所有 Document + current_version_id
   - 获取每个 Version 的所有 Chunk ID
   - 构建预期 Point 集合 S_meta

2. Qdrant 侧扫描：
   - Scroll 遍历 Collection 所有 Point
   - 构建实际 Point 集合 S_qdrant

3. 对比计算：
   - S_qdrant - S_meta = 孤儿向量（需删除）
   - S_meta - S_qdrant = 缺失向量（需重建）
   - 交集内 Payload 字段校验

4. 生成报告：
   - 统计各类不一致数量
   - 列出异常详情（前 100 条）
   - 评估修复影响范围

5. 执行修复（可选）：
   - 用户确认后执行修复脚本
   - 修复过程记录日志
   - 修复后重新检查验证''')

    add_heading(doc, '18.4 恢复策略', level=2)
    add_bullet(doc, '删除孤儿向量：直接 delete by filter，安全无风险')
    add_bullet(doc, '重建缺失向量：根据 Chunk 文本重新向量化后 upsert')
    add_bullet(doc, '文件丢失恢复：无法自动恢复，标记文档为异常状态，提示用户重新上传')
    add_bullet(doc, '版本回滚：current_version_id 指向上一个有效版本，同时触发向量切换')
    add_bullet(doc, '全量重建：严重不一致时，触发 Collection Rebuild')

    add_heading(doc, '18.5 定时检查', level=2)
    add_para(doc, '支持配置定时一致性检查（默认每天凌晨 3 点），检查结果生成报告，异常情况可配置告警（Webhook / 邮件）。检查模式支持快速检查（仅计数对比）和完整检查（逐条比对）。')

    doc.add_page_break()

    # ============================
    # 第19章 模型对话验证模块
    # ============================
    add_heading(doc, '第19章 模型对话验证模块', level=1)

    add_heading(doc, '19.1 模块定位', level=2)
    add_para(doc, '对话验证模块是内置的 RAG 测试工具，允许用户在管理界面中直接进行问答对话，验证向量检索效果和大模型回答质量。该模块用于知识库质量验收、调参验证和日常使用，不作为生产级对话服务（生产对话建议在业务系统中对接）。')

    add_heading(doc, '19.2 功能特性', level=2)
    add_bullet(doc, '多 Collection 检索：可选择一个或多个 Collection 作为检索源')
    add_bullet(doc, '检索参数可调：top_k、相似度阈值、过滤条件实时调整')
    add_bullet(doc, '引用溯源：回答中引用的 Chunk 高亮显示，点击可查看原文和位置')
    add_bullet(doc, 'Prompt 模板：可自定义 System Prompt 和 RAG 模板')
    add_bullet(doc, '对话历史：多轮对话上下文管理')
    add_bullet(doc, '流式输出：支持 SSE 流式响应，打字机效果')
    add_bullet(doc, '评分反馈：用户可对回答点赞/点踩，收集评估数据')

    add_heading(doc, '19.3 RAG 流水线', level=2)
    add_code_block(doc, '''用户问题
   ↓
1. Query 改写（可选）
   - 多轮对话上下文扩展
   - HyDE（假设文档嵌入）
   ↓
2. 向量检索
   - Embedding 模型编码问题
   - Qdrant 相似度搜索（支持过滤）
   - top_k 结果召回
   ↓
3. 重排序（可选，Reranker）
   - bge-reranker / cross-encoder
   - 精排 top_n（n < k）
   ↓
4. 上下文组装
   - 将检索到的 Chunk 文本拼接
   - 按 Prompt 模板格式化
   - 控制上下文长度（Token 预算）
   ↓
5. LLM 生成
   - 调用配置的 LLM Provider
   - 流式返回响应
   ↓
6. 返回结果
   - 回答文本
   - 引用的 Chunk 列表（含得分）
   - 检索耗时、生成耗时''')

    add_heading(doc, '19.4 对话上下文管理', level=2)
    add_bullet(doc, '会话 ID 标识对话，历史消息存入数据库')
    add_bullet(doc, '上下文窗口：保留最近 N 轮对话（默认 5 轮）')
    add_bullet(doc, 'Token 控制：使用 tiktoken 估算 Token 数，不超过模型上限')
    add_bullet(doc, '上下文压缩：超限时优先丢弃较早的用户消息（保留系统提示和检索上下文）')

    doc.add_page_break()

    # ============================
    # 第20章 LLM Provider
    # ============================
    add_heading(doc, '第20章 DeepSeek / Qwen / Ark / Ollama Provider', level=1)

    add_heading(doc, '20.1 多 Provider 架构', level=2)
    add_para(doc, '采用策略模式封装大模型 Provider，支持通过环境变量动态切换。所有 Provider 实现统一的 chat() 和 chat_stream() 接口，业务层不感知具体实现。')

    add_heading(doc, '20.2 Provider 接口定义', level=2)
    add_code_block(doc, '''class BaseLLMProvider(ABC):
    """LLM Provider 基类"""

    @abstractmethod
    async def chat(self, messages: list[dict],
                   **kwargs) -> LLMResponse:
        """非流式对话"""
        pass

    @abstractmethod
    async def chat_stream(self, messages: list[dict],
                          **kwargs) -> AsyncIterator[str]:
        """流式对话（SSE）"""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 名称"""
        pass

class LLMResponse:
    content: str
    model: str
    usage: dict  # prompt_tokens, completion_tokens, total_tokens
    latency_ms: int''')

    add_heading(doc, '20.3 支持的 Provider', level=2)
    add_table(doc,
        ['Provider', '模型示例', 'API 类型', '配置环境变量', '特点'],
        [
            ['DeepSeek', 'deepseek-chat, deepseek-coder', 'OpenAI 兼容', 'DEEPSEEK_API_KEY', '高质量推理、代码能力强'],
            ['Qwen（阿里）', 'qwen-turbo, qwen-plus, qwen-max', 'OpenAI 兼容', 'DASHSCOPE_API_KEY', '中文优秀、工具调用'],
            ['Ark（字节）', 'doubao-pro, doubao-lite', 'OpenAI 兼容', 'ARK_API_KEY', '豆包模型、多模态'],
            ['Ollama', 'qwen2.5, llama3, mistral', '本地部署', 'OLLAMA_BASE_URL', '本地离线、完全隐私'],
        ],
        col_widths=[1.2, 1.8, 1.2, 1.8, 2.0]
    )

    add_heading(doc, '20.4 环境变量配置', level=2)
    add_code_block(doc, '''# .env 中 LLM 相关配置

# 当前使用的 Provider：deepseek / qwen / ark / ollama
LLM_PROVIDER=ollama

# 模型名称
LLM_MODEL=qwen2.5:7b

# ========== DeepSeek ==========
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# ========== Qwen（阿里）==========
DASHSCOPE_API_KEY=sk-xxx
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# ========== Ark（字节）==========
ARK_API_KEY=ark-xxx
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=ep-xxx  # 接入点 ID

# ========== Ollama ==========
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# 通用参数
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2048
LLM_TIMEOUT=120''')

    add_heading(doc, '20.5 Provider 工厂与自动切换', level=2)
    add_code_block(doc, '''class LLMFactory:
    """LLM Provider 工厂"""

    _providers: dict[str, BaseLLMProvider] = {}

    @classmethod
    def get_provider(cls, provider_name: str = None) -> BaseLLMProvider:
        provider_name = provider_name or settings.LLM_PROVIDER
        if provider_name not in cls._providers:
            if provider_name == "deepseek":
                cls._providers[provider_name] = DeepSeekProvider(
                    api_key=settings.DEEPSEEK_API_KEY,
                    base_url=settings.DEEPSEEK_BASE_URL,
                    model=settings.LLM_MODEL,
                )
            elif provider_name == "qwen":
                cls._providers[provider_name] = QwenProvider(...)
            elif provider_name == "ark":
                cls._providers[provider_name] = ArkProvider(...)
            elif provider_name == "ollama":
                cls._providers[provider_name] = OllamaProvider(...)
            else:
                raise ValueError(f"Unknown provider: {provider_name}")
        return cls._providers[provider_name]

# 使用方式
llm = LLMFactory.get_provider()
response = await llm.chat(messages=[...])''')

    doc.add_page_break()

    # ============================
    # 第21章 FastAPI API 设计
    # ============================
    add_heading(doc, '第21章 FastAPI API 设计', level=1)

    add_heading(doc, '21.1 API 设计原则', level=2)
    add_bullet(doc, 'RESTful 风格：资源命名清晰，HTTP 方法语义准确')
    add_bullet(doc, '统一响应格式：{ code, data, message, request_id }')
    add_bullet(doc, 'Pydantic 校验：所有请求响应使用 Schema，自动生成 OpenAPI 文档')
    add_bullet(doc, '版本化：API 路径前缀 /api/v1/，便于后续升级')
    add_bullet(doc, '分页规范：使用 page + page_size，返回 total + items')
    add_bullet(doc, '错误码规范：HTTP 状态码 + 业务错误码双层设计')

    add_heading(doc, '21.2 统一响应格式', level=2)
    add_code_block(doc, '''# 成功响应
{
  "code": 0,
  "message": "success",
  "data": { ... },
  "request_id": "uuid"
}

# 分页响应
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [...],
    "total": 100,
    "page": 1,
    "page_size": 20
  },
  "request_id": "uuid"
}

# 错误响应
{
  "code": 40001,
  "message": "参数校验失败",
  "detail": { "field": "name", "error": "required" },
  "request_id": "uuid"
}''')

    add_heading(doc, '21.3 Collection API', level=2)
    add_table(doc,
        ['方法', '路径', '说明'],
        [
            ['GET', '/api/v1/collections', '获取 Collection 列表（分页）'],
            ['POST', '/api/v1/collections', '创建 Collection'],
            ['GET', '/api/v1/collections/{id}', '获取 Collection 详情'],
            ['PUT', '/api/v1/collections/{id}', '更新 Collection 信息'],
            ['DELETE', '/api/v1/collections/{id}', '删除 Collection'],
            ['POST', '/api/v1/collections/{id}/rebuild', '触发全量 Rebuild'],
            ['GET', '/api/v1/collections/{id}/stats', '获取统计信息（文档数、向量数等）'],
        ],
        col_widths=[1.0, 3.2, 2.5]
    )

    add_heading(doc, '21.4 Directory API', level=2)
    add_table(doc,
        ['方法', '路径', '说明'],
        [
            ['GET', '/api/v1/collections/{cid}/directories/tree', '获取完整目录树'],
            ['POST', '/api/v1/collections/{cid}/directories', '创建目录'],
            ['PUT', '/api/v1/directories/{id}', '更新目录（重命名）'],
            ['DELETE', '/api/v1/directories/{id}', '删除目录'],
            ['POST', '/api/v1/directories/{id}/move', '移动目录'],
            ['GET', '/api/v1/directories/{id}/children', '获取子目录和文档列表'],
        ],
        col_widths=[1.0, 3.8, 1.8]
    )

    add_heading(doc, '21.5 Document API', level=2)
    add_table(doc,
        ['方法', '路径', '说明'],
        [
            ['POST', '/api/v1/documents/upload/init', '初始化分片上传'],
            ['PUT', '/api/v1/documents/upload/{upload_id}/{index}', '上传分片'],
            ['POST', '/api/v1/documents/upload/{upload_id}/complete', '完成上传并合并'],
            ['GET', '/api/v1/documents/upload/{upload_id}/status', '查询上传状态'],
            ['GET', '/api/v1/collections/{cid}/documents', '获取文档列表（分页+过滤）'],
            ['GET', '/api/v1/documents/{id}', '获取文档详情'],
            ['DELETE', '/api/v1/documents/{id}', '删除文档'],
            ['POST', '/api/v1/documents/{id}/reindex', '重新索引文档'],
            ['GET', '/api/v1/documents/{id}/versions', '获取版本列表'],
            ['POST', '/api/v1/documents/{id}/rollback/{ver_id}', '回滚到指定版本'],
            ['GET', '/api/v1/documents/{id}/download', '下载原始文件'],
        ],
        col_widths=[1.0, 4.0, 1.8]
    )

    add_heading(doc, '21.6 Vector / Search API', level=2)
    add_table(doc,
        ['方法', '路径', '说明'],
        [
            ['POST', '/api/v1/collections/{cid}/search', '语义检索'],
            ['POST', '/api/v1/collections/{cid}/search/hybrid', '混合检索（稠密+稀疏）'],
            ['GET', '/api/v1/collections/{cid}/points/{point_id}', '获取单个 Point 详情'],
            ['DELETE', '/api/v1/collections/{cid}/points/{point_id}', '删除单个 Point'],
            ['POST', '/api/v1/collections/{cid}/points/delete-by-filter', '按过滤条件批量删除'],
        ],
        col_widths=[1.0, 3.8, 1.8]
    )

    add_heading(doc, '21.7 Task API', level=2)
    add_table(doc,
        ['方法', '路径', '说明'],
        [
            ['GET', '/api/v1/tasks', '获取任务列表（分页+过滤）'],
            ['GET', '/api/v1/tasks/{id}', '获取任务详情'],
            ['GET', '/api/v1/tasks/{id}/progress', '获取任务进度（SSE 可选）'],
            ['POST', '/api/v1/tasks/{id}/cancel', '取消任务'],
            ['POST', '/api/v1/tasks/{id}/retry', '重试失败任务'],
            ['DELETE', '/api/v1/tasks/{id}', '删除任务记录'],
        ],
        col_widths=[1.0, 3.5, 2.0]
    )

    add_heading(doc, '21.8 Chat API', level=2)
    add_table(doc,
        ['方法', '路径', '说明'],
        [
            ['POST', '/api/v1/chat', '对话（非流式）'],
            ['POST', '/api/v1/chat/stream', '对话（SSE 流式）'],
            ['GET', '/api/v1/chat/sessions', '会话列表'],
            ['GET', '/api/v1/chat/sessions/{id}', '会话详情（历史消息）'],
            ['DELETE', '/api/v1/chat/sessions/{id}', '删除会话'],
            ['POST', '/api/v1/chat/messages/{id}/feedback', '消息反馈（点赞/点踩）'],
        ],
        col_widths=[1.0, 3.5, 2.0]
    )

    add_heading(doc, '21.9 System API', level=2)
    add_table(doc,
        ['方法', '路径', '说明'],
        [
            ['GET', '/api/v1/system/health', '健康检查'],
            ['GET', '/api/v1/system/config', '系统配置（前端需要的公开配置）'],
            ['POST', '/api/v1/system/consistency-check', '触发一致性检查'],
            ['GET', '/api/v1/system/storage-usage', '存储使用情况'],
            ['GET', '/api/v1/system/models', '可用模型列表'],
        ],
        col_widths=[1.0, 3.5, 2.0]
    )

    doc.add_page_break()

    # ============================
    # 第22章 前端规划
    # ============================
    add_heading(doc, '第22章 Vue3 + Element Plus + UnoCSS + Vite8 前端规划', level=1)

    add_heading(doc, '22.1 技术栈', level=2)
    add_bullet(doc, '框架：Vue 3.5+（Composition API + <script setup>）')
    add_bullet(doc, '语言：TypeScript 5.x')
    add_bullet(doc, '构建工具：Vite 8（极速 HMR、Rollup 打包）')
    add_bullet(doc, 'UI 组件库：Element Plus 2.x')
    add_bullet(doc, '原子 CSS：UnoCSS（preset-uno + preset-icons + preset-attributify）')
    add_bullet(doc, '状态管理：Pinia')
    add_bullet(doc, '路由：Vue Router 4.x')
    add_bullet(doc, 'HTTP 客户端：Axios（拦截器统一处理鉴权、错误）')
    add_bullet(doc, '代码规范：ESLint + Prettier + Stylelint')

    add_heading(doc, '22.2 页面结构规划', level=2)
    add_code_block(doc, '''src/pages/
├── login/                       # 登录页（可选）
├── layout/
│   ├── BasicLayout.vue          # 主布局（侧边栏+顶栏+内容区）
│   └── components/
│       ├── SideMenu.vue         # 侧边导航
│       ├── HeaderBar.vue        # 顶部栏
│       └── Breadcrumb.vue       # 面包屑
├── collections/
│   ├── CollectionList.vue       # Collection 列表
│   ├── CollectionDetail.vue     # Collection 详情（概览+统计）
│   └── CollectionSettings.vue   # Collection 设置
├── documents/
│   ├── DocumentTree.vue         # 目录树+文档列表（主工作区）
│   ├── DocumentDetail.vue       # 文档详情（版本、Chunk 列表）
│   ├── DocumentUpload.vue       # 上传组件（拖拽+分片+秒传）
│   └── ChunkViewer.vue          # Chunk 查看器
├── search/
│   └── VectorSearch.vue         # 向量检索测试页
├── chat/
│   ├── ChatPage.vue             # 对话验证页
│   ├── ChatMessage.vue          # 消息气泡组件
│   ├── ChatSidebar.vue          # 会话列表侧边栏
│   └── ReferencePanel.vue       # 引用溯源面板
├── tasks/
│   └── TaskCenter.vue           # 任务中心
└── system/
    ├── ConsistencyCheck.vue     # 一致性检查
    ├── Settings.vue             # 系统设置
    └── Models.vue               # 模型管理''')

    add_heading(doc, '22.3 核心功能界面', level=2)

    add_para(doc, '文档管理主界面：', bold=True, first_line_indent=False)
    add_bullet(doc, '左侧：Collection 切换 + 目录树（支持拖拽、新建、重命名、删除）')
    add_bullet(doc, '中间：文档列表（表格/卡片切换，支持搜索、排序、批量操作）')
    add_bullet(doc, '右侧：文档详情抽屉（基本信息、版本列表、Chunk 预览）')
    add_bullet(doc, '上传：拖拽上传 + 文件选择器，进度条实时展示，秒传提示')

    add_para(doc, '对话验证界面：', bold=True, first_line_indent=False)
    add_bullet(doc, '左侧：会话列表（新建、删除、重命名）')
    add_bullet(doc, '中间：对话主区域（消息气泡、流式打字效果）')
    add_bullet(doc, '右侧：检索引用面板（展示引用的 Chunk、相似度分数、原文预览）')
    add_bullet(doc, '底部：输入框 + 参数设置（模型、top_k、温度等）')

    add_para(doc, '任务中心界面：', bold=True, first_line_indent=False)
    add_bullet(doc, '任务列表（按类型、状态、时间筛选）')
    add_bullet(doc, '任务详情（进度条、日志输出、耗时）')
    add_bullet(doc, '操作按钮（重试、取消、查看结果）')

    add_heading(doc, '22.4 关键技术点', level=2)
    add_bullet(doc, '文件上传：使用 Web Worker 计算 SHA256，不阻塞 UI；支持并发分片上传')
    add_bullet(doc, 'SSE 流式对话：EventSource + 自定义解析，实现打字机效果')
    add_bullet(doc, '虚拟滚动：文档列表、Chunk 列表数据量大时使用虚拟滚动优化性能')
    add_bullet(doc, 'UnoCSS 原子化：减少 CSS 体积，开发效率高')
    add_bullet(doc, 'Pinia 状态管理：用户信息、当前 Collection、任务列表等全局状态')
    add_bullet(doc, '全局错误处理：Axios 拦截器统一处理 401、403、500 等错误')

    add_heading(doc, '22.5 前端目录结构', level=2)
    add_code_block(doc, '''src/
├── api/                # API 接口封装
│   ├── collection.ts
│   ├── document.ts
│   ├── upload.ts
│   ├── search.ts
│   ├── chat.ts
│   ├── task.ts
│   └── system.ts
├── assets/             # 静态资源
├── components/         # 通用组件
│   ├── common/
│   └── business/
├── composables/        # 组合式函数
│   ├── useUpload.ts
│   ├── useChat.ts
│   ├── useTask.ts
│   └── useTree.ts
├── layouts/            # 布局组件
├── pages/              # 页面组件
├── router/             # 路由配置
├── stores/             # Pinia 状态
│   ├── user.ts
│   ├── collection.ts
│   └── task.ts
├── styles/             # 全局样式
├── utils/              # 工具函数
│   ├── request.ts      # Axios 封装
│   ├── hash.ts         # SHA256 计算
│   └── format.ts
├── types/              # TypeScript 类型定义
├── App.vue
└── main.ts''')

    doc.add_page_break()

    # ============================
    # 第23章 项目目录结构
    # ============================
    add_heading(doc, '第23章 项目目录结构', level=1)

    add_heading(doc, '23.1 后端目录结构', level=2)
    add_code_block(doc, '''vector-data-manager/
├── backend/
│   ├── pyproject.toml           # uv 项目配置
│   ├── uv.lock                  # 依赖锁定
│   ├── .env.example             # 环境变量示例
│   ├── README.md
│   ├── app/
│   │   ├── main.py              # FastAPI 应用入口
│   │   ├── api/                 # API 路由层
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── collection.py
│   │   │   │   ├── directory.py
│   │   │   │   ├── document.py
│   │   │   │   ├── upload.py
│   │   │   │   ├── search.py
│   │   │   │   ├── chat.py
│   │   │   │   ├── task.py
│   │   │   │   └── system.py
│   │   │   └── deps.py          # 依赖注入
│   │   ├── services/            # 业务逻辑层
│   │   │   ├── collection_service.py
│   │   │   ├── directory_service.py
│   │   │   ├── document_service.py
│   │   │   ├── upload_service.py
│   │   │   ├── vector_service.py
│   │   │   ├── chat_service.py
│   │   │   ├── task_service.py
│   │   │   └── consistency_service.py
│   │   ├── repositories/        # 数据访问层
│   │   │   ├── qdrant_repo.py
│   │   │   ├── document_repo.py
│   │   │   └── task_repo.py
│   │   ├── models/              # 数据模型
│   │   │   ├── schemas/         # Pydantic Schemas
│   │   │   │   ├── collection.py
│   │   │   │   ├── document.py
│   │   │   │   ├── upload.py
│   │   │   │   ├── chat.py
│   │   │   │   └── task.py
│   │   │   └── db/              # ORM Models
│   │   │       ├── base.py
│   │   │       ├── collection.py
│   │   │       ├── directory.py
│   │   │       ├── document.py
│   │   │       ├── upload.py
│   │   │       └── task.py
│   │   ├── providers/           # 外部服务提供者
│   │   │   ├── embedding/
│   │   │   │   ├── base.py
│   │   │   │   └── huggingface_embedding.py
│   │   │   ├── llm/
│   │   │   │   ├── base.py
│   │   │   │   ├── factory.py
│   │   │   │   ├── deepseek_provider.py
│   │   │   │   ├── qwen_provider.py
│   │   │   │   ├── ark_provider.py
│   │   │   │   └── ollama_provider.py
│   │   │   └── storage/
│   │   │       ├── base.py
│   │   │       ├── factory.py
│   │   │       ├── local_storage.py
│   │   │       └── s3_storage.py
│   │   ├── tasks/               # 异步任务
│   │   │   ├── worker.py
│   │   │   ├── document_tasks.py
│   │   │   ├── collection_tasks.py
│   │   │   └── scheduler.py
│   │   ├── parsers/             # 文件解析器
│   │   │   ├── base.py
│   │   │   ├── pdf_parser.py
│   │   │   ├── docx_parser.py
│   │   │   ├── txt_parser.py
│   │   │   ├── markdown_parser.py
│   │   │   └── excel_parser.py
│   │   ├── chunkers/            # 分块策略
│   │   │   ├── base.py
│   │   │   ├── recursive_chunker.py
│   │   │   ├── semantic_chunker.py
│   │   │   └── fixed_size_chunker.py
│   │   ├── core/                # 核心配置
│   │   │   ├── config.py        # 配置管理
│   │   │   ├── exceptions.py    # 异常定义
│   │   │   ├── logging.py       # 日志配置
│   │   │   ├── middleware.py    # 中间件
│   │   │   └── database.py      # 数据库连接
│   │   └── utils/               # 工具函数
│   │       ├── hash_utils.py
│   │       ├── text_cleaner.py
│   │       ├── token_utils.py
│   │       └── file_utils.py
│   ├── data/                    # 数据目录（运行时生成）
│   │   ├── db/
│   │   ├── storage/
│   │   └── models/
│   └── tests/                   # 测试
│       ├── unit/
│       └── integration/
├── frontend/                    # 前端项目（Vue3）
│   ├── package.json
│   ├── vite.config.ts
│   ├── uno.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
├── docker/
│   ├── Dockerfile.backend
│   ├── Dockerfile.frontend
│   └── nginx.conf
├── docker-compose.yml
└── .env.example''')

    doc.add_page_break()

    # ============================
    # 第24章 .env 配置
    # ============================
    add_heading(doc, '第24章 .env 配置', level=1)

    add_heading(doc, '24.1 配置说明', level=2)
    add_para(doc, '所有可配置项通过环境变量管理，使用 python-dotenv 加载 .env 文件。配置项分为：基础配置、数据库配置、Qdrant 配置、存储配置、模型配置、LLM 配置、安全配置。')

    add_heading(doc, '24.2 完整 .env 示例', level=2)
    add_code_block(doc, '''# ==============================================
# Vector Data Manager 环境配置
# ==============================================

# ---------- 基础配置 ----------
APP_NAME=Vector Data Manager
APP_ENV=production          # development / production
APP_DEBUG=false
APP_HOST=0.0.0.0
APP_PORT=8000
SECRET_KEY=your-secret-key-here-change-in-production
CORS_ORIGINS=*              # 逗号分隔，如 http://localhost:5173,http://example.com
WORKERS=1                   # Uvicorn worker 数

# ---------- 数据库配置 ----------
DB_DRIVER=sqlite+aiosqlite  # sqlite+aiosqlite / postgresql+asyncpg
DB_HOST=localhost
DB_PORT=5432
DB_USER=vdm
DB_PASSWORD=vdm_password
DB_NAME=vdm_db
# SQLite 路径（使用 SQLite 时有效）
DB_PATH=./data/vdm.db

# ---------- Qdrant 配置 ----------
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_API_KEY=            # 空表示不需要认证
QDRANT_USE_GRPC=false
QDRANT_COLLECTION_PREFIX=vdm_

# ---------- Redis 配置（可选） ----------
REDIS_ENABLED=false
REDIS_URL=redis://localhost:6379/0

# ---------- 存储配置 ----------
STORAGE_BACKEND=local      # local / s3 / minio / oss
STORAGE_ROOT=./data/storage

# S3 / MinIO 配置
S3_ENDPOINT=
S3_REGION=us-east-1
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_BUCKET=vdm-storage
S3_SECURE=true

# 阿里云 OSS 配置
OSS_ENDPOINT=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_BUCKET=

# ---------- 上传配置 ----------
UPLOAD_CHUNK_SIZE=5242880  # 5MB
UPLOAD_MAX_FILE_SIZE=10737418240  # 10GB
UPLOAD_EXPIRE_HOURS=24
UPLOAD_CONCURRENT_CHUNKS=3

# ---------- Embedding 模型配置 ----------
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=auto      # auto / cpu / cuda
EMBEDDING_BATCH_SIZE=32
EMBEDDING_CACHE_DIR=./data/models
EMBEDDING_NORMALIZE=true

# ---------- Chunk 配置 ----------
CHUNK_STRATEGY=recursive   # recursive / semantic / fixed / sentence
CHUNK_SIZE=500
CHUNK_OVERLAP=50
CHUNK_MIN_SIZE=50

# ---------- LLM 配置 ----------
LLM_PROVIDER=ollama        # deepseek / qwen / ark / ollama
LLM_MODEL=qwen2.5:7b
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2048
LLM_TIMEOUT=120

# DeepSeek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Qwen（阿里云百炼）
DASHSCOPE_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# Ark（字节火山引擎）
ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL_ENDPOINT=

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# ---------- 任务配置 ----------
TASK_MAX_WORKERS=4
TASK_MAX_RETRIES=3
TASK_RETRY_DELAY=30        # 秒
TASK_CLEANUP_DAYS=7        # 任务记录保留天数

# ---------- 一致性检查配置 ----------
CONSISTENCY_CHECK_ENABLED=true
CONSISTENCY_CHECK_SCHEDULE=0 3 * * *    # 每天凌晨 3 点
CONSISTENCY_CHECK_MODE=quick             # quick / full

# ---------- 日志配置 ----------
LOG_LEVEL=INFO
LOG_FILE=./data/logs/vdm.log
LOG_MAX_BYTES=10485760     # 10MB
LOG_BACKUP_COUNT=5

# ---------- 安全配置 ----------
AUTH_ENABLED=false         # 是否启用认证
JWT_SECRET=change-this-in-production
JWT_EXPIRE_DAYS=7
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123

# ---------- 限流配置 ----------
RATE_LIMIT_ENABLED=false
RATE_LIMIT_PER_MINUTE=60''')

    doc.add_page_break()

    # ============================
    # 第25章 uv 依赖
    # ============================
    add_heading(doc, '第25章 uv 依赖', level=1)

    add_heading(doc, '25.1 运行时依赖', level=2)
    add_table(doc,
        ['依赖包', '版本范围', '用途'],
        [
            ['fastapi', '>=0.115.0', 'Web 框架'],
            ['uvicorn[standard]', '>=0.30.0', 'ASGI 服务器'],
            ['pydantic', '>=2.9.0', '数据校验'],
            ['pydantic-settings', '>=2.5.0', '配置管理（环境变量）'],
            ['python-multipart', '>=0.0.9', '文件上传支持'],
            ['qdrant-client[fastembed]', '>=1.12.0', 'Qdrant 客户端'],
            ['sentence-transformers', '>=3.0.0', 'Embedding 模型推理'],
            ['torch', '>=2.3.0', 'PyTorch（sentence-transformers 依赖）'],
            ['PyMuPDF', '>=1.24.0', 'PDF 解析'],
            ['python-docx', '>=1.1.0', 'DOCX 解析'],
            ['openpyxl', '>=3.1.0', 'Excel 解析'],
            ['markdown-it-py', '>=3.0.0', 'Markdown 解析'],
            ['sqlalchemy[asyncio]', '>=2.0.0', 'ORM + 异步数据库'],
            ['aiosqlite', '>=0.20.0', 'SQLite 异步驱动'],
            ['apscheduler', '>=3.10.0', '任务调度'],
            ['httpx', '>=0.27.0', '异步 HTTP 客户端（LLM 调用）'],
            ['aiofiles', '>=24.0.0', '异步文件操作'],
            ['python-dotenv', '>=1.0.0', '.env 文件加载'],
            ['jinja2', '>=3.1.0', '模板引擎（如需要）'],
            ['tiktoken', '>=0.7.0', 'Token 计数'],
            ['python-jose[cryptography]', '>=3.3.0', 'JWT 认证'],
            ['passlib[bcrypt]', '>=1.7.0', '密码哈希'],
            ['aioboto3', '>=12.0.0', 'S3 异步客户端（可选）'],
            ['pypinyin', '>=0.51.0', '拼音排序（可选）'],
        ],
        col_widths=[2.5, 1.5, 3.0]
    )

    add_heading(doc, '25.2 开发依赖', level=2)
    add_table(doc,
        ['依赖包', '版本范围', '用途'],
        [
            ['pytest', '>=8.0.0', '测试框架'],
            ['pytest-asyncio', '>=0.24.0', '异步测试支持'],
            ['pytest-cov', '>=5.0.0', '覆盖率报告'],
            ['ruff', '>=0.7.0', '代码 lint + 格式化（替代 flake8+black）'],
            ['mypy', '>=1.11.0', '类型检查'],
            ['pre-commit', '>=3.8.0', 'Git 钩子'],
            ['httpie', '>=3.2.0', 'HTTP 测试工具'],
        ],
        col_widths=[2.5, 1.5, 3.0]
    )

    add_heading(doc, '25.3 uv 工作流', level=2)
    add_code_block(doc, '''# 1. 初始化项目（已有 pyproject.toml 时跳过）
uv init --app

# 2. 安装所有依赖（从 uv.lock 精确还原）
uv sync

# 3. 添加新依赖
uv add qdrant-client
uv add --dev pytest

# 4. 移除依赖
uv remove some-package

# 5. 锁定依赖（生成/更新 uv.lock）
uv lock

# 6. 运行脚本
uv run python app/main.py
uv run uvicorn app.main:app --reload

# 7. 运行测试
uv run pytest

# 8. 代码检查
uv run ruff check .
uv run ruff format .''')

    add_heading(doc, '25.4 依赖分层策略', level=2)
    add_para(doc, '为减小生产环境镜像体积，依赖按功能分组，可按需安装：')
    add_bullet(doc, 'core：核心功能（FastAPI + Qdrant + SQLAlchemy），必须')
    add_bullet(doc, 'embedding：Embedding 模型相关（sentence-transformers + torch）')
    add_bullet(doc, 'parsers：文件解析器（PyMuPDF + python-docx + openpyxl 等）')
    add_bullet(doc, 's3：S3 存储支持（aioboto3）')
    add_bullet(doc, 'auth：认证功能（python-jose + passlib）')
    add_bullet(doc, 'dev：开发依赖（pytest + ruff + mypy）')

    doc.add_page_break()

    # ============================
    # 第26章 Docker Compose
    # ============================
    add_heading(doc, '第26章 Docker Compose 部署', level=1)

    add_heading(doc, '26.1 服务组成', level=2)
    add_para(doc, 'Docker Compose 一键部署包含以下服务：')
    add_table(doc,
        ['服务名', '镜像/构建', '端口', '说明'],
        [
            ['backend', '构建自 Dockerfile.backend', '8000', 'FastAPI 后端服务'],
            ['frontend', '构建自 Dockerfile.frontend', '80', 'Vue3 前端（Nginx 托管）'],
            ['qdrant', 'qdrant/qdrant:v1.10.1', '6333, 6334', 'Qdrant 向量数据库'],
            ['redis', 'redis:7-alpine', '6379', '缓存与队列（可选）'],
            ['nginx', 'nginx:alpine', '80, 443', '反向代理 + 静态资源（可选，独立 frontend 时无需）'],
        ],
        col_widths=[1.2, 2.5, 1.2, 2.0]
    )

    add_heading(doc, '26.2 docker-compose.yml', level=2)
    add_code_block(doc, '''version: "3.8"

services:
  # Qdrant 向量数据库
  qdrant:
    image: qdrant/qdrant:v1.10.1
    container_name: vdm-qdrant
    restart: unless-stopped
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./data/qdrant:/qdrant/storage
    environment:
      - QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY:-}
      - QDRANT__LOG_LEVEL=INFO
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
      interval: 30s
      timeout: 10s
      retries: 5

  # Redis（可选，任务队列/缓存）
  redis:
    image: redis:7-alpine
    container_name: vdm-redis
    restart: unless-stopped
    command: redis-server --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - ./data/redis:/data
    profiles:
      - redis

  # 后端服务
  backend:
    build:
      context: ./backend
      dockerfile: ../docker/Dockerfile.backend
    container_name: vdm-backend
    restart: unless-stopped
    ports:
      - "8000:8000"
    depends_on:
      qdrant:
        condition: service_healthy
    environment:
      - APP_ENV=production
      - QDRANT_HOST=qdrant
      - QDRANT_PORT=6333
      - DB_PATH=/app/data/vdm.db
      - STORAGE_ROOT=/app/data/storage
      - EMBEDDING_CACHE_DIR=/app/data/models
      - LLM_PROVIDER=${LLM_PROVIDER:-ollama}
      - OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
    volumes:
      - ./data/backend:/app/data
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/system/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # 前端服务
  frontend:
    build:
      context: ./frontend
      dockerfile: ../docker/Dockerfile.frontend
    container_name: vdm-frontend
    restart: unless-stopped
    ports:
      - "8080:80"
    depends_on:
      - backend
    environment:
      - VITE_API_BASE_URL=/api

volumes:
  qdrant-data:
  redis-data:''')

    add_heading(doc, '26.3 后端 Dockerfile', level=2)
    add_code_block(doc, '''# docker/Dockerfile.backend
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装 uv
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖（生产环境，不含 dev 依赖）
RUN uv sync --frozen --no-dev

# ===== 运行时镜像 =====
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# 安装系统依赖（PDF 处理等可能需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制虚拟环境
COPY --from=builder /app/.venv .venv

# 复制应用代码
COPY app/ app/
COPY .env.example .env

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/system/health || exit 1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]''')

    add_heading(doc, '26.4 前端 Dockerfile', level=2)
    add_code_block(doc, '''# docker/Dockerfile.frontend
# 构建阶段
FROM node:20-alpine AS builder

WORKDIR /app

# 复制 package 文件
COPY package.json pnpm-lock.yaml* ./

# 安装依赖
RUN npm install -g pnpm && pnpm install --frozen-lockfile

# 复制源码并构建
COPY . .
RUN pnpm build

# ===== 运行阶段（Nginx）=====
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY ../docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]''')

    add_heading(doc, '26.5 Nginx 配置（前端）', level=2)
    add_code_block(doc, '''# docker/nginx.conf
server {
    listen 80;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;

    # 前端路由 fallback（Vue Router history 模式）
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 代理到后端
    location /api/ {
        proxy_pass http://backend:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式响应支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
    }

    # 静态资源缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # gzip 压缩
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml;
    gzip_min_length 1024;
}''')

    doc.add_page_break()

    # ============================
    # 第27章 分阶段开发计划
    # ============================
    add_heading(doc, '第27章 分阶段开发计划', level=1)

    add_heading(doc, '27.1 阶段总览', level=2)
    add_para(doc, '项目分为 5 个阶段迭代开发，每个阶段都有可演示的完整功能，确保增量交付和持续验证。')
    add_table(doc,
        ['阶段', '名称', '周期', '核心交付物'],
        [
            ['第一阶段', '基础框架与核心通路', '2 周', '项目骨架 + 上传解析 + 向量化 + 检索'],
            ['第二阶段', '版本管理与增量更新', '2 周', '版本控制 + Chunk Hash 增量 + 目录树'],
            ['第三阶段', '生产级能力', '3 周', '分片上传 + 断点续传 + 秒传 + 任务系统 + 一致性检查'],
            ['第四阶段', '对话验证与多 Provider', '2 周', 'RAG 对话 + 多 LLM Provider + 前端对话界面'],
            ['第五阶段', '完善与部署', '2 周', '前端完整功能 + Docker 部署 + 测试 + 文档'],
        ],
        col_widths=[1.2, 2.0, 1.0, 3.0]
    )

    add_heading(doc, '27.2 第一阶段：基础框架与核心通路（第1-2周）', level=2)
    add_bullet(doc, '目标：跑通文档上传 → 解析 → 分块 → 向量化 → 检索的完整链路')
    add_bullet(doc, '后端：FastAPI 项目初始化、uv 配置、Qdrant 集成、SQLAlchemy 模型')
    add_bullet(doc, '文件解析：PDF、TXT、Markdown 三种格式 Parser')
    add_bullet(doc, '分块：递归字符分块 + 固定大小分块')
    add_bullet(doc, 'Embedding：HuggingFace 本地推理（bge-m3）')
    add_bullet(doc, 'API：Collection CRUD、单文件上传、文档列表、语义检索')
    add_bullet(doc, '前端：项目初始化、Collection 管理、文档列表、检索测试页')
    add_bullet(doc, '验收：上传一份 PDF，能在检索页搜索到相关内容')

    add_heading(doc, '27.3 第二阶段：版本管理与增量更新（第3-4周）', level=2)
    add_bullet(doc, '目标：实现文档版本化管理和 Chunk 级增量更新')
    add_bullet(doc, 'DocumentVersion 模型：版本列表、版本详情、版本切换')
    add_bullet(doc, 'Chunk Hash 增量更新：content_hash 计算、差异对比、复用向量')
    add_bullet(doc, '目录树：Directory 模型、树形结构、目录 CRUD、拖拽移动')
    add_bullet(doc, 'DOCX 和 Excel 解析器补全')
    add_bullet(doc, 'PDF 文本清洗：页眉页脚移除、断行修复')
    add_bullet(doc, '前端：目录树组件、文档版本面板、Chunk 查看器')
    add_bullet(doc, '验收：上传同一文件两次（修改少量内容），验证增量更新生效，速度明显提升')

    add_heading(doc, '27.4 第三阶段：生产级能力（第5-7周）', level=2)
    add_bullet(doc, '目标：大文件支持、任务化处理、一致性保障')
    add_bullet(doc, '分片上传：初始化、分片上传、合并、秒传校验')
    add_bullet(doc, '断点续传：上传状态查询、续传、过期清理')
    add_bullet(doc, '秒传：SHA256 去重、文件复用')
    add_bullet(doc, '任务系统：APScheduler 集成、异步任务、进度追踪、任务列表')
    add_bullet(doc, 'Storage 抽象：本地存储实现、S3 兼容接口定义')
    add_bullet(doc, '一致性检查：检查任务、报告生成、自动修复')
    add_bullet(doc, '全量 Rebuild：Collection 重建、蓝绿切换')
    add_bullet(doc, '前端：上传组件（分片+进度+秒传）、任务中心、一致性检查页面')
    add_bullet(doc, '验收：上传 1GB 大文件，刷新页面后续传成功；一致性检查能发现并修复模拟的不一致')

    add_heading(doc, '27.5 第四阶段：对话验证与多 Provider（第8-9周）', level=2)
    add_bullet(doc, '目标：内置 RAG 对话验证，支持多种 LLM Provider 切换')
    add_bullet(doc, 'LLM Provider 框架：BaseLLMProvider + Factory')
    add_bullet(doc, 'DeepSeek / Qwen / Ark / Ollama 四个 Provider 实现')
    add_bullet(doc, 'RAG 对话服务：检索 + 重排 + 上下文组装 + 生成')
    add_bullet(doc, '流式输出：SSE 流式响应')
    add_bullet(doc, '会话管理：会话列表、历史消息、上下文管理')
    add_bullet(doc, '引用溯源：回答中关联 Chunk，展示相似度和原文')
    add_bullet(doc, '前端：对话页面（会话列表 + 消息流 + 引用面板）')
    add_bullet(doc, '验收：在对话页面提问，能得到基于知识库的回答，并展示引用来源')

    add_heading(doc, '27.6 第五阶段：完善与部署（第10-11周）', level=2)
    add_bullet(doc, '目标：功能完善、生产部署、质量保障')
    add_bullet(doc, 'Docker Compose 完整部署：backend + frontend + qdrant + redis')
    add_bullet(doc, '前端功能完善：设置页面、系统配置、模型管理、用户反馈')
    add_bullet(doc, '单元测试 + 集成测试：核心模块覆盖率 ≥ 70%')
    add_bullet(doc, '性能测试：并发上传、大文件上传、检索 QPS')
    add_bullet(doc, '安全加固：认证鉴权、接口限流、输入校验')
    add_bullet(doc, '部署文档 + 使用文档')
    add_bullet(doc, '验收：docker-compose up -d 一键启动，所有功能可用，通过生产级验收标准')

    doc.add_page_break()

    # ============================
    # 第28章 生产级验收标准
    # ============================
    add_heading(doc, '第28章 生产级验收标准', level=1)

    add_heading(doc, '28.1 功能验收', level=2)
    add_table(doc,
        ['类别', '验收项', '验收标准'],
        [
            ['Collection 管理', '创建/删除/列表/详情', 'CRUD 完整，Qdrant 同步创建 Collection'],
            ['目录管理', '多级目录、拖拽移动', '支持 N 级嵌套，移动后子节点同步更新路径'],
            ['文档上传', '小文件（<10MB）', '上传成功，自动解析分块向量化'],
            ['文档上传', '大文件（1GB）', '分片上传成功，支持中断后续传'],
            ['秒传', '相同文件二次上传', '1 秒内完成，复用已有数据'],
            ['版本管理', '多版本、版本切换', '版本列表正确，回滚后检索内容切换'],
            ['增量更新', '修改少量内容重新上传', '增量更新生效，耗时显著低于全量'],
            ['文件格式', 'PDF/DOCX/TXT/MD/XLSX', '五种格式都能正确解析和检索'],
            ['PDF 清洗', '页眉页脚、断行', '清洗后文本无重复页眉，段落连贯'],
            ['向量检索', '语义搜索', 'top10 结果语义相关，相似度排序正确'],
            ['过滤检索', '按目录/文档/标签过滤', '过滤条件生效，结果准确'],
            ['任务系统', '异步任务、进度展示', '任务后台执行，进度实时更新'],
            ['一致性检查', '检查+修复', '能检测并修复各类不一致'],
            ['全量 Rebuild', 'Collection 重建', '重建后数据完整，服务不中断'],
            ['对话验证', 'RAG 问答', '回答基于检索内容，引用溯源正确'],
            ['多 Provider', '切换 LLM Provider', '通过环境变量切换，对话正常'],
        ],
        col_widths=[1.2, 2.2, 3.5]
    )

    add_heading(doc, '28.2 性能验收', level=2)
    add_table(doc,
        ['指标', '验收标准', '测试条件'],
        [
            ['小文件上传处理', '< 10s', '10 页 PDF，5MB，bge-m3，CPU'],
            ['100MB PDF 解析', '< 60s', '含分块，不含向量化'],
            ['单 Chunk 向量化', '< 50ms', 'bge-m3，GPU（RTX 3090 级别）'],
            ['万级向量检索 P99', '< 50ms', '10 万 Point，top10，Cosine'],
            ['十万级向量检索 P99', '< 100ms', '100 万 Point，top10，HNSW'],
            ['并发上传', '支持 10 并发', '10 个文件同时上传，全部成功'],
            ['秒传响应时间', '< 1s', '相同文件二次上传'],
            ['对话首字延迟', '< 3s', '流式输出，从请求到首 token'],
        ],
        col_widths=[2.0, 2.0, 3.0]
    )

    add_heading(doc, '28.3 可靠性验收', level=2)
    add_bullet(doc, '服务重启：后端重启后，进行中的任务能恢复或标记失败，数据无丢失')
    add_bullet(doc, 'Qdrant 宕机：Qdrant 不可用时，后端友好报错，恢复后自动重连')
    add_bullet(doc, '上传中断：网络中断后再恢复，断点续传能正确继续并完成')
    add_bullet(doc, '重复上传：同一文件重复上传 10 次，数据库和向量库中只有 1 份有效数据')
    add_bullet(doc, '并发写入：多任务同时向同一 Collection 写入向量，无数据丢失或冲突')
    add_bullet(doc, '一致性校验：人为制造 3 类不一致，检查工具能全部发现并正确修复')
    add_bullet(doc, '7x24 稳定性：连续运行 7 天，无内存泄漏，无服务崩溃')

    add_heading(doc, '28.4 安全性验收', level=2)
    add_bullet(doc, '输入校验：所有 API 参数都有 Pydantic 校验，恶意输入被拦截')
    add_bullet(doc, '路径遍历：文件操作接口无路径遍历漏洞')
    add_bullet(doc, 'SQL 注入：使用 ORM 参数化查询，无注入风险')
    add_bullet(doc, 'XSS 防护：用户输入的文本在前端展示时正确转义')
    add_bullet(doc, '认证鉴权（可选）：启用认证后，未登录用户无法访问 API')
    add_bullet(doc, '限流（可选）：启用限流后，超出频率的请求被拒绝')
    add_bullet(doc, '敏感信息：配置文件中密钥不硬编码，通过环境变量注入')

    add_heading(doc, '28.5 可运维性验收', level=2)
    add_bullet(doc, '一键部署：docker-compose up -d 即可启动全部服务')
    add_bullet(doc, '健康检查：各服务提供 health 端点，可被监控系统采集')
    add_bullet(doc, '日志规范：结构化日志（JSON 格式可选），包含 request_id、时间、级别、模块')
    add_bullet(doc, '配置管理：所有可变参数通过环境变量配置，代码中无硬编码配置')
    add_bullet(doc, '数据备份：Qdrant 数据卷 + 数据库文件可直接备份和恢复')
    add_bullet(doc, '监控指标：提供 Collection 大小、文档数、任务成功率等统计接口')
    add_bullet(doc, '文档完善：API 文档（OpenAPI）、部署文档、使用文档齐全')

    # === 保存 ===
    output_path = r'f:\Qdrant-vector\Vector Data Manager 生产级向量数据库管理工具技术设计文档.docx'
    doc.save(output_path)
    print(f'文档已生成: {output_path}')


if __name__ == '__main__':
    main()
