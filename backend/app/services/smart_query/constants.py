"""
smart_query 模块的统一常量配置。

集中管理：停用词、正则模式、权重系数、阈值、枚举关键词等。
业务语义相关的配置（如 COLUMN_ALIASES / HIERARCHY_KEYWORDS）保留在 schema_provider.py，
因为它们本身就是"业务语义层"的配置，且已经集中管理。

使用说明：
- 纯通用、跨模块复用的常量 → 放这里
- 只在单个类内部使用、与该类逻辑强绑定的常量 → 保留在类内（类变量）
- 业务可配置项（可通过 settings 调整） → 优先走 settings，默认值放这里
"""

from __future__ import annotations

import re

# ===================================================================
# 1. 停用词（查询关键词提取时过滤）
# ===================================================================

# 中文查询停用词：疑问词、动词前缀、泛指词等
QUERY_STOPWORDS = {
    # 疑问词
    "什么", "多少", "哪些", "怎么", "如何", "是否", "有没有", "哪一个", "哪个",
    "谁", "哪儿", "哪里", "为什么", "怎么", "怎样", "几",
    # 动词前缀 / 请求词
    "查询", "统计", "计算", "查看", "给出", "显示", "请问", "帮我",
    "介绍", "一下", "看看", "说下", "说一下", "告诉我",
    # 泛指 / 限定词
    "每个", "各个", "所有", "全部", "分别", "各自", "分别是",
    "这个", "那个", "这些", "那些",
}

# 关键词检索器（keyword_retriever）用的停用词
# 与 QUERY_STOPWORDS 不同：偏语气助词 / 疑问语气 / 介词，
# 因为它是按标点切分后的"词"级别过滤，不是 2-6 字块级别
KEYWORD_RETRIEVER_STOPWORDS = {
    # 直接作为停用词过滤的完整词
    "full_words": {
        "多少", "几个", "什么", "怎么", "哪些", "哪里", "哪个",
        "有多少", "有哪些", "是什么", "怎么样", "为什么",
        "吗", "呢", "啊", "吧", "了", "的", "和", "与",
        "手下", "里面", "当中", "其中",
    },
    # 包含即过滤的疑问词片段
    "question_words": ["多少", "什么", "怎么", "哪些", "哪里", "哪个"],
}

# ===================================================================
# 2. 查询预处理相关
# ===================================================================

# 查询前缀清理模式（首尾装饰符/引导词）
QUERY_PREFIX_PATTERNS = [
    r"^[#\*\-—•·]+[\s:：]*",            # # 、 - 、 * 开头
    r"^[👉📌💡🔍📝❓⚠️✅🎯📊]+[\s:：]*",   # emoji 装饰
    r"^(?:问题|问|请问|查询|帮我查|你好|您好|请问一下|请问)[\s:：]+",
    r"^Q[\s:：]+",
    r"^q[\s:：]+",
]

# 中文关键词提取正则（2-6个中文字符的连续块）
CHINESE_KEYWORD_RE = r'[\u4e00-\u9fa5]{2,6}'

# ===================================================================
# 3. 表格排名（_rank_table_files）权重 & 实体识别
# ===================================================================

# 各匹配维度的权重系数
TABLE_RANK_WEIGHTS = {
    "file_name_match": 2.0,    # 文件名关键词匹配
    "column_name_match": 1.5,  # 列名关键词匹配
    "entity_type_bonus": 1.0,  # 语义实体类型匹配（作为基数，具体分值在下方配置）
    "original_order": 0.001,   # 原始顺序保底（越小越不影响主排序）
}

# 语义实体 → 对应表名关键词 + 加分数值
# 说明：
#   - "query_pattern": 匹配查询语句的正则（命中则认为查询涉及该类实体）
#   - "table_keywords": 命中后，给包含这些关键词的表名加 bonus 分
#   - "bonus": 加分值
# 新增业务类型时，在这里加一条即可，不需要改代码
ENTITY_TABLE_BOOSTS: list[dict] = [
    {
        "name": "person",
        "query_pattern": r"姓名|员工|人员|名字|谁是|叫什么|个人|本人",
        "table_keywords": ["员工", "人员", "人事", "职员"],
        "bonus": 30.0,
    },
    {
        "name": "department",
        "query_pattern": r"部门|科室|事业部|中心|小组",
        "table_keywords": ["员工", "部门", "组织", "人事"],
        "bonus": 25.0,
    },
    {
        "name": "sales",
        "query_pattern": r"销售|业绩|提成|绩效|金额|销量|订单|客户|营收",
        "table_keywords": ["销售", "绩效", "提成", "销货", "订单"],
        "bonus": 25.0,
    },
    {
        "name": "product",
        "query_pattern": r"产品|商品|物料|SKU|规格|货品",
        "table_keywords": ["产品", "商品", "物料", "货品"],
        "bonus": 20.0,
    },
    {
        "name": "finance",
        "query_pattern": r"财务|会计|应收|应付|成本|费用|利润|收入",
        "table_keywords": ["财务", "会计", "账目", "账单", "费用", "成本"],
        "bonus": 25.0,
    },
    {
        "name": "inventory",
        "query_pattern": r"库存|仓库|入库|出库|存货|盘点|库存数量",
        "table_keywords": ["库存", "仓库", "存货", "入库", "出库"],
        "bonus": 25.0,
    },
]

