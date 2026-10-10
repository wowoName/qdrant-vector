"""
应用配置管理
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 基础配置 ----------
    app_name: str = "Vector Data Manager"
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    secret_key: str = "dev-secret-key"
    cors_origins: str = "*"
    workers: int = 1
    # 应用时区：所有时间戳按此时区生成与展示（默认北京时间）
    app_timezone: str = "Asia/Shanghai"

    # ---------- 主数据库配置（业务元数据：集合/文档/任务/上传 等） ----------
    # 统一使用 PostgreSQL（不再支持 SQLite）
    db_driver: str = "postgresql+asyncpg"
    db_host: str | None = None
    db_port: int | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    # 数据库连接池大小
    db_pool_size: int = 20
    # 数据库连接池最大溢出连接数
    db_max_overflow: int = 10
    # 连接池中连接的回收时间（秒），防止连接长时间闲置后失效
    db_pool_recycle: int = 3600

    # ---------- 对话数据库配置（对话记录：会话/消息/摘要） ----------
    # 不配置则使用主库（默认行为），配置后对话数据独立存储
    chat_db_enabled: bool = False
    chat_db_driver: str = "postgresql+asyncpg"
    chat_db_host: str | None = None
    chat_db_port: int | None = None
    chat_db_user: str | None = None
    chat_db_password: str | None = None
    chat_db_name: str | None = None
    chat_db_pool_size: int = 10
    chat_db_max_overflow: int = 5
    chat_db_pool_recycle: int = 3600

    # ---------- Qdrant 配置 ----------
    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_api_key: str | None = None
    qdrant_use_grpc: bool = False
    qdrant_collection_prefix: str = "vdm_"
    # 启动时是否自动将数据库中的已完成文档同步到 Qdrant
    #  - if_missing: 仅当 Qdrant 中对应 Collection 不存在时才同步（默认，安全）
    #  - always:     每次启动都检查并同步缺失的向量（增量）
    #  - rebuild:    每次启动都重建所有向量（全量，谨慎使用）
    #  - disabled:   不自动同步
    qdrant_sync_on_start: str = "if_missing"

    # ---------- 存储配置 ----------
    storage_backend: str = "local"
    storage_root: str = "./data/storage"
    # 可分配配额上限占磁盘总容量的比例（默认 0.7 = 70%）
    # 用于：租户配额分配封顶、后台改配额超售校验、磁盘管理页展示的"可分配上限"
    storage_allocatable_ratio: float = 0.7

    # S3 配置（可选）
    s3_endpoint: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "vdm-storage"
    s3_secure: bool = True

    # ---------- 上传配置 ----------
    upload_chunk_size: int = 5 * 1024 * 1024  # 5MB
    upload_max_file_size: int = 10 * 1024 * 1024 * 1024  # 10GB
    upload_expire_hours: int = 24
    upload_concurrent_chunks: int = 3

    # ---------- Embedding 配置 ----------
    embedding_model: str = "bge-small-zh-v1.5"
    embedding_device: str = "auto"
    embedding_batch_size: int = 32
    embedding_cache_dir: str = "./data/models"
    embedding_normalize: bool = True
    # 离线模式：强制从本地加载，不访问 HuggingFace
    embedding_offline: bool = False
    # HuggingFace 镜像源（如 hf-mirror.com，留空则使用官方）
    hf_endpoint: str | None = None

    # ---------- Chunk 配置 ----------
    chunk_strategy: str = "recursive"
    chunk_size: int = 500
    chunk_overlap: int = 50
    chunk_min_size: int = 50
    # 语义切片：相似度阈值（低于此值视为语义边界）
    chunk_similarity_threshold: float = 0.65
    # 父子块：父块大小与重叠
    chunk_parent_size: int = 1500
    chunk_parent_overlap: int = 200

    # 表格专用切片配置（TabularChunker / Record 模式）
    # 模式: record（记录模式，一行一个，精度最高但最慢）
    #      row（行级，一行一个chunk）/ row_group（行组，N行一个chunk，速度快）
    #      column_group（列组） / sql（自定义SQL）
    tabular_chunk_mode: str = "row_group"
    # 行组模式下每组行数，None 表示根据 chunk_size 自动估算
    # 设为固定值可以获得更稳定的 chunk 数量和处理速度
    tabular_rows_per_chunk: int = 10
    # 列组模式下每组列数
    tabular_columns_per_group: int = 10
    # 是否在每个 chunk 中包含表头信息（推荐开启，增强检索语义）
    tabular_include_headers: bool = True
    # 表格文件是否自动使用 tabular 切片器（否则使用通用文本切片器）
    tabular_auto_for_spreadsheets: bool = True

    # ---------- 按文件类型的专属切片配置 ----------
    # 每种文件类型可以独立指定：strategy / size / overlap / min_size
    # 未指定的字段会回退到全局 chunk_* 配置
    # 格式：{ "file_type": { "strategy": "...", "size": N, "overlap": N, "min_size": N } }
    #
    # 设计理念：
    # - Excel/CSV：语义单元是"行"，按行切，不受字符数限制
    # - PDF/DOCX：语义单元是"段落"，按结构切，字符数较大
    # - Markdown：语义单元是"标题块"，按 markdown 结构切
    # - 普通文本：按字符数切
    per_type_chunk_config: dict = {
        # ---- 表格类：record 模式，一行一条结构化记录（支持 SmartQuery 精确查询）----
        "xlsx": {
            "strategy": "tabular",
            "mode": "record",  # record / row / row_group / column_group / sql
            "rows_per_chunk": 10,  # row_group 模式下每组行数
            "include_headers": True,
        },
        "xls": {
            "strategy": "tabular",
            "mode": "record",
            "rows_per_chunk": 10,
            "include_headers": True,
        },
        "csv": {
            "strategy": "tabular",
            "mode": "record",
            "rows_per_chunk": 10,
            "include_headers": True,
        },
        # ---- 富文本类：按结构切，字符数较大 ----
        "pdf": {
            "strategy": "recursive",
            "size": 800,
            "overlap": 80,
            "min_size": 100,
        },
        "docx": {
            "strategy": "recursive",
            "size": 800,
            "overlap": 80,
            "min_size": 100,
        },
        "doc": {
            "strategy": "recursive",
            "size": 800,
            "overlap": 80,
            "min_size": 100,
        },
        "pptx": {
            "strategy": "recursive",
            "size": 600,
            "overlap": 50,
            "min_size": 50,
        },
        "ppt": {
            "strategy": "recursive",
            "size": 600,
            "overlap": 50,
            "min_size": 50,
        },
        # ---- 轻量文本类：按标题结构切 ----
        "md": {
            "strategy": "recursive",
            "size": 600,
            "overlap": 60,
            "min_size": 80,
        },
        "markdown": {
            "strategy": "recursive",
            "size": 600,
            "overlap": 60,
            "min_size": 80,
        },
        "html": {
            "strategy": "recursive",
            "size": 600,
            "overlap": 60,
            "min_size": 80,
        },
        "htm": {
            "strategy": "recursive",
            "size": 600,
            "overlap": 60,
            "min_size": 80,
        },
        # ---- 纯文本：默认全局配置（无需特殊指定） ----
        # "txt": {}  # 不配置则使用全局默认
        #
        # ---- 多媒体类：OCR/ASR 输出的文本，按句子切 ----
        "png": {
            "strategy": "recursive",
            "size": 400,
            "overlap": 40,
            "min_size": 30,
        },
        "jpg": {
            "strategy": "recursive",
            "size": 400,
            "overlap": 40,
            "min_size": 30,
        },
        "jpeg": {
            "strategy": "recursive",
            "size": 400,
            "overlap": 40,
            "min_size": 30,
        },
        "mp3": {
            "strategy": "recursive",
            "size": 500,
            "overlap": 50,
            "min_size": 50,
        },
        "wav": {
            "strategy": "recursive",
            "size": 500,
            "overlap": 50,
            "min_size": 50,
        },
    }

    # ---------- 大文件处理配置 ----------
    # 超过此字符数启用 chunk 级流式处理（默认 10万字符 ≈ 200KB 纯文本）
    streaming_threshold_chars: int = 100_000
    # 流式处理时每批处理的 chunk 数（同时是 DB 写入和向量化的批次）
    streaming_batch_size: int = 100
    # Qdrant 每批写入的 point 数（避免单次请求 payload 过大）
    qdrant_upsert_batch_size: int = 500
    # 语义切片：最大参与 embedding 的句子数（超过则采样）
    chunk_max_sentences_for_embedding: int = 2000
    # 超过此页数启用页级流式处理（内存最优模式）
    page_streaming_threshold_pages: int = 100
    # 超过此文件大小(MB)启用页级流式处理
    page_streaming_file_size_mb: int = 5

    # ---------- 视频解析配置 ----------
    video_max_frames: int = 30  # 最多抽取多少帧做 OCR

    # ---------- 音频/ASR 配置 ----------
    asr_model: str = "small"  # Whisper 模型大小: tiny/base/small/medium/large
    asr_language: str | None = None  # 语言，None 表示自动检测
    asr_device: str = "auto"  # auto/cpu/cuda

    # ---------- LLM 配置 ----------
    # 全局默认 Provider 和模型
    llm_provider: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048
    llm_timeout: int = 120

    # 按场景独立配置模型（未设置则使用全局默认）
    # 对话场景
    chat_llm_provider: str | None = None
    chat_llm_model: str | None = None
    # 查询改写场景
    rewrite_llm_provider: str | None = None
    rewrite_llm_model: str | None = None
    # 摘要生成场景
    summary_llm_provider: str | None = None
    summary_llm_model: str | None = None

    # DeepSeek
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # Qwen (阿里通义千问)
    dashscope_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 火山方舟 (Ark)
    ark_api_key: str | None = None
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_model_endpoint: str | None = None

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # ---------- 任务配置 ----------
    task_max_workers: int = 4
    task_max_retries: int = 3
    task_retry_delay: int = 30

    # ---------- 认证 / SaaS 配置 ----------
    # JWT 配置
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7
    # 租户默认磁盘配额（MB）：新租户注册时的默认可用空间，超管可按租户调整
    tenant_default_quota_mb: int = 10
    # 新注册租户的试用有效期（天）：自注册租户的过期时间 = 注册时间 + 该天数
    tenant_trial_days: int = 7
    # 邮箱验证码有效期（分钟）
    email_code_expire_minutes: int = 10
    # 邮件验证码发送间隔（秒，防刷）
    email_code_resend_seconds: int = 60
    # SMTP 邮件配置（不配置时：开发模式下验证码写入日志并随接口返回，方便测试）
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # 发件人，默认使用 smtp_user
    smtp_use_ssl: bool = True
    smtp_use_tls: bool = False

    # ---------- 日志配置 ----------
    log_level: str = "INFO"
    log_file: str = "./data/logs/vdm.log"
    log_max_bytes: int = 10 * 1024 * 1024  # 10MB
    log_backup_count: int = 5

    # ---------- Redis 配置（热存储 / 缓存） ----------
    redis_enabled: bool = True
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    redis_key_prefix: str = "vdm:"
    # 连接池
    redis_max_connections: int = 20
    # 会话热缓存 TTL（秒）：超过此时间未访问的会话从 Redis 淘汰（DB 仍保留）
    redis_session_ttl: int = 24 * 3600  # 24 小时

    # ---------- 对话 / 上下文配置 ----------
    # 上下文窗口总 token 预算（需小于模型实际 context window）
    chat_context_window_tokens: int = 8000
    # 系统提示 + 检索上下文 预留 token 预算
    chat_context_reserve_tokens: int = 3000
    # 历史消息可用 token 预算 = window - reserve
    # 近期完整消息保留轮数（滑动窗口，user+assistant 算 1 轮）
    chat_recent_turns: int = 6
    # 中期消息保留轮数（会被一句摘要压缩）
    chat_mid_turns: int = 10
    # 滚动摘要触发阈值：当历史 token 超过预算的 80% 时触发压缩
    chat_compaction_threshold: float = 0.8
    # 工具结果最大 token（超过则截断）
    chat_tool_result_max_tokens: int = 500

    # ---------- SmartQuery 智能问数配置 ----------
    # 是否启用智能问数（多Agent + Text-to-SQL）
    smart_query_enabled: bool = True
    # SQL 查询的最低置信度阈值（低于此值不走向量检索优先）
    smart_query_sql_min_confidence: float = 0.5
    # 最多参与 SQL 查询的表格文件数
    smart_query_max_sql_files: int = 3
    # SQL 查询最大重试次数
    smart_query_sql_max_retries: int = 2
    # SQL 查询超时时间（秒）
    smart_query_sql_timeout: int = 30
    # 是否使用 LLM 做 SQL 生成（False 则只用规则生成，更快但能力有限）
    smart_query_llm_sql_generation: bool = True
    # 是否使用 LLM 做答案合成（False 则用规则模板生成，更快）
    smart_query_llm_synthesis: bool = True
    # SmartQuery 的 LLM Provider（None 则用全局默认）
    smart_query_llm_provider: str | None = None
    # SmartQuery 的 LLM 模型（None 则用全局默认）
    smart_query_llm_model: str | None = None

    # ---------- 流式输出配置 ----------
    # SSE 心跳间隔（秒）：前置阶段暂无内容时保活，防止网关/代理空闲断连
    stream_heartbeat_sec: int = 12
    # 合成阶段是否真流式（LLM delta 直透传，而非等整段生成完再一次性下发）
    smart_query_stream_synthesis: bool = True
    # 合成阶段是否注入对话历史（多轮上下文理解；关闭可回退到"无历史"行为）
    smart_query_use_history: bool = True
    # 是否启用分组聚合排名（GROUP_AGG）：支持"哪个X的Y最多/Top N"这类维度分组聚合问题
    # 关闭后相关问句回退到原有 TOP_N 行为（可快速回滚）
    smart_query_group_agg_enabled: bool = True
    # 是否由 LLM 主导查询规划：True=所有问句先由 LLM 产出计划（规则仅做校验/兜底）；
    # False=回退到"规则优先 + LLM 兜底"的旧行为
    smart_query_llm_first: bool = True
    # 是否缓存查询计划（相同问句 + 相同表结构复用，省一次 LLM 规划）
    smart_query_plan_cache_enabled: bool = True
    # 是否启用多轮结构化范围继承：追问未重新指定范围时，自动沿用上一轮已解析的过滤条件
    chat_scope_inheritance_enabled: bool = True
    # 是否启用"状态解析 LLM"：判定本轮 operation（inherit/modify/remove_filter/reset_query）
    # 关闭后仅用重置短语词典（规则）判定
    chat_state_resolver_enabled: bool = True
    # 是否启用"按需历史召回"：追问引用较早内容（继续/之前/刚才…）时，从最近窗口之外召回相关旧消息
    chat_history_recall_enabled: bool = True
    # 是否启用"跨会话长期用户记忆"：抽取并注入用户偏好/背景（记住/以后都/我们公司…）
    chat_user_memory_enabled: bool = True
    # 历史召回 / 长期记忆注入块的字符上限（与检索上下文共享 reserve 预算，避免撑爆上下文窗口）
    chat_extra_context_max_chars: int = 1500

    @property
    def database_url(self) -> str:
        """构造主数据库连接 URL（PostgreSQL）"""
        return (
            f"{self.db_driver}://"
            f"{self.db_user}:{self.db_password}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def chat_database_url(self) -> str | None:
        """构造对话数据库连接 URL，未启用则返回 None（使用主库）"""
        if not self.chat_db_enabled:
            return None
        return (
            f"{self.chat_db_driver}://"
            f"{self.chat_db_user}:{self.chat_db_password}@"
            f"{self.chat_db_host}:{self.chat_db_port}/{self.chat_db_name}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        """解析 CORS 来源列表"""
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()


def get_chunk_config(file_type: str | None = None) -> dict:
    """
    获取指定文件类型的切片配置，缺失字段回退到全局默认。

    Args:
        file_type: 文件类型（如 'xlsx', 'pdf'），None 则返回全局配置

    Returns:
        包含 strategy / size / overlap / min_size 等字段的字典
    """
    # 全局默认
    base = {
        "strategy": settings.chunk_strategy,
        "size": settings.chunk_size,
        "overlap": settings.chunk_overlap,
        "min_size": settings.chunk_min_size,
    }

    if not file_type:
        return base

    # 按文件类型覆盖
    per_type = settings.per_type_chunk_config or {}
    type_cfg = per_type.get(file_type.lower(), {})
    if not type_cfg:
        return base

    # 合并：type 配置覆盖全局
    result = {**base, **type_cfg}
    return result


def get_tabular_config(file_type: str | None = None) -> dict:
    """
    获取表格类文件的切片配置。

    返回 TabularChunker 需要的参数：mode / rows_per_chunk / columns_per_group / include_headers 等。
    """
    # 全局默认（从旧配置兼容）
    base = {
        "strategy": "tabular",
        "mode": getattr(settings, "tabular_chunk_mode", "row"),
        "rows_per_chunk": getattr(settings, "tabular_rows_per_chunk", None),
        "columns_per_group": getattr(settings, "tabular_columns_per_group", 10),
        "include_headers": getattr(settings, "tabular_include_headers", True),
    }

    if not file_type:
        return base

    per_type = settings.per_type_chunk_config or {}
    type_cfg = per_type.get(file_type.lower(), {})
    if not type_cfg:
        return base

    result = {**base, **type_cfg}
    return result