# ===================================================================
# 4. 跨表语义差异检测（_detect_cross_table_diffs）
# ===================================================================

# 明显不是实体名的值（常见枚举 / 布尔 / 性别 / 状态等），检测差异时跳过
NON_ENTITY_VALUES = {
    # 性别
    "男", "女", "male", "female", "M", "F",
    # 布尔 / 是否
    "是", "否", "有", "无", "真", "假", "Y", "N", "yes", "no", "true", "false",
    # 任务 / 流程状态
    "已完成", "未完成", "完成", "进行中", "待处理", "处理中", "已处理",
    "启用", "禁用", "正常", "异常", "有效", "无效", "激活", "未激活",
    "已审核", "未审核", "审核中", "草稿", "已提交", "已驳回",
}

# 明显不是实体名所在列的列名模式（整列跳过）
NON_ENTITY_COL_PATTERNS = [
    r"^id$|^编号$|^序号$|^no\.?$",          # 纯编号列
    r"日期|时间|时间戳|date|time",           # 时间列
    r"状态|status|state|type|类型|类别|级别",  # 枚举分类列
    r"数量|金额|价格|单价|合计|总计|平均|比例|百分比|率",  # 数值列
]

# 实体名长度范围
ENTITY_VALUE_MIN_LEN = 2
ENTITY_VALUE_MAX_LEN = 30

# 差异检测最多展示多少处（避免上下文过长）
MAX_CROSS_TABLE_DIFF_ITEMS = 10

# ===================================================================
# 5. 多子任务执行参数
# ===================================================================

# 默认查表数量上限（每个子任务初始分析多少张表）
# 准确性优先，宁可多查也不漏；语义类型驱动选表会在此基础上进一步扩展
DEFAULT_MAX_TABLES_PER_SUBQUERY = 15

# ===================================================================
# 6. LLM 调用参数（按场景）
# ===================================================================
# smart_query 模块各场景的 LLM 参数默认值。
# 优先级：settings 中的场景配置 > 此处默认值 > 全局 llm_temperature / llm_max_tokens
#
# 场景列表：
#   - router:       路由分析（识别查询意图、生成查询计划）
#   - sql_generate: SQL 生成
#   - sql_repair:   SQL 修复（报错后重试）
#   - synthesis:    结果合成（最终答案生成）
#   - decompose:    查询拆解（多意图分解）
#   - retrieval:    检索问答（纯向量 / 混合检索的答案生成）
#   - followup:     追问生成 / 澄清问题
# ===================================================================

LLM_SCENE_DEFAULTS: dict[str, dict] = {
    "router": {
        "temperature": 0.1,
        "max_tokens": 2048,
        "use_rewrite_model": True,
    },
    "sql_generate": {
        "temperature": 0.0,
        "max_tokens": 1500,
        "use_rewrite_model": True,
    },
    "sql_repair": {
        "temperature": 0.0,
        "max_tokens": 1500,
        "use_rewrite_model": True,
    },
    "synthesis": {
        "temperature": 0.3,
        "max_tokens": 4096,
        "use_chat_model": True,
    },
    "synthesis_multi": {
        "temperature": 0.3,
        "max_tokens": 4096,
        "use_chat_model": True,
    },
    "synthesis_multi_sql": {
        "temperature": 0.3,
        "max_tokens": 2048,
        "use_chat_model": True,
    },
    "synthesis_multi_hybrid": {
        "temperature": 0.5,
        "max_tokens": 2048,
        "use_chat_model": True,
    },
    "synthesis_sql": {
        "temperature": 0.3,
        "max_tokens": 1200,
        "use_chat_model": True,
    },
    "synthesis_semantic": {
        "temperature": 0.7,
        "max_tokens": 2048,
        "use_chat_model": True,
    },
    "synthesis_hybrid": {
        "temperature": 0.5,
        "max_tokens": 2048,
        "use_chat_model": True,
    },
    "decompose": {
        "temperature": 0.1,
        "max_tokens": 2048,
        "use_rewrite_model": True,
    },
    "retrieval": {
        "temperature": 0.5,
        "max_tokens": 2048,
        "use_chat_model": True,
    },
    "followup": {
        "temperature": 0.7,
        "max_tokens": 1200,
        "use_chat_model": True,
    },
}


def get_llm_config(scene: str, settings=None) -> dict:
    """
    获取指定场景的 LLM 配置。

    合并优先级：settings 场景覆盖 > constants 默认值 > 全局默认值

    Args:
        scene: 场景名称（router / sql_generate / synthesis 等）
        settings: 应用 Settings 对象，可选

    Returns:
        {"temperature": float, "max_tokens": int, "provider": str, "model": str}
    """
    defaults = LLM_SCENE_DEFAULTS.get(scene, {})
    result = {
        "temperature": defaults.get("temperature", 0.7),
        "max_tokens": defaults.get("max_tokens", 2048),
        "provider": None,
        "model": None,
    }

    if settings is None:
        return result

    # 全局默认值
    global_provider = getattr(settings, "llm_provider", "ollama")
    global_model = getattr(settings, "llm_model", None)

    # 场景级模型选择
    if defaults.get("use_rewrite_model"):
        provider = getattr(settings, "rewrite_llm_provider", None) or global_provider
        model = getattr(settings, "rewrite_llm_model", None) or global_model
    elif defaults.get("use_chat_model"):
        provider = getattr(settings, "chat_llm_provider", None) or global_provider
        model = getattr(settings, "chat_llm_model", None) or global_model
    else:
        provider = global_provider
        model = global_model

    result["provider"] = provider
    result["model"] = model

    # 支持 settings 中通过 smart_query_llm_{scene}_{param} 形式的细粒度覆盖
    scene_temp = getattr(settings, f"smart_query_llm_{scene}_temperature", None)
    if scene_temp is not None:
        result["temperature"] = scene_temp

    scene_tokens = getattr(settings, f"smart_query_llm_{scene}_max_tokens", None)
    if scene_tokens is not None:
        result["max_tokens"] = scene_tokens

    return result


# ===================================================================
# 8. 检索与展示阈值
# ===================================================================

# 默认 top_k（各检索器的默认返回条数）
DEFAULT_TOP_K = 20

# 路由分析建议的 top_k
SUGGESTED_TOP_K = 10

# SQL 结果展示上限（Markdown 表格最多展示多少行）
DEFAULT_MAX_ROWS = 50
SQL_SUMMARY_MAX_ROWS = 20   # 合成摘要里的表格行数
SQL_HYBRID_MAX_ROWS = 15    # 混合检索里的 SQL 表格行数

# 向量检索结果展示上限（最多取多少个 chunk 进上下文）
DEFAULT_MAX_CHUNKS = 5
RETRIEVAL_MAX_CHUNKS = 3    # 混合检索里的向量 chunk 数

# 重试次数
DEFAULT_MAX_RETRIES = 2     # SQL 生成 / 路由分析的默认重试次数

# 缓存大小
MAX_CACHED_TABLES = 50      # SchemaProvider 表缓存上限
PLAN_CACHE_MAX = 512        # RouterAgent 计划缓存上限

# 日志 / 摘要展示条数
BRIEF_SQL_HITS_LIMIT = 3    # 日志里 SQL 命中样例条数
SQL_DETAIL_MAX_K_AGG = 50   # 聚合类：明细只取前K条做展示
SQL_DETAIL_MAX_K_DEFAULT = 100  # 默认：明细取前K条


# ===================================================================
# 9. 编译好的正则（避免每次都重新 compile）
# ===================================================================
_non_entity_col_re: re.Pattern | None = None
_entity_value_num_re: re.Pattern | None = None
_entity_value_date_re: re.Pattern | None = None
_chinese_keyword_compiled: re.Pattern | None = None


def get_non_entity_col_re() -> re.Pattern:
    global _non_entity_col_re
    if _non_entity_col_re is None:
        _non_entity_col_re = re.compile(
            "|".join(NON_ENTITY_COL_PATTERNS), re.IGNORECASE
        )
    return _non_entity_col_re


def get_entity_value_num_re() -> re.Pattern:
    global _entity_value_num_re
    if _entity_value_num_re is None:
        _entity_value_num_re = re.compile(r"^[\d\.\-\+%,]+$")
    return _entity_value_num_re


def get_entity_value_date_re() -> re.Pattern:
    global _entity_value_date_re
    if _entity_value_date_re is None:
        _entity_value_date_re = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}.*")
    return _entity_value_date_re


def get_chinese_keyword_re() -> re.Pattern:
    global _chinese_keyword_compiled
    if _chinese_keyword_compiled is None:
        _chinese_keyword_compiled = re.compile(CHINESE_KEYWORD_RE)
    return _chinese_keyword_compiled
